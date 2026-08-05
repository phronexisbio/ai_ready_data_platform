"""End-to-end integration test — BUILD_PLAN.md §10 Phase 11: "run a small
mixed-modality, multi-source batch through the full path, assert feature
repository contents match expected schema."

Needs the live cluster reachable (Postgres/MinIO/NATS/catalog/Argo) — the
same port-forwards used throughout manual verification in this project:

    kubectl -n data-platform port-forward svc/minio 19000:9000 &
    kubectl -n data-platform port-forward svc/nats 14222:4222 &
    kubectl -n data-platform port-forward svc/catalog 18000:8000 &
    MINIO_ENDPOINT=http://127.0.0.1:19000 NATS_URL=nats://127.0.0.1:14222 \\
      CATALOG_URL=http://127.0.0.1:18000 python -m pytest -m integration

Excluded from the default test run (pyproject.toml's addopts) since it needs
that live infrastructure and makes one real network call to UniProt for
genuine multi-source coverage (BUILD_PLAN's own wording), not two
differently-labeled local connectors pretending to be different sources.
"""

import subprocess
import time
import uuid

import pytest
import requests

from connectors.catalog_client import CatalogClient
from connectors.local_connector import LocalConnector
from connectors.uniprot_connector import UniProtConnector
from engine.feature_repository import FeatureRepository

pytestmark = pytest.mark.integration

SAMPLE_DATA_DIR = "tests/sample_data/local_batch"


def _catalog_reachable() -> bool:
    try:
        requests.get(f"{CatalogClient().base_url}/health", timeout=3).raise_for_status()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_live_cluster():
    if not _catalog_reachable():
        pytest.skip(
            "catalog not reachable — this test needs the live cluster; "
            "see this file's module docstring for the port-forward commands"
        )


@pytest.fixture
def dataset_id():
    """A fresh, uniquely-named dataset per test run — avoids colliding with
    real platform data or previous runs of this same test."""
    return f"test-e2e-{uuid.uuid4().hex[:8]}"


def _run_ingestion_service_once():
    """Drains PLATFORM_EVENTS once, submitting an Argo Workflow per event —
    the same script used throughout manual verification of this project."""
    result = subprocess.run(
        ["python", "-m", "engine.ingestion_service"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"ingestion_service failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _wait_for_workflows(names: list[str], timeout_s: int = 120):
    deadline = time.time() + timeout_s
    remaining = set(names)
    while remaining and time.time() < deadline:
        for name in list(remaining):
            phase = subprocess.run(
                ["kubectl", "-n", "data-platform", "get", "workflow", name, "-o", "jsonpath={.status.phase}"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if phase in ("Succeeded", "Failed", "Error"):
                remaining.discard(name)
        if remaining:
            time.sleep(3)
    assert not remaining, f"workflow(s) did not finish in time: {remaining}"


def _extract_workflow_names(ingestion_service_stdout: str) -> list[str]:
    """Parses lines like 'workflow.argoproj.io/ingest-validate-xmjcq created'."""
    names = []
    for line in ingestion_service_stdout.splitlines():
        if "workflow.argoproj.io/" in line:
            after_slash = line.strip().split("/")[-1]
            names.append(after_slash.split()[0])  # drop the trailing " created"
    return names


def test_mixed_modality_multi_source_batch_produces_correct_feature_schema(dataset_id):
    # --- land a mixed-modality, multi-source batch ---
    local_landed = LocalConnector(batch_dir=SAMPLE_DATA_DIR).run(dataset_id=dataset_id, owner="integration-test")
    uniprot_landed = UniProtConnector(accessions=["P69905"]).run(dataset_id=f"{dataset_id}-uniprot", owner="integration-test")

    modalities_landed = {f.modality for f in local_landed} | {f.modality for f in uniprot_landed}
    sources_landed = {f.event_subject.rsplit(".", 1)[-1] for f in (local_landed + uniprot_landed)}
    assert modalities_landed >= {"molecule", "sequence"}, "expected at least molecule + sequence modalities"
    assert sources_landed >= {"local", "uniprot"}, "expected at least local + uniprot sources"

    # --- drive it through the real pipeline ---
    stdout = _run_ingestion_service_once()
    workflow_names = _extract_workflow_names(stdout)
    assert workflow_names, "ingestion_service submitted no workflows — events may not have been published"
    _wait_for_workflows(workflow_names)

    # --- assert feature repository contents match the BUILD_PLAN §11 schema ---
    repo = FeatureRepository()
    features = repo.query(dataset_id=dataset_id) + repo.query(dataset_id=f"{dataset_id}-uniprot")
    assert features, "no features were produced for the test batch"

    for f in features:
        assert f.feature_id and isinstance(f.feature_id, str)
        assert f.source_file_id and isinstance(f.source_file_id, str)
        assert isinstance(f.dataset_version, int) and f.dataset_version >= 1
        assert f.modality in {"molecule", "sequence", "structure", "image"}
        assert f.representation_type  # e.g. "molecule_graph", "sequence_tokens"
        assert ":" in f.pipeline_version  # "molecule_pipeline:0.1.0" shape
        assert len(f.source_hash) == 64  # sha256 hex digest
        assert f.location.startswith("features/") or f.location.startswith("rejected/")
        assert isinstance(f.model_compatibility_tags, list)
        assert f.quality_status in {"passed", "rejected"}
        assert isinstance(f.quality_checks_passed, list)

    produced_modalities = {f.modality for f in features}
    assert produced_modalities >= {"molecule", "sequence"}, "expected features for both landed modalities"

    # --- a produced feature's tensor is actually fetchable and well-formed ---
    passed = [f for f in features if f.quality_status == "passed"]
    assert passed, "expected at least one passed feature"
    tensor = repo.load_tensor(passed[0])
    assert "representation_type" in tensor

    _cleanup(dataset_id)
    _cleanup(f"{dataset_id}-uniprot")


def _cleanup(dataset_id: str):
    """Best-effort teardown so this test leaves no trace in the catalog —
    matches the hygiene maintained throughout manual verification."""
    try:
        subprocess.run(
            [
                "kubectl", "-n", "data-platform", "exec", "postgres-postgresql-0", "--",
                "env", "PGPASSWORD=catalog", "psql", "-U", "catalog", "-d", "catalog", "-c",
                f"DELETE FROM features WHERE dataset_id = '{dataset_id}'; "
                f"DELETE FROM files WHERE dataset_pk IN (SELECT id FROM datasets WHERE dataset_id = '{dataset_id}'); "
                f"DELETE FROM datasets WHERE dataset_id = '{dataset_id}';",
            ],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass  # best-effort — don't fail the test over cleanup
