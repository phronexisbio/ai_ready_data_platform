"""Metadata catalog service — BUILD_PLAN.md §7.

The source of truth every other platform component (connectors, engine,
feature repository) queries instead of inspecting files directly.
"""

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from catalog import schemas
from catalog.db import engine, get_db
from catalog.hashing import dataset_hash
from catalog.models import Base, Dataset, Feature, File, Job, Pipeline
from catalog.public_api import router as public_router

# Phase 13 (BUILD_PLAN_COMMERCIAL.md): these internal endpoints previously had
# no authentication at all — anything on the cluster network could read/write
# every table. This isn't full per-service identity, just a shared secret
# every internal caller (connectors, engine steps, via CatalogClient) now
# presents — closes "any pod, no exceptions" down to "any pod holding the
# secret," which is a real reduction in blast radius on its own.
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET")


def require_internal_secret(x_internal_secret: str | None = Header(default=None)):
    if not INTERNAL_API_SECRET:
        raise HTTPException(500, "INTERNAL_API_SECRET not configured on the server")
    if not x_internal_secret or not hmac.compare_digest(x_internal_secret, INTERNAL_API_SECRET):
        raise HTTPException(401, "invalid or missing internal secret")


internal_router = APIRouter(dependencies=[Depends(require_internal_secret)])


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="AI-Ready Data Platform — Metadata Catalog", lifespan=lifespan)
app.include_router(public_router)
# internal_router is included at the bottom of this file, not here — its
# routes are only added to it by the @internal_router... decorators further
# down, and FastAPI's include_router() copies whatever routes exist on the
# router at call time, not a live reference. Including it this early would
# silently register zero of the internal endpoints.


@app.get("/health")
def health():
    return {"status": "ok"}


def _get_dataset(db: Session, dataset_id: str, dataset_version: int) -> Dataset:
    ds = db.execute(
        select(Dataset).where(Dataset.dataset_id == dataset_id, Dataset.dataset_version == dataset_version)
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(404, f"dataset {dataset_id} version {dataset_version} not found")
    return ds


@internal_router.post("/datasets", response_model=schemas.DatasetOut, status_code=201)
def create_dataset(payload: schemas.DatasetCreate, db: Session = Depends(get_db)):
    """Register a new dataset manifest. Always a new version — manifests are
    immutable, so this never updates an existing row."""
    next_version = (
        db.execute(select(func.max(Dataset.dataset_version)).where(Dataset.dataset_id == payload.dataset_id)).scalar()
        or 0
    ) + 1
    ds = Dataset(
        dataset_id=payload.dataset_id,
        dataset_version=next_version,
        dataset_hash=dataset_hash(payload.manifest),
        owner=payload.owner,
        source=payload.source,
        license=payload.license,
        manifest=payload.manifest,
        schema_version=payload.schema_version,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


@internal_router.get("/datasets/{dataset_id}", response_model=schemas.DatasetOut)
def get_dataset(dataset_id: str, version: int | None = None, db: Session = Depends(get_db)):
    if version is not None:
        return _get_dataset(db, dataset_id, version)
    ds = db.execute(
        select(Dataset).where(Dataset.dataset_id == dataset_id).order_by(Dataset.dataset_version.desc())
    ).scalars().first()
    if ds is None:
        raise HTTPException(404, f"dataset {dataset_id} not found")
    return ds


@internal_router.get("/datasets/{dataset_id}/versions", response_model=list[schemas.DatasetOut])
def list_dataset_versions(dataset_id: str, db: Session = Depends(get_db)):
    return db.execute(
        select(Dataset).where(Dataset.dataset_id == dataset_id).order_by(Dataset.dataset_version)
    ).scalars().all()


@internal_router.post("/files", response_model=schemas.FileOut, status_code=201)
def create_file(payload: schemas.FileCreate, db: Session = Depends(get_db)):
    ds = _get_dataset(db, payload.dataset_id, payload.dataset_version)
    f = File(
        dataset_pk=ds.id,
        source=payload.source,
        checksum=payload.checksum,
        modality=payload.modality,
        location=payload.location,
        pipeline_version=payload.pipeline_version,
        container_digest=payload.container_digest,
        git_commit=payload.git_commit,
        status=payload.status,
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@internal_router.get("/files/{file_id}", response_model=schemas.FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    f = db.execute(select(File).where(File.file_id == file_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"file {file_id} not found")
    return f


@internal_router.patch("/files/{file_id}", response_model=schemas.FileOut)
def update_file(file_id: str, payload: schemas.FileUpdate, db: Session = Depends(get_db)):
    f = db.execute(select(File).where(File.file_id == file_id)).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, f"file {file_id} not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(f, field, value)
    db.commit()
    db.refresh(f)
    return f


@internal_router.get("/files", response_model=list[schemas.FileOut])
def list_files(dataset_id: str, dataset_version: int | None = None, db: Session = Depends(get_db)):
    q = select(File).join(Dataset).where(Dataset.dataset_id == dataset_id)
    if dataset_version is not None:
        q = q.where(Dataset.dataset_version == dataset_version)
    return db.execute(q.order_by(File.created_at)).scalars().all()


@internal_router.post("/features", response_model=schemas.FeatureOut, status_code=201)
def create_feature(payload: schemas.FeatureCreate, db: Session = Depends(get_db)):
    """Register a produced representation. Written once the transform step has
    already run the output-validation gate (§6) — quality_status/detail come
    in already decided, same append-only-log spirit as Dataset."""
    feat = Feature(**payload.model_dump())
    db.add(feat)
    db.commit()
    db.refresh(feat)
    return feat


@internal_router.get("/features/{feature_id}", response_model=schemas.FeatureOut)
def get_feature(feature_id: str, db: Session = Depends(get_db)):
    feat = db.execute(select(Feature).where(Feature.feature_id == feature_id)).scalar_one_or_none()
    if feat is None:
        raise HTTPException(404, f"feature {feature_id} not found")
    return feat


@internal_router.get("/features", response_model=list[schemas.FeatureOut])
def list_features(
    dataset_id: str | None = None,
    dataset_version: int | None = None,
    source_file_id: str | None = None,
    representation_type: str | None = None,
    modality: str | None = None,
    source_hash: str | None = None,
    pipeline_version: str | None = None,
    quality_status: str | None = None,
    produced_since: datetime | None = None,
    produced_before: datetime | None = None,
    db: Session = Depends(get_db),
):
    """The feature repository's query surface (BUILD_PLAN §6 Phase 6): "give
    me all X features produced in this time range" is `representation_type` +
    `produced_since`/`produced_before`; "and only ones that passed QC" is
    `quality_status`; pinning to an exact, reproducible input is `dataset_id`
    + `dataset_version`. `source_hash` + `representation_type` +
    `pipeline_version` together are also the content-addressed cache key
    (BUILD_PLAN §10 Phase 5): identical values across all three mean the
    transform step already produced and validated this exact result before,
    whatever file/dataset it came from.
    """
    q = select(Feature)
    if dataset_id is not None:
        q = q.where(Feature.dataset_id == dataset_id)
    if dataset_version is not None:
        q = q.where(Feature.dataset_version == dataset_version)
    if source_file_id is not None:
        q = q.where(Feature.source_file_id == source_file_id)
    if representation_type is not None:
        q = q.where(Feature.representation_type == representation_type)
    if modality is not None:
        q = q.where(Feature.modality == modality)
    if source_hash is not None:
        q = q.where(Feature.source_hash == source_hash)
    if pipeline_version is not None:
        q = q.where(Feature.pipeline_version == pipeline_version)
    if quality_status is not None:
        q = q.where(Feature.quality_status == quality_status)
    if produced_since is not None:
        q = q.where(Feature.created_at >= produced_since)
    if produced_before is not None:
        q = q.where(Feature.created_at < produced_before)
    return db.execute(q.order_by(Feature.created_at)).scalars().all()


@internal_router.post("/pipelines", response_model=schemas.PipelineOut, status_code=201)
def register_pipeline(payload: schemas.PipelineCreate, db: Session = Depends(get_db)):
    existing = db.execute(
        select(Pipeline).where(
            Pipeline.pipeline_name == payload.pipeline_name,
            Pipeline.pipeline_version == payload.pipeline_version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    p = Pipeline(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@internal_router.get("/pipelines", response_model=list[schemas.PipelineOut])
def list_pipelines(db: Session = Depends(get_db)):
    return db.execute(select(Pipeline).order_by(Pipeline.pipeline_name, Pipeline.pipeline_version)).scalars().all()


@internal_router.post("/jobs", response_model=schemas.JobOut, status_code=201)
def create_job(payload: schemas.JobCreate, db: Session = Depends(get_db)):
    ds = _get_dataset(db, payload.dataset_id, payload.dataset_version)
    job = Job(
        dataset_pk=ds.id,
        pipeline_version=payload.pipeline_version,
        requested_representations=payload.requested_representations,
        content_hash=payload.content_hash,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@internal_router.get("/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, f"job {job_id} not found")
    return job


@internal_router.patch("/jobs/{job_id}", response_model=schemas.JobOut)
def update_job(job_id: str, payload: schemas.JobUpdate, db: Session = Depends(get_db)):
    job = db.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, f"job {job_id} not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(job, field, value)
    db.commit()
    db.refresh(job)
    return job


# Now that every @internal_router... decorator above has run, the router
# actually has routes on it — safe to include.
app.include_router(internal_router)
