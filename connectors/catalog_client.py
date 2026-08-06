"""Thin HTTP client for the metadata catalog service (catalog/api.py)."""

import os

import requests


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set — Phase 13 (BUILD_PLAN_COMMERCIAL.md) added internal-API "
            f"authentication; wire it from the catalog-internal-api-secret Secret."
        )
    return value


CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog.data-platform.svc.cluster.local:8000")
INTERNAL_API_SECRET = _required_env("INTERNAL_API_SECRET")


class CatalogClient:
    """Every request carries X-Internal-Secret (Phase 13) — the internal
    endpoints in catalog/api.py reject anything without it. A requests.Session
    with a default header means every method below gets this for free without
    repeating it at each call site."""

    def __init__(self, base_url: str = CATALOG_URL):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers["X-Internal-Secret"] = INTERNAL_API_SECRET

    def create_dataset(
        self,
        dataset_id: str,
        owner: str,
        source: str,
        manifest: dict,
        license: str | None = None,
        schema_version: str = "1",
    ) -> dict:
        r = self._session.post(
            f"{self.base_url}/datasets",
            json={
                "dataset_id": dataset_id,
                "owner": owner,
                "source": source,
                "manifest": manifest,
                "license": license,
                "schema_version": schema_version,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def create_feature(
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
    ) -> dict:
        r = self._session.post(
            f"{self.base_url}/features",
            json={
                "source_file_id": source_file_id,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "modality": modality,
                "representation_type": representation_type,
                "pipeline_version": pipeline_version,
                "source_hash": source_hash,
                "location": location,
                "model_compatibility_tags": model_compatibility_tags or [],
                "quality_status": quality_status,
                "quality_checks_passed": quality_checks_passed or [],
                "quality_detail": quality_detail,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def find_feature(self, source_hash: str, representation_type: str, pipeline_version: str) -> dict | None:
        """Content-addressed cache lookup (BUILD_PLAN §10 Phase 5). Returns the
        earliest matching Feature — same key always means the same result, so
        which matching row comes back doesn't change correctness — or None."""
        r = self._session.get(
            f"{self.base_url}/features",
            params={
                "source_hash": source_hash,
                "representation_type": representation_type,
                "pipeline_version": pipeline_version,
            },
            timeout=30,
        )
        r.raise_for_status()
        results = r.json()
        return results[0] if results else None

    def list_features(
        self,
        dataset_id: str | None = None,
        dataset_version: int | None = None,
        source_file_id: str | None = None,
        representation_type: str | None = None,
        modality: str | None = None,
        source_hash: str | None = None,
        pipeline_version: str | None = None,
        quality_status: str | None = None,
        produced_since: str | None = None,
        produced_before: str | None = None,
    ) -> list[dict]:
        """General feature-repository query (BUILD_PLAN §10 Phase 6).
        `produced_since`/`produced_before` are ISO 8601 strings."""
        params = {
            k: v
            for k, v in {
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "source_file_id": source_file_id,
                "representation_type": representation_type,
                "modality": modality,
                "source_hash": source_hash,
                "pipeline_version": pipeline_version,
                "quality_status": quality_status,
                "produced_since": produced_since,
                "produced_before": produced_before,
            }.items()
            if v is not None
        }
        r = self._session.get(f"{self.base_url}/features", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_file(self, file_id: str) -> dict:
        r = self._session.get(f"{self.base_url}/files/{file_id}", timeout=30)
        r.raise_for_status()
        return r.json()

    def list_files(self, dataset_id: str, dataset_version: int | None = None) -> list[dict]:
        params = {"dataset_id": dataset_id}
        if dataset_version is not None:
            params["dataset_version"] = dataset_version
        r = self._session.get(f"{self.base_url}/files", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def update_file(
        self,
        file_id: str,
        status: str | None = None,
        status_detail: str | None = None,
        location: str | None = None,
    ) -> dict:
        payload = {}
        if status is not None:
            payload["status"] = status
        if status_detail is not None:
            payload["status_detail"] = status_detail
        if location is not None:
            payload["location"] = location
        r = self._session.patch(f"{self.base_url}/files/{file_id}", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def create_file(
        self,
        dataset_id: str,
        dataset_version: int,
        source: str,
        checksum: str,
        modality: str,
        location: str,
        **extra,
    ) -> dict:
        r = self._session.post(
            f"{self.base_url}/files",
            json={
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "source": source,
                "checksum": checksum,
                "modality": modality,
                "location": location,
                **extra,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
