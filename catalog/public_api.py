"""Public-facing read-only API — the only surface exposed through the
Cloudflare Tunnel for the engine.phronexis.bio frontend.

Deliberately separate from the internal CRUD endpoints in api.py: everything
here is read-only (no POST/PATCH), and protected by a shared-secret API key
(`X-API-Key` header, checked against the `PUBLIC_API_KEY` env var) so the
tunnel has exactly one thing to authenticate, not three. Aggregates across
Postgres, MinIO, and Prometheus internally (all reachable via in-cluster DNS
from here) so the tunnel only ever needs to expose this one service — the
frontend never talks to MinIO or Prometheus directly.
"""

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
import requests
from botocore.client import Config
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile
from fastapi import File as UploadFileParam
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from catalog.db import get_db
from catalog.hashing import dataset_hash
from catalog.models import Dataset, Feature, File

PUBLIC_API_KEY = os.environ.get("PUBLIC_API_KEY")
PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.data-platform.svc.cluster.local:9090"
)
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio.data-platform.svc.cluster.local:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
ARGO_NAMESPACE = os.environ.get("ARGO_NAMESPACE", "data-platform")

# Public-upload safety limits — this is the one write path the tunnel exposes
# (indirectly: only reachable through the Next.js server holding
# PUBLIC_API_KEY, never straight from a browser), so it gets its own,
# tighter guardrails on top of the shared API-key gate every /public/*
# endpoint already has.
MAX_UPLOAD_BYTES = 512 * 1024  # 512KB — a demo upload, not a bulk loader
UPLOAD_RATE_LIMIT_MAX = 5
UPLOAD_RATE_LIMIT_WINDOW_SECONDS = 600  # 5 uploads / 10 min / client

# Same shape as connectors/local_connector.py's suffix table — duplicated
# rather than imported because catalog and connectors ship as separate
# Docker images and neither depends on the other.
MODALITY_BY_SUFFIX = {
    ".fasta": "sequence",
    ".fa": "sequence",
    ".fna": "sequence",
    ".pdb": "structure",
    ".cif": "structure",
    ".mmcif": "structure",
    ".smi": "molecule",
    ".sdf": "molecule",
    ".mol2": "molecule",
    ".inchi": "molecule",
    ".tif": "image",
    ".tiff": "image",
    ".csv": "tabular",
    ".tsv": "tabular",
    ".json": "text",
    ".txt": "text",
}


def require_api_key(x_api_key: str | None = Header(default=None)):
    if not PUBLIC_API_KEY:
        raise HTTPException(500, "PUBLIC_API_KEY not configured on the server")
    if x_api_key != PUBLIC_API_KEY:
        raise HTTPException(401, "invalid or missing API key")


router = APIRouter(prefix="/public", dependencies=[Depends(require_api_key)])


def _minio_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


_upload_timestamps: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    # Cloudflare sets this on every request that reaches us through the
    # tunnel; request.client.host would otherwise just be cloudflared's
    # loopback connection to the catalog Service, not the real visitor.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


def _check_upload_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window_start = now - UPLOAD_RATE_LIMIT_WINDOW_SECONDS
    recent = [t for t in _upload_timestamps[ip] if t >= window_start]
    if len(recent) >= UPLOAD_RATE_LIMIT_MAX:
        raise HTTPException(429, "too many uploads from this client — try again later")
    recent.append(now)
    _upload_timestamps[ip] = recent


def _submit_workflow(*, file_id: str, source: str, dataset_id: str, dataset_version: int, modality: str, location: str) -> str:
    """Same ingest-validate WorkflowTemplate submission engine/ingestion_service.py
    does for connector events, via the Kubernetes API instead of shelling out to
    `kubectl create -f -` (this runs inside the catalog pod, not on a host with a
    kubeconfig) — see infra/k8s-manifests/catalog-rbac.yaml for the RBAC this needs.
    Called directly instead of going through NATS: the public-upload path wants the
    workflow running immediately for a live-feeling demo, and publishing the event
    too would make a later `engine.ingestion_service` drain double-submit it.
    """
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()  # local dev, outside the cluster

    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"generateName": "ingest-validate-", "namespace": ARGO_NAMESPACE},
        "spec": {
            "workflowTemplateRef": {"name": "ingest-validate"},
            "arguments": {
                "parameters": [
                    {"name": "file-id", "value": file_id},
                    {"name": "source", "value": source},
                    {"name": "dataset-id", "value": dataset_id},
                    {"name": "dataset-version", "value": str(dataset_version)},
                    {"name": "modality", "value": modality},
                    {"name": "location", "value": location},
                ]
            },
        },
    }
    api = k8s_client.CustomObjectsApi()
    try:
        result = api.create_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=ARGO_NAMESPACE,
            plural="workflows",
            body=workflow,
        )
    except k8s_client.exceptions.ApiException as e:
        raise HTTPException(502, f"could not submit workflow: {e.reason}")
    return result["metadata"]["name"]


def _feature_dict(f: Feature) -> dict:
    return {
        "feature_id": f.feature_id,
        "source_file_id": f.source_file_id,
        "dataset_id": f.dataset_id,
        "dataset_version": f.dataset_version,
        "modality": f.modality,
        "representation_type": f.representation_type,
        "pipeline_version": f.pipeline_version,
        "source_hash": f.source_hash,
        "location": f.location,
        "model_compatibility_tags": f.model_compatibility_tags,
        "quality_status": f.quality_status,
        "quality_checks_passed": f.quality_checks_passed,
        "quality_detail": f.quality_detail,
        "created_at": f.created_at,
    }


@router.get("/datasets")
def list_datasets(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    """Latest version of each distinct dataset_id, most recent first."""
    subq = (
        select(Dataset.dataset_id, func.max(Dataset.dataset_version).label("max_version"))
        .group_by(Dataset.dataset_id)
        .subquery()
    )
    q = (
        select(Dataset)
        .join(subq, (Dataset.dataset_id == subq.c.dataset_id) & (Dataset.dataset_version == subq.c.max_version))
        .order_by(Dataset.created_at.desc())
        .limit(limit)
    )
    rows = db.execute(q).scalars().all()
    return [
        {
            "dataset_id": d.dataset_id,
            "dataset_version": d.dataset_version,
            "owner": d.owner,
            "source": d.source,
            "manifest": d.manifest,
            "created_at": d.created_at,
        }
        for d in rows
    ]


@router.get("/datasets/{dataset_id}/versions")
def dataset_versions(dataset_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        select(Dataset).where(Dataset.dataset_id == dataset_id).order_by(Dataset.dataset_version)
    ).scalars().all()
    if not rows:
        raise HTTPException(404, f"dataset {dataset_id} not found")
    return [
        {
            "dataset_id": d.dataset_id,
            "dataset_version": d.dataset_version,
            "dataset_hash": d.dataset_hash,
            "owner": d.owner,
            "source": d.source,
            "manifest": d.manifest,
            "created_at": d.created_at,
        }
        for d in rows
    ]


@router.get("/features")
def search_features(
    dataset_id: str | None = None,
    modality: str | None = None,
    representation_type: str | None = None,
    quality_status: str | None = "passed",
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = select(Feature)
    if dataset_id:
        q = q.where(Feature.dataset_id == dataset_id)
    if modality:
        q = q.where(Feature.modality == modality)
    if representation_type:
        q = q.where(Feature.representation_type == representation_type)
    if quality_status and quality_status != "any":
        q = q.where(Feature.quality_status == quality_status)
    q = q.order_by(Feature.created_at.desc()).limit(limit)
    return [_feature_dict(f) for f in db.execute(q).scalars().all()]


@router.get("/features/{feature_id}")
def get_feature_detail(feature_id: str, db: Session = Depends(get_db)):
    f = db.execute(select(Feature).where(Feature.feature_id == feature_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"feature {feature_id} not found")
    return _feature_dict(f)


@router.get("/features/{feature_id}/tensor")
def get_feature_tensor(feature_id: str, db: Session = Depends(get_db)):
    f = db.execute(select(Feature).where(Feature.feature_id == feature_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"feature {feature_id} not found")
    bucket, _, key = f.location.partition("/")
    try:
        obj = _minio_client().get_object(Bucket=bucket, Key=key)
        content = obj["Body"].read()
    except Exception as e:
        raise HTTPException(502, f"could not fetch tensor: {e}")
    return json.loads(content)


@router.get("/sources")
def source_sync_status(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (source) source, dataset_id, dataset_version, created_at
            FROM datasets
            ORDER BY source, created_at DESC
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = UploadFileParam(...),
    db: Session = Depends(get_db),
):
    """Land a visitor-submitted file through the real ingest -> validate ->
    transform pipeline and return a workflow name the frontend can poll.

    Every dataset registered here gets its own fresh `dataset_id`
    (`public-upload-<uuid>`) rather than sharing one across uploads — these
    are independent, unrelated submissions, not versions of the same
    manifest, so reusing one dataset_id would misrepresent that relationship
    in the catalog.
    """
    _check_upload_rate_limit(_client_ip(request))

    filename = file.filename or "upload"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    modality = MODALITY_BY_SUFFIX.get(suffix)
    if modality is None:
        raise HTTPException(
            400,
            f"unsupported file type '{suffix}' — accepted: {', '.join(sorted(MODALITY_BY_SUFFIX))}",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large — max {MAX_UPLOAD_BYTES // 1024}KB for public uploads")
    if not content.strip():
        raise HTTPException(400, "empty file")

    dataset_id = f"public-upload-{uuid.uuid4().hex[:12]}"
    manifest = {"files": [filename]}
    ds = Dataset(
        dataset_id=dataset_id,
        dataset_version=1,
        dataset_hash=dataset_hash(manifest),
        owner="public-upload",
        source="public-upload",
        manifest=manifest,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    key = f"public-upload/{dataset_id}/{filename}"
    _minio_client().put_object(Bucket="landing", Key=key, Body=content)
    location = f"landing/{key}"

    f = File(
        dataset_pk=ds.id,
        source="public-upload",
        checksum=hashlib.sha256(content).hexdigest(),
        modality=modality,
        location=location,
    )
    db.add(f)
    db.commit()
    db.refresh(f)

    workflow_name = _submit_workflow(
        file_id=f.file_id,
        source="public-upload",
        dataset_id=dataset_id,
        dataset_version=1,
        modality=modality,
        location=location,
    )

    return {
        "file_id": f.file_id,
        "dataset_id": dataset_id,
        "dataset_version": 1,
        "modality": modality,
        "workflow_name": workflow_name,
    }


@router.get("/uploads/{file_id}/status")
def upload_status(file_id: str, db: Session = Depends(get_db)):
    """Poll target for the frontend's live upload view. `File.status`
    (landed -> raw -> validated/rejected, set by workflows/argo/ingest-validate-template.yaml's
    ingest/validate steps) already carries the pipeline stage — no need to
    also parse Argo's own workflow phase for that. Once a Feature row shows
    up for this file, transform has produced (or rejected) the final output."""
    f = db.execute(select(File).where(File.file_id == file_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"file {file_id} not found")
    features = db.execute(select(Feature).where(Feature.source_file_id == file_id)).scalars().all()
    return {
        "file_id": f.file_id,
        "status": f.status,
        "status_detail": f.status_detail,
        "modality": f.modality,
        "features": [_feature_dict(feat) for feat in features],
    }


def _prometheus_query(promql: str) -> list[dict]:
    """Best-effort: an observability blip shouldn't 500 the whole page."""
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=10)
        resp.raise_for_status()
        return resp.json()["data"]["result"]
    except Exception:
        return []


@router.get("/stats/pipeline-phases")
def pipeline_phases():
    results = _prometheus_query("sum by (phase) (argo_workflows_total_count)")
    return {r["metric"].get("phase", "unknown"): r["value"][1] for r in results}


@router.get("/stats/queue-depth")
def queue_depth():
    results = _prometheus_query('nats_consumer_num_pending{consumer_name="ingestion-service"}')
    return {"pending": results[0]["value"][1] if results else "0"}


@router.get("/stats/cache-hit-rate")
def cache_hit_rate(hours: int = 24, db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        text(
            """
            SELECT
                date_trunc('hour', created_at) AS time,
                100.0 * count(*) FILTER (WHERE quality_checks_passed::jsonb ? 'cache_hit') / NULLIF(count(*), 0) AS cache_hit_rate_pct,
                count(*) AS total
            FROM features
            WHERE created_at >= :since
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"since": since},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/stats/rejected-files")
def rejected_files(limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    rows = db.execute(
        select(File).where(File.status == "rejected").order_by(File.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "file_id": f.file_id,
            "source": f.source,
            "modality": f.modality,
            "location": f.location,
            "status_detail": f.status_detail,
            "created_at": f.created_at,
        }
        for f in rows
    ]
