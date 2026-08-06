from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DatasetCreate(BaseModel):
    dataset_id: str
    owner: str
    source: str
    manifest: dict
    license: str | None = None
    schema_version: str = "1"
    # Phase 14 (BUILD_PLAN_COMMERCIAL.md): optional because every existing
    # internal caller (every connector, via CatalogClient) predates tenants —
    # catalog/api.py's create_dataset defaults this to the reserved
    # "platform" tenant when omitted, rather than requiring every connector
    # and CronWorkflow to be touched just to pass a value that's always the
    # same for them.
    tenant_id: str | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dataset_id: str
    dataset_version: int
    dataset_hash: str
    owner: str
    source: str
    tenant_id: str
    license: str | None
    manifest: dict
    schema_version: str
    created_at: datetime


class FileCreate(BaseModel):
    dataset_id: str
    dataset_version: int
    source: str
    checksum: str
    modality: str
    location: str
    pipeline_version: str | None = None
    container_digest: str | None = None
    git_commit: str | None = None
    status: str = "landed"


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: str
    source: str
    checksum: str
    modality: str
    pipeline_version: str | None
    container_digest: str | None
    git_commit: str | None
    status: str
    status_detail: str | None
    location: str
    tenant_id: str
    created_at: datetime


class FileUpdate(BaseModel):
    status: str | None = None
    status_detail: str | None = None
    location: str | None = None


class FeatureCreate(BaseModel):
    source_file_id: str
    dataset_id: str
    dataset_version: int
    modality: str
    representation_type: str
    pipeline_version: str
    source_hash: str
    location: str
    model_compatibility_tags: list[str] = []
    quality_status: str
    quality_checks_passed: list[str] = []
    quality_detail: str | None = None


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_id: str
    source_file_id: str
    dataset_id: str
    dataset_version: int
    modality: str
    representation_type: str
    pipeline_version: str
    source_hash: str
    location: str
    model_compatibility_tags: list[str]
    quality_status: str
    quality_checks_passed: list[str]
    quality_detail: str | None
    tenant_id: str
    created_at: datetime


class PipelineCreate(BaseModel):
    pipeline_name: str
    pipeline_version: str
    config_version: str | None = None
    git_commit: str | None = None
    container_digest: str | None = None


class PipelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pipeline_name: str
    pipeline_version: str
    config_version: str | None
    git_commit: str | None
    container_digest: str | None
    created_at: datetime


class JobCreate(BaseModel):
    dataset_id: str
    dataset_version: int
    pipeline_version: str
    requested_representations: list[str] = []
    content_hash: str | None = None


class JobUpdate(BaseModel):
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    pipeline_version: str
    requested_representations: list[str]
    status: str
    content_hash: str | None
    started_at: datetime | None
    finished_at: datetime | None
