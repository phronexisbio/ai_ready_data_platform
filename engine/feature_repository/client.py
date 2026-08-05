"""Feature repository — BUILD_PLAN.md §9/§11/§10 Phase 6: the read/write
contract every producer (engine/steps/transform.py) and every future
consumer (v3 model connectors) goes through, instead of talking to the
catalog API directly.

Backed today by the catalog's `Feature` table — a thin Postgres catalog
storing pointers + metadata, never tensor bytes (BUILD_PLAN §2: "you don't
want Feast [or its replacement] holding bytes, only pointers + stats +
schema"). Feast was the other option BUILD_PLAN §2 names; it's not stood up
here because nothing about this single-user, single-node deployment has hit
a reason to justify its overhead yet — swapping the backend later means
changing this module, not its callers.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from connectors.catalog_client import CatalogClient
from connectors.storage import get as get_object


@dataclass
class FeatureRecord:
    """One row of the BUILD_PLAN §11 output contract: a single produced
    representation, with the metadata that lets a caller decide whether to
    trust and use it without inspecting the tensor itself."""

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
    created_at: datetime

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureRecord":
        return cls(
            feature_id=d["feature_id"],
            source_file_id=d["source_file_id"],
            dataset_id=d["dataset_id"],
            dataset_version=d["dataset_version"],
            modality=d["modality"],
            representation_type=d["representation_type"],
            pipeline_version=d["pipeline_version"],
            source_hash=d["source_hash"],
            location=d["location"],
            model_compatibility_tags=d["model_compatibility_tags"],
            quality_status=d["quality_status"],
            quality_checks_passed=d["quality_checks_passed"],
            quality_detail=d.get("quality_detail"),
            created_at=datetime.fromisoformat(d["created_at"]),
        )


class FeatureRepository:
    def __init__(self, catalog: CatalogClient | None = None):
        self.catalog = catalog or CatalogClient()

    def register(
        self,
        source_file_id: str,
        dataset_id: str,
        dataset_version: int,
        modality: str,
        representation_type: str,
        pipeline_version: str,
        source_hash: str,
        location: str,
        quality_status: str,
        model_compatibility_tags: list[str] | None = None,
        quality_checks_passed: list[str] | None = None,
        quality_detail: str | None = None,
    ) -> FeatureRecord:
        """Write path — what engine/steps/transform.py calls after computing
        (or cache-hitting) a representation."""
        d = self.catalog.create_feature(
            source_file_id=source_file_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            modality=modality,
            representation_type=representation_type,
            pipeline_version=pipeline_version,
            source_hash=source_hash,
            location=location,
            quality_status=quality_status,
            model_compatibility_tags=model_compatibility_tags,
            quality_checks_passed=quality_checks_passed,
            quality_detail=quality_detail,
        )
        return FeatureRecord.from_dict(d)

    def find_cached(self, source_hash: str, representation_type: str, pipeline_version: str) -> FeatureRecord | None:
        """Content-addressed cache lookup (BUILD_PLAN §10 Phase 5)."""
        d = self.catalog.find_feature(source_hash, representation_type, pipeline_version)
        return FeatureRecord.from_dict(d) if d else None

    def query(
        self,
        dataset_id: str | None = None,
        dataset_version: int | None = None,
        representation_type: str | None = None,
        modality: str | None = None,
        quality_status: str | None = "passed",
        produced_since: datetime | None = None,
        produced_before: datetime | None = None,
    ) -> list[FeatureRecord]:
        """"Give me all X features produced in this time range" — the query
        BUILD_PLAN §10 Phase 6's done-when criterion is built around.
        Defaults to `quality_status="passed"` — pass `None` to include
        rejected features too (e.g. for auditing what got quarantined)."""
        results = self.catalog.list_features(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            representation_type=representation_type,
            modality=modality,
            quality_status=quality_status,
            produced_since=produced_since.isoformat() if produced_since else None,
            produced_before=produced_before.isoformat() if produced_before else None,
        )
        return [FeatureRecord.from_dict(d) for d in results]

    def load_tensor(self, feature: FeatureRecord) -> dict:
        """Fetch a feature's actual representation data — hides the MinIO
        location format from callers, so "any model can connect" doesn't
        require knowing the storage layout, just a FeatureRecord."""
        return json.loads(get_object(feature.location))
