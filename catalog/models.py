"""Metadata catalog schema — BUILD_PLAN.md §7.

Every other component (connectors, engine, feature repository) queries this
schema instead of inspecting files directly. Two rules matter more than the
schema itself:

- Dataset manifests are immutable: a change is always a new `dataset_version`
  row, never an edit to an existing one (same idea as an immutable Docker tag).
- `dataset_hash` is computed from the manifest as a whole, not just per-file
  checksums, which is what makes the content-addressed cache trustworthy at
  the dataset level later on.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Dataset(Base):
    """A versioned dataset manifest. Same `dataset_id`, higher `dataset_version`
    is how a dataset changes — rows are never updated in place."""

    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("dataset_id", "dataset_version", name="uq_dataset_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dataset_version: Mapped[int] = mapped_column(nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    files: Mapped[list["File"]] = relationship(back_populates="dataset")


class File(Base):
    """A single ingested file, tied to the specific dataset version whose
    manifest listed it."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    file_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True, default=_uuid)
    dataset_pk: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    modality: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[str | None] = mapped_column(String, nullable=True)
    container_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="landed")
    status_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    dataset: Mapped["Dataset"] = relationship(back_populates="files")


class Pipeline(Base):
    """A registered pipeline version — semver, e.g. sequence_pipeline:1.2.0."""

    __tablename__ = "pipelines"
    __table_args__ = (UniqueConstraint("pipeline_name", "pipeline_version", name="uq_pipeline_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    pipeline_name: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String, nullable=False)
    config_version: Mapped[str | None] = mapped_column(String, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    container_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Feature(Base):
    """A model-ready representation produced by a transform pipeline —
    BUILD_PLAN.md §11 metadata contract + §6 output-quality fields.

    `source_hash` + `dataset_id` tie multiple representations of the same
    input back together (e.g. a molecule's graph and token-sequence
    representations share both). This is an append-only audit log, like
    Dataset: a rejected feature still gets a row, with quality_status
    recording why, rather than being silently dropped.
    """

    __tablename__ = "features"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    feature_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True, default=_uuid)
    source_file_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dataset_version: Mapped[int] = mapped_column(nullable=False)
    modality: Mapped[str] = mapped_column(String, nullable=False)
    representation_type: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String, nullable=False)
    source_hash: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    model_compatibility_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_status: Mapped[str] = mapped_column(String, nullable=False)
    quality_checks_passed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_detail: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    """A processing job run against a dataset version by a pipeline version."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True, default=_uuid)
    dataset_pk: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String, nullable=False)
    requested_representations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    """A per-customer API key for the /public/* surface — BUILD_PLAN_COMMERCIAL.md
    Phase 13. Replaces the single shared PUBLIC_API_KEY so every request is
    traceable to a specific tenant and individually revocable without
    affecting any other tenant's key. `key_hash` is a SHA-256 of the secret
    half of the key (see catalog/api_key_auth.py) — the raw key is shown once
    at creation and never stored anywhere."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    key_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
