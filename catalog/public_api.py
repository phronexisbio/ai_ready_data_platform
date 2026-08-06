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

import csv
import hashlib
import io
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from ftplib import FTP

import boto3
import requests
from botocore import UNSIGNED
from botocore.client import Config
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, UploadFile
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

# /upload-batch limits — per-file cap stays MAX_UPLOAD_BYTES (unchanged safety
# posture for the one write path the tunnel exposes), these bound the *batch*
# on top of that: how many files and how many total bytes one request can push.
MAX_BATCH_FILES = 20
MAX_BATCH_TOTAL_BYTES = 5 * 1024 * 1024  # 5MB total per batch request

# Raw-content preview cap — unlike the public-upload write path, files landed
# by scheduled connectors (chembl/pubchem/uniprot syncs) have no size ceiling,
# so this read path needs its own guard against being used to tunnel an
# arbitrarily large object out through the public API one preview at a time.
RAW_PREVIEW_MAX_BYTES = 200 * 1024

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

# Pull-by-source config — deliberately NOT a free-form "give me any URL"
# endpoint. Each source either hits one fixed, hardcoded host (geo/sra/ftp)
# or, for s3, only ever talks to real AWS with anonymous/unsigned
# credentials (never a user-suppliable endpoint_url) — so a site visitor can
# choose *what* to fetch but never *where from* in a way that could reach an
# internal network address. See connectors/{geo,sra,s3,ftp}_connector.py for
# the CLI/scheduled versions of the same fetch logic these mirror.
GEO_ACC_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}&targ=self&form=text&view=brief"
SRA_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
SRA_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
FTP_HOST = "ftp.ncbi.nlm.nih.gov"

GEO_ACCESSION_RE = re.compile(r"^G[A-Z]{2}\d{1,10}$")  # GSE12345, GSM123, GPL96, GDS858
SRA_ACCESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{3,20}$")
S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
FTP_PATH_RE = re.compile(r"^/[\x20-\x7e]{1,510}$")  # must start with "/", printable ASCII, no traversal-friendly chars needed to block since ftplib can't escape the connection


def _pull_dataset(source: str) -> str:
    return f"public-pull-{source}-{uuid.uuid4().hex[:12]}"


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


def _check_submission_rate_limit(ip: str) -> None:
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


def _file_dict(f: File, dataset_id: str, dataset_version: int) -> dict:
    return {
        "file_id": f.file_id,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "source": f.source,
        "modality": f.modality,
        "status": f.status,
        "status_detail": f.status_detail,
        "checksum": f.checksum,
        "location": f.location,
        "pipeline_version": f.pipeline_version,
        "created_at": f.created_at,
    }


@router.get("/datasets/{dataset_id}/files")
def dataset_files(dataset_id: str, version: int | None = None, db: Session = Depends(get_db)):
    """Per-file pipeline status (landed -> raw -> validated/rejected, then a
    Feature row once transform runs) for a dataset version. The manifest on
    GET /datasets/{id}/versions only lists filenames — this is what actually
    shows where each one currently sits in the pipeline, the same status
    field workflows/argo/ingest-validate-template.yaml's steps set."""
    q = select(Dataset).where(Dataset.dataset_id == dataset_id)
    if version is not None:
        q = q.where(Dataset.dataset_version == version)
    else:
        q = q.order_by(Dataset.dataset_version.desc())
    ds = db.execute(q).scalars().first()
    if ds is None:
        raise HTTPException(404, f"dataset {dataset_id} not found")
    files = db.execute(select(File).where(File.dataset_pk == ds.id).order_by(File.created_at)).scalars().all()
    return [_file_dict(f, ds.dataset_id, ds.dataset_version) for f in files]


@router.get("/files/{file_id}")
def get_file_detail(file_id: str, db: Session = Depends(get_db)):
    f = db.execute(select(File).where(File.file_id == file_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"file {file_id} not found")
    ds = db.execute(select(Dataset).where(Dataset.id == f.dataset_pk)).scalar_one()
    return _file_dict(f, ds.dataset_id, ds.dataset_version)


@router.get("/files/{file_id}/raw")
def get_file_raw(file_id: str, db: Session = Depends(get_db)):
    """Bounded raw-content preview of the file as it landed, decoded best-effort
    as UTF-8 (binary formats like .tiff just render as replacement characters —
    this is a text preview, not a viewer for every modality). Capped at
    RAW_PREVIEW_MAX_BYTES via an S3 Range request so this read path can't be
    used to pull an arbitrarily large connector-synced object through the
    tunnel — /upload's own size cap only bounds the public *write* path."""
    f = db.execute(select(File).where(File.file_id == file_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"file {file_id} not found")
    bucket, _, key = f.location.partition("/")
    try:
        obj = _minio_client().get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{RAW_PREVIEW_MAX_BYTES - 1}")
        content = obj["Body"].read()
    except Exception as e:
        raise HTTPException(502, f"could not fetch file content: {e}")
    content_range = obj.get("ContentRange", "")
    total_size = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else len(content)
    return {
        "file_id": file_id,
        "location": f.location,
        "modality": f.modality,
        "content": content.decode("utf-8", errors="replace"),
        "truncated": total_size > len(content),
        "total_size_bytes": total_size,
    }


@router.get("/features")
def search_features(
    dataset_id: str | None = None,
    source_file_id: str | None = None,
    modality: str | None = None,
    representation_type: str | None = None,
    quality_status: str | None = "passed",
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = select(Feature)
    if dataset_id:
        q = q.where(Feature.dataset_id == dataset_id)
    if source_file_id:
        q = q.where(Feature.source_file_id == source_file_id)
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


def _land_register_submit(*, db: Session, source: str, dataset_id: str, filename: str, content: bytes, modality: str) -> dict:
    """Shared by /upload and every /pull/{source} endpoint: land bytes into
    MinIO, register one Dataset + one File row, submit the ingest-validate
    workflow. Each call gets its own fresh dataset_id — these are independent
    submissions, not versions of one manifest, so reusing a dataset_id across
    them would misrepresent that relationship in the catalog."""
    manifest = {"files": [filename]}
    ds = Dataset(
        dataset_id=dataset_id,
        dataset_version=1,
        dataset_hash=dataset_hash(manifest),
        owner=source,
        source=source,
        manifest=manifest,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    key = f"{source}/{dataset_id}/{filename}"
    _minio_client().put_object(Bucket="landing", Key=key, Body=content)
    location = f"landing/{key}"

    f = File(
        dataset_pk=ds.id,
        source=source,
        checksum=hashlib.sha256(content).hexdigest(),
        modality=modality,
        location=location,
    )
    db.add(f)
    db.commit()
    db.refresh(f)

    workflow_name = _submit_workflow(
        file_id=f.file_id,
        source=source,
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


@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = UploadFileParam(...),
    db: Session = Depends(get_db),
):
    """Land a visitor-submitted file through the real ingest -> validate ->
    transform pipeline and return a workflow name the frontend can poll."""
    _check_submission_rate_limit(_client_ip(request))

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

    return _land_register_submit(
        db=db, source="public-upload", dataset_id=f"public-upload-{uuid.uuid4().hex[:12]}", filename=filename, content=content, modality=modality
    )


def _land_register_submit_batch(*, db: Session, source: str, dataset_id: str, items: list[tuple[str, bytes, str]]) -> dict:
    """Batch counterpart to _land_register_submit: one Dataset version covering
    every file in the batch (mirroring how connectors/base.py's Connector.run()
    registers one Dataset version + one File row per landed file for a single
    sync), rather than a separate Dataset per file the way single-file /upload
    does — a batch is genuinely one submission, not N independent ones."""
    manifest = {"files": [filename for filename, _, _ in items]}
    ds = Dataset(
        dataset_id=dataset_id,
        dataset_version=1,
        dataset_hash=dataset_hash(manifest),
        owner=source,
        source=source,
        manifest=manifest,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    minio = _minio_client()
    files_out = []
    for filename, content, modality in items:
        key = f"{source}/{dataset_id}/{filename}"
        minio.put_object(Bucket="landing", Key=key, Body=content)
        location = f"landing/{key}"

        f = File(
            dataset_pk=ds.id,
            source=source,
            checksum=hashlib.sha256(content).hexdigest(),
            modality=modality,
            location=location,
        )
        db.add(f)
        db.commit()
        db.refresh(f)

        workflow_name = _submit_workflow(
            file_id=f.file_id,
            source=source,
            dataset_id=dataset_id,
            dataset_version=1,
            modality=modality,
            location=location,
        )
        files_out.append(
            {"filename": filename, "file_id": f.file_id, "modality": modality, "workflow_name": workflow_name}
        )

    return {"dataset_id": dataset_id, "dataset_version": 1, "files": files_out}


@router.post("/upload-batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = UploadFileParam(...),
    db: Session = Depends(get_db),
):
    """Land multiple visitor-submitted files as one dataset in a single request,
    each through the real ingest -> validate -> transform pipeline. Counts as one
    submission against the rate limiter (like /pull/*) since charging a batch of
    N files N budget slots would defeat the point of having this endpoint."""
    _check_submission_rate_limit(_client_ip(request))

    if not files:
        raise HTTPException(400, "no files provided")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(400, f"too many files — max {MAX_BATCH_FILES} per batch")

    valid: list[tuple[str, bytes, str]] = []
    errors: list[dict] = []
    total_bytes = 0

    for file in files:
        filename = file.filename or "upload"
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        modality = MODALITY_BY_SUFFIX.get(suffix)
        if modality is None:
            errors.append({"filename": filename, "error": f"unsupported file type '{suffix}'"})
            continue

        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            errors.append({"filename": filename, "error": f"file too large — max {MAX_UPLOAD_BYTES // 1024}KB per file"})
            continue
        if not content.strip():
            errors.append({"filename": filename, "error": "empty file"})
            continue

        prospective_total = total_bytes + len(content)
        if prospective_total > MAX_BATCH_TOTAL_BYTES:
            errors.append(
                {"filename": filename, "error": f"batch total too large — max {MAX_BATCH_TOTAL_BYTES // (1024 * 1024)}MB per batch"}
            )
            continue
        total_bytes = prospective_total
        valid.append((filename, content, modality))

    if not valid:
        raise HTTPException(400, f"no valid files in batch: {errors}")

    result = _land_register_submit_batch(
        db=db,
        source="public-upload",
        dataset_id=f"public-upload-batch-{uuid.uuid4().hex[:12]}",
        items=valid,
    )
    result["errors"] = errors
    return result


def _write_csv_row(header: list[str], row: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _fetch_geo(accession: str) -> tuple[bytes, str]:
    resp = requests.get(GEO_ACC_URL.format(accession=accession), timeout=30)
    resp.raise_for_status()
    text_body = resp.content.decode("utf-8", errors="replace")

    fields: dict[str, list[str]] = {}
    for line in text_body.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.lstrip("^!#").strip()
        value = value.strip()
        if key:
            fields.setdefault(key, []).append(value)
    if not fields:
        raise HTTPException(422, f"no metadata found for GEO accession {accession}")

    header = list(fields.keys())
    row = ["; ".join(v for v in values if v) for values in fields.values()]
    return _write_csv_row(header, row), f"{accession}.csv"


def _fetch_sra(accession: str) -> tuple[bytes, str]:
    search_resp = requests.get(SRA_ESEARCH_URL, params={"db": "sra", "term": accession, "retmode": "json"}, timeout=30)
    search_resp.raise_for_status()
    id_list = search_resp.json()["esearchresult"]["idlist"]
    if not id_list:
        raise HTTPException(422, f"no SRA record found for accession {accession}")
    uid = id_list[0]

    summary_resp = requests.get(SRA_ESUMMARY_URL, params={"db": "sra", "id": uid, "retmode": "json"}, timeout=30)
    summary_resp.raise_for_status()
    record = summary_resp.json()["result"][uid]
    if "error" in record:
        raise HTTPException(422, f"SRA esummary error for {accession}: {record['error']}")

    header = ["accession", "uid"] + [k for k in record if k != "uid"]
    row = [accession, uid] + [str(record[k]) for k in header[2:]]
    return _write_csv_row(header, row), f"{accession}.csv"


def _fetch_s3(bucket: str, key: str) -> tuple[bytes, str]:
    client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.ClientError as e:
        raise HTTPException(422, f"could not fetch s3://{bucket}/{key}: {e}")
    if obj.get("ContentLength", 0) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"object too large — max {MAX_UPLOAD_BYTES // 1024}KB for public pulls")
    content = obj["Body"].read()
    return content, key.rsplit("/", 1)[-1]


def _fetch_ftp(path: str) -> tuple[bytes, str]:
    buf = io.BytesIO()

    def _write(chunk: bytes):
        if buf.tell() + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file too large — max {MAX_UPLOAD_BYTES // 1024}KB for public pulls")
        buf.write(chunk)

    ftp = FTP(FTP_HOST, timeout=30)
    try:
        ftp.login()
        ftp.retrbinary(f"RETR {path}", _write)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"could not fetch ftp://{FTP_HOST}{path}: {e}")
    finally:
        ftp.quit()
    return buf.getvalue(), path.rsplit("/", 1)[-1]


@router.post("/pull/geo")
def pull_geo(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    _check_submission_rate_limit(_client_ip(request))
    accession = str(body.get("accession", "")).strip().upper()
    if not GEO_ACCESSION_RE.match(accession):
        raise HTTPException(400, "accession must look like GSE12345, GSM123, GPL96, or GDS858")
    content, filename = _fetch_geo(accession)
    return _land_register_submit(db=db, source="geo", dataset_id=_pull_dataset("geo"), filename=filename, content=content, modality="tabular")


@router.post("/pull/sra")
def pull_sra(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    _check_submission_rate_limit(_client_ip(request))
    accession = str(body.get("accession", "")).strip().upper()
    if not SRA_ACCESSION_RE.match(accession):
        raise HTTPException(400, "accession must be 3-20 alphanumeric characters, e.g. SRR000001")
    content, filename = _fetch_sra(accession)
    return _land_register_submit(db=db, source="sra", dataset_id=_pull_dataset("sra"), filename=filename, content=content, modality="tabular")


@router.post("/pull/s3")
def pull_s3(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    _check_submission_rate_limit(_client_ip(request))
    bucket = str(body.get("bucket", "")).strip().lower()
    key = str(body.get("key", "")).strip()
    if not S3_BUCKET_RE.match(bucket):
        raise HTTPException(400, "invalid S3 bucket name")
    if not key or len(key) > 1024:
        raise HTTPException(400, "key required, max 1024 characters")
    content, filename = _fetch_s3(bucket, key)
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    modality = MODALITY_BY_SUFFIX.get(suffix, "text")  # anonymous public objects rarely have a recognized extension
    return _land_register_submit(db=db, source="s3", dataset_id=_pull_dataset("s3"), filename=filename, content=content, modality=modality)


@router.post("/pull/ftp")
def pull_ftp(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    _check_submission_rate_limit(_client_ip(request))
    path = str(body.get("path", "")).strip()
    if not FTP_PATH_RE.match(path):
        raise HTTPException(400, f"path must start with / and be a plain path on {FTP_HOST}")
    content, filename = _fetch_ftp(path)
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    modality = MODALITY_BY_SUFFIX.get(suffix, "text")
    return _land_register_submit(db=db, source="ftp", dataset_id=_pull_dataset("ftp"), filename=filename, content=content, modality=modality)


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
