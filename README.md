# AI-Ready Bulk Data Platform

A platform that turns raw, multi-modal drug-discovery data (protein/DNA/RNA sequences, small molecules, protein structures, protein–ligand complexes, cellular microscopy images, tabular assay data) into validated, canonicalized, model-ready tensors, versioned in a queryable feature repository.

It stops there. Model training, fine-tuning, and inference serving are explicitly out of scope — this platform's job ends once a feature is sitting in the repository, versioned and ready to be pulled by whatever consumes it next.

Full architecture, technology rationale, and the phase-by-phase build plan live in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). Day-to-day commands, deployment gotchas, and per-phase implementation notes live in [`CLAUDE.md`](CLAUDE.md). This file covers the two things every future connector and every future model-connector integration actually depends on: **the output contract** and **the catalog schema**.

## Architecture, in one line

```
Connectors → NATS (event bus) → Metadata Catalog → Ingest/Validate → Transform (Argo + Ray) →
Content-Addressed Cache → Output Validation → Data Lake (MinIO) → Feature Repository → SDK/CLI/REST
```

Everything runs as Kubernetes pods on a local `kind` cluster. See `docs/BUILD_PLAN.md` §1 for the full diagram and §2 for why each piece of technology was chosen.

## The output contract

Every representation a transform pipeline produces is one of exactly **four container types**:

| Type | Shape | Example models |
|---|---|---|
| Token-ID sequence | `[seq_len]` + attention mask | ESM-2/3, TxGemma, REINVENT, ProtBERT-style |
| Graph batch | node features, edge index, edge features | Chemprop, ProteinMPNN, GNN-based ADMET |
| Dense image tensor | `[C, H, W]` float32 | Phenom-style phenomics models |
| SE(3) frame tensor | rotation + translation per residue/atom | RFdiffusion, AlphaFold/Boltz-class |

A single input can produce **more than one** representation (a molecule can be both a graph and a token sequence) — the rigid part isn't "one output per input," it's that every representation, however many get generated, conforms to one of these four shapes. Which representations get generated is a job parameter (`representations: [...]`); the default is the primary representation for that modality, more is opt-in.

Every produced representation carries this metadata (the `Feature` row — see schema below):

```
{ feature_id, source_file_id, dataset_id, dataset_version, modality,
  representation_type, pipeline_version, source_hash, location,
  model_compatibility_tags, quality_status, quality_checks_passed,
  quality_detail, created_at }
```

- **`source_hash` + `dataset_id`** tie multiple representations of the same input back together (a molecule's graph and its token sequence share both).
- **`representation_type`** is what a caller filters on to get a specific shape (`"molecule_graph"` vs `"molecule_tokens"`) — see `engine/feature_repository/`.
- **`quality_status`** is `"passed"` or `"rejected"` from the output-validation gate (NaN/Inf checks, round-trip checks, shape checks — see `docs/BUILD_PLAN.md` §6). A rejected feature still gets a row, with `quality_detail` recording why — nothing is silently dropped.

**Reading a feature**: go through `engine.feature_repository.FeatureRepository` (`register()`/`find_cached()`/`query()`/`load_tensor()`), not the catalog HTTP API directly — the repository is the stable, swappable contract (BUILD_PLAN §2 names Feast as an alternative backend; this is the seam where that swap would happen without touching callers).

## The catalog schema

The metadata catalog (Postgres, `catalog/` service) is the source of truth every other component queries instead of inspecting files directly. Five tables:

| Table | Purpose |
|---|---|
| `Dataset` | A versioned manifest. Same `dataset_id`, higher `dataset_version` is how a dataset changes — **rows are never updated in place**, always a new version. `dataset_hash` is computed from the manifest as a whole (not per-file checksums), which is what makes dataset-level content-addressed caching trustworthy. |
| `File` | One ingested file, tied to the dataset version whose manifest listed it. `status` tracks its journey (`landed` → `raw` → `validated`/`rejected` → `featurized`); `status_detail` carries the input-validation failure reason when rejected. `location` always points to the file's *current* zone. |
| `Pipeline` | A registered pipeline version (semver, e.g. `sequence_pipeline:1.2.0`). |
| `Job` | A processing job run against a dataset version by a pipeline version (fields exist for future job-level orchestration; not yet the primary mechanism — see `docs/BUILD_PLAN.md` §8 on the deferred Job Service). |
| `Feature` | One produced representation — the output-contract row described above. Also append-only, like `Dataset`. |

**Two rules that matter more than the schema itself** (BUILD_PLAN §7):
1. Dataset manifests are immutable — a change is always a new `dataset_version`, the same way a Docker tag or a git commit is never mutated in place.
2. `dataset_hash` is computed from the manifest, not just per-file checksums.

A feature's full reproducibility manifest is a join across these tables: raw dataset hash → pipeline version → container digest → git commit → config version → feature hash → timestamp. That's a query, not a separate provenance service.

## Repository structure

```
connectors/       # base.py Connector ABC + one module per source (local, uniprot, chembl, pubchem, ...)
catalog/           # Postgres schema + FastAPI service — the metadata source of truth
engine/
├── pipelines/       # molecule/sequence/structure/image: adapters -> canonical record -> representations
├── validators/       # input/ (Phase 2 gate) and output/ (Phase 3-4 gate) per modality
├── steps/             # Argo step scripts: ingest.py, validate.py, transform.py, ray_batch_transform.py
├── feature_repository/ # the read/write contract for produced features
├── ingestion_service.py # NATS -> Argo bridge
└── ray_tasks.py        # Ray remote tasks for the heaviest transform steps
workflows/argo/     # WorkflowTemplate + CronWorkflow definitions
infra/               # kind config, Helm values, raw k8s manifests, backup image
observability/dashboards/ # Grafana dashboard JSON
tests/
├── sample_data/      # fixtures used by both unit and manual verification
├── unit/               # fast, no cluster needed
└── integration/         # needs the live cluster — see below
```

## Running the tests

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

# unit tests — fast, no cluster, no network (external HTTP is mocked)
python -m pytest

# integration test — needs the live cluster; see tests/integration/test_end_to_end.py's
# module docstring for the exact port-forward commands
python -m pytest -m integration
```

Unit tests are excluded from `-m integration` runs and vice versa (configured in `pyproject.toml`) — the integration test makes one real network call (to UniProt) and drives the full ingest → validate → transform pipeline through Argo, so it's deliberately not part of the fast default run.

## Bringing the platform up

See [`CLAUDE.md`](CLAUDE.md) for the full command reference (cluster bring-up, per-service rebuild/reload, and the non-obvious gotchas hit at each phase — KubeRay quirks, Argo executor behavior, inotify limits, and so on). The short version:

```bash
kind create cluster --config infra/kind-cluster.yaml
# then install each Helm-deployed service per CLAUDE.md's per-phase sections
```
