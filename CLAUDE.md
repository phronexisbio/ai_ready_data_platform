# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Phase 0 (cluster foundation), Phase 1 (metadata catalog + connector framework), Phase 2 (ingest + validate), Phase 3 (molecule + sequence transform pipelines, plus tabular_pipeline added later — see below), Phase 4 (structure + image transform pipelines), Phase 5 (content-addressed caching), Phase 6 (feature repository), Phase 7 (additional connectors), Phase 8 (KubeRay GPU integration — CPU-only in this environment, see below), Phase 9 (observability), Phase 10 (backups), and an initial pass at Phase 11 (tests + README) are done. Phase 11 is explicitly ongoing (BUILD_PLAN's own framing, not a one-time milestone) — the test suite should keep growing as code changes, not be treated as finished. When implementation continues on v2's scope, follow the repository structure and phase order defined in `docs/BUILD_PLAN.md` (§9 and §10) rather than improvising a different layout.

**This platform is now being used commercially by a company (contractual work), not just as a single-operator demo.** v2 was explicitly scoped for a single internal operator (no auth, no multi-tenancy, secrets as plain Helm values) — that scoping is now a set of real gaps, not a deferred nice-to-have. An evidence-based audit found 23 concrete gaps (no auth distinguishing customers, no tenant isolation in the schema, plaintext credentials committed to git, no HA/DR, no alerting, connector-sourced data licenses never populated, and more). **`docs/BUILD_PLAN_COMMERCIAL.md`** (Phases 12–22) is the remediation plan — Phase 12 (secrets remediation) and Phase 13 (per-customer authentication) are done; Phases 14–22 are not yet started. Follow the plan's suggested order (14 → 16 → 17 → 18 → 19 → 15 → 20 → 21 → 22) for further commercial-hardening work, the same way v2's phases were followed in order, rather than picking gaps to fix ad hoc.

**Run `python -m pytest` (unit tests) before considering any change to `engine/` or `connectors/` done.** They're fast (<1s, no cluster/network needed) and already caught one real bug (a truthy-`Element` bug in `uniprot_xml.py` — see Phase 11 below) — there's no excuse not to run them.

## Cluster commands (Phase 0)

CLIs (`kind`, `kubectl`, `helm`) are installed to `~/.local/bin` (no sudo on this box — installed there instead of `/usr/local/bin`; Docker itself was already present).

```bash
# bring the cluster up (3 nodes: 1 control-plane + 2 workers)
kind create cluster --config infra/kind-cluster.yaml

# tear it down
kind delete cluster --name ai-ready-data-platform

# everything (MinIO, NATS, and all future platform services) lives in the data-platform namespace
kubectl -n data-platform get pods,svc

# MinIO: root credentials live in the `minio-credentials` Secret (Phase 12,
# BUILD_PLAN_COMMERCIAL.md — no plaintext value in infra/helm/minio-values.yaml
# anymore). Retrieve for local `mc`/SDK use with:
#   kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootUser}' | base64 -d; echo
#   kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootPassword}' | base64 -d; echo
# data lake zone buckets from BUILD_PLAN §5 are pre-created via the chart's `buckets:` list.
kubectl -n data-platform port-forward svc/minio 9000:9000     # then use `mc`/S3 SDK against localhost:9000
kubectl -n data-platform port-forward svc/minio-console 9001:9001

# NATS: JetStream is enabled (infra/helm/nats-values.yaml). The `nats-box` pod ships the `nats` CLI
# for ad hoc stream/consumer inspection without installing anything locally:
kubectl -n data-platform exec -it deploy/nats-box -- nats stream ls
```

Re-install/upgrade either service after editing its values file with:
```bash
helm upgrade minio minio/minio -n data-platform -f infra/helm/minio-values.yaml
helm upgrade nats nats/nats -n data-platform -f infra/helm/nats-values.yaml
```

Note: the NATS values file sets up JetStream file storage but does not yet configure the Phase-0-mentioned dead-letter subject — that's a per-stream `max_deliver` + DLQ consumer, created when the actual event streams are defined in Phase 1, not part of the base NATS deployment.

## Catalog + connector commands (Phase 1)

Python environment: a single venv at `.venv/` (repo root) covers both `catalog/` and `connectors/` during local dev — `python3 -m venv .venv && source .venv/bin/activate && pip install -r catalog/requirements.txt -r connectors/requirements.txt`. Each of `catalog/` and `connectors/` keeps its own `requirements.txt` because they ship as separate Docker images/CronJobs eventually, even though local dev shares one venv.

```bash
# Postgres (bitnami chart): db "catalog", user "catalog". Passwords live in the
# `postgres-credentials` Secret (Phase 12, BUILD_PLAN_COMMERCIAL.md — no
# plaintext value in infra/helm/postgres-values.yaml anymore). Retrieve with:
#   kubectl -n data-platform get secret postgres-credentials -o jsonpath='{.data.password}' | base64 -d; echo           # catalog user
#   kubectl -n data-platform get secret postgres-credentials -o jsonpath='{.data.postgres-password}' | base64 -d; echo  # superuser

# catalog service: rebuild + redeploy after editing catalog/*.py
docker build -f catalog/Dockerfile -t catalog:phase1 .
kind load docker-image catalog:phase1 --name ai-ready-data-platform
kubectl -n data-platform rollout restart deployment/catalog   # picks up the reloaded image

# table creation is automatic: catalog/api.py runs Base.metadata.create_all() on startup —
# there's no separate migration step yet (no Alembic; add it if/when schema changes need
# to preserve existing data rather than starting from an empty dev DB)

# the JetStream stream connectors publish to must exist before anything can publish:
bash infra/setup-nats-streams.sh   # run once per cluster, via nats-box — see the script for the exact kubectl invocation

# run a connector from the host against the in-cluster services (port-forward all three first)
kubectl -n data-platform port-forward svc/minio 19000:9000 &
kubectl -n data-platform port-forward svc/nats 14222:4222 &
kubectl -n data-platform port-forward svc/catalog 18000:8000 &
# MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required as of Phase 12 (no more
# plaintext fallback default) — pull them from the Secret, don't hardcode:
MINIO_ACCESS_KEY=$(kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootUser}' | base64 -d) \
MINIO_SECRET_KEY=$(kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootPassword}' | base64 -d) \
MINIO_ENDPOINT=http://127.0.0.1:19000 NATS_URL=nats://127.0.0.1:14222 CATALOG_URL=http://127.0.0.1:18000 \
  python3 -c "from connectors.local_connector import LocalConnector; LocalConnector(batch_dir='tests/sample_data/local_batch').run(dataset_id='my-batch', owner='me')"
```

Every connector (`connectors/base.py`'s `Connector.run()`) does the same four things regardless of source: `discover()` → `fetch()` → `validate()` → land bytes into MinIO `landing/{source}/{dataset_id}/{filename}` untouched → register one `Dataset` version + one `File` row per landed file via the catalog API → publish one JetStream event per file on `platform.catalog.file.{source}`. A new connector only needs to subclass `Connector` and implement `discover()`/`fetch()` (see `connectors/local_connector.py` and `connectors/uniprot_connector.py`) — it never touches storage/catalog/event-publishing code directly.

## Ingest + validate commands (Phase 2)

Argo Workflows (controller only, no UI — `infra/helm/argo-workflows-values.yaml`) runs in `data-platform` in `singleNamespace` mode, using the `argo-workflow` ServiceAccount (required: the emissary executor's `wait` sidecar needs `workflowtaskresults` RBAC that the default SA doesn't have — always set `serviceAccountName: argo-workflow` on Workflow specs/templates in this cluster).

```bash
# rebuild + reload after editing engine/*.py or engine/steps/*.py
docker build -f engine/Dockerfile -t engine-steps:phase2 .
kind load docker-image engine-steps:phase2 --name ai-ready-data-platform

# re-apply after editing the DAG shape
kubectl apply -f workflows/argo/ingest-validate-template.yaml

# drain pending "file landed" events and submit one Workflow per file
# (port-forward NATS first; it shells out to `kubectl create -f -` for each workflow, so needs your kube context, not a port-forward, for Argo)
kubectl -n data-platform port-forward svc/nats 14222:4222 &
NATS_URL=nats://127.0.0.1:14222 python3 -m engine.ingestion_service

# inspect outcomes
kubectl -n data-platform get workflows
kubectl -n data-platform get workflow <name> -o json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(n['displayName'], n['phase'], n.get('message')) for n in d['status']['nodes'].values()]"
```

The DAG (`workflows/argo/ingest-validate-template.yaml`) is two steps per file: `ingest` (copies `landing/` → `raw/{source}/{dataset_id}/`, catalog status → `raw`) then `validate` (runs the modality's input validator from `engine/validators/input/registry.py`; on pass copies `raw/` → `validated/` and sets status `validated`, on fail copies to `rejected/` and sets status `rejected` + `status_detail` to the failure reason). A rejection is a normal business outcome, not a bug — both step scripts always exit 0, and pass/fail is read from the catalog's `status` field, not from Argo node success/failure.

One executor-specific gotcha worth knowing if this breaks again: the `ingest` step's raw location has to reach the `validate` step as an Argo `outputs.parameters` (`valueFrom.path`), but the `main` and `wait` containers in this executor only share the `/var/run/argo` emptyDir — not `/tmp`. The output file must be written under `/var/run/argo/outputs/...` (see `engine/steps/ingest.py`'s `OUTPUT_PATH`), not `/tmp`, or the wait sidecar can't find it and the workflow errors out.

`engine/ingestion_service.py` (the "Ingestion Service" in BUILD_PLAN's architecture diagram) is the NATS → Argo bridge: a durable JetStream pull-consumer (`ingestion-service` on `PLATFORM_EVENTS`) that submits one Workflow per event, then acks. It's invoked as a one-shot drain-and-exit batch, not a long-running Deployment — there's no scheduling/contention need yet to justify one (same reasoning as the Kueue/Job Service deferrals in BUILD_PLAN §8); the durable consumer means nothing is lost by running it periodically instead.

## Transform pipeline commands (Phase 3)

The DAG now has a third step, `transform`, gated with `when: "{{tasks.validate.outputs.parameters.result}} == PASSED"` so it never runs on data that already failed input validation. Rebuild/reload/reapply the same way as Phase 2 (`docker build -f engine/Dockerfile -t engine-steps:phase2 .` — the image tag stayed `phase2`, it's just a dev tag, not a version marker; `kind load docker-image ...`; `kubectl apply -f workflows/argo/ingest-validate-template.yaml`).

`engine/pipelines/{molecule,sequence}_pipeline/` both follow the same three-piece shape (BUILD_PLAN §4a/§4b):
- `adapters/` — one module per input format, each exporting `parse(content: bytes) -> list[Record]`. molecule: smiles/sdf/inchi/mol2 (all funnel through RDKit, which is why SMILES and SDF of the same molecule produce byte-identical `canonical_smiles`). sequence: fasta/uniprot_xml/uniprot_json/genbank.
- `canonical.py` — the one record shape (`MoleculeRecord/SequenceRecord`) featurization is written against.
- `featurize.py` — canonical record -> representation dict(s). Molecule: `to_graph` (default) and `to_tokens` (opt-in). Sequence: `to_tokens` (default); `to_msa` is a stub returning `None` — real MSA (MMseqs2) isn't implemented yet, so anything that requests it should currently fail output validation (`engine/validators/output/sequence.py`'s `validate_msa`), not silently skip.
- The package `__init__.py` is the dispatcher: picks the adapter by file extension, calls the requested featurizers (`DEFAULT_REPRESENTATIONS` if the job didn't ask for anything specific), returns `{record_name, canonical_form, representation_type, tensor}` per (record, representation) pair.

`engine/steps/transform.py` is modality-routed via its `_PIPELINES` dict (`molecule`/`sequence`/`structure`/`image`/`tabular`; `text` is still the only modality with no transform pipeline, skipped rather than rejected). For each representation it runs the matching `engine/validators/output/*.py` check (BUILD_PLAN §6), writes the tensor as JSON to `features/{source}/{dataset_id}/{filename}.{representation_type}.json` on pass or `rejected/...` on fail, and registers a `Feature` catalog row either way — same "always exit 0, quality lives in the catalog field" pattern as `validate.py`. Tensors are plain JSON for now, not Zarr/Parquet — that formalization is Phase 5/6's job once the cache and feature-repository layers exist; don't jump ahead of it.

`engine/pipelines/tabular_pipeline/` was added after the fact (public-frontend visitors kept pulling GEO/SRA metadata and getting no final output, since tabular previously stopped at `validated`). Same three-piece shape as molecule/sequence: `adapters/csv_adapter.py` and `adapters/tsv_adapter.py` each parse one `TabularRecord` per data row; `featurize.py`'s `to_tokens` is the only representation, since tabular assay/metadata rows don't naturally fit BUILD_PLAN §11's other three container types (graph, image, SE(3) frames) the way they fit token-ID sequence. Each cell becomes one token via a stable SHA-256-based hash ("column=value" -> a fixed vocab slot) rather than a trained/fitted vocabulary — deliberately simple 0.1.0 scope, same as sequence_pipeline's tokenizer before anything fancier was needed.

The `Feature` table (catalog/models.py) is BUILD_PLAN §11's metadata contract plus §6's quality fields, one append-only row per produced representation (never updated in place, like `Dataset`): `source_hash` + `dataset_id` tie a molecule's graph and token representations back together as views of the same input; `quality_status`/`quality_checks_passed`/`quality_detail` record the output-validation outcome. Query via `GET /features?dataset_id=&source_file_id=&representation_type=&modality=`.

## Structure + image pipeline notes (Phase 4)

`engine/pipelines/structure_pipeline/` uses **Biopython for both PDB and mmCIF**, not BioPandas for either — BioPandas' `PandasMmcif` reader is broken in the currently-pinned version (`_construct_df` KeyErrors on `auth_atom_id` even against a spec-complete fixture); Biopython's `PDBParser`/`MMCIFParser` produce the identical `Structure` object hierarchy for both formats, so one extraction code path (`adapters/pdb.py`'s `_extract`, reused by `adapters/mmcif.py`) covers both. Waters/heteroatoms are stripped for free by filtering on Biopython's hetero flag (`residue.id[0] != " "`), not a resname denylist. `featurize.py` builds SE(3) frames (default) via the standard AlphaFold-style Gram-Schmidt rigid-from-3-points construction on N/CA/C, and a residue graph (opt-in) with sequential + CA-CA-distance (≤8Å) edges. A residue missing any of N/CA/C is dropped from the frame tensor — `engine/validators/output/structure.py`'s residue-count-matches-source check is what catches that, which is also what a genuinely incomplete input structure looks like at the output-validation gate (not a synthetic case: `tests/sample_data/structure_batch/incomplete_backbone.pdb` demonstrates it for real).

`engine/pipelines/image_pipeline/` normalizes against the **source dtype's fixed range** (e.g. divide by 65535 for uint16), deliberately not a per-image min/max rescale — min/max rescaling would stretch any non-constant channel to touch exactly [0,1], making genuine sensor saturation undetectable after normalization. Fixed-range normalization keeps a real overexposed channel (every raw pixel at the dtype max) visibly saturated (~1.0 throughout), which is what `engine/validators/output/image.py`'s all-saturated check needs. `tests/sample_data/image_batch/saturated_channel.tiff` is a real fixture demonstrating this, not a synthetic tensor.

Both pipelines' input validators (`engine/validators/input/{structure,image}.py`) had to be added and registered in `registry.py` before either could flow through the existing Argo DAG at all — the `validate` step rejects any modality with no registered input validator, so without them every structure/image file would've been quarantined before ever reaching `transform`.

## Content-addressed caching (Phase 5)

The cache key is `(content_hash, representation_type, pipeline_version)`, exactly as BUILD_PLAN §10 specifies — checked in `engine/steps/transform.py` immediately after parsing (cheap) and before calling the featurizer (the potentially expensive part). `content_hash` is the sha256 of `pipeline.canonical_form_of(record)` (canonical SMILES / sequence / one-letter structure sequence / image-pixel hash, depending on modality) — the same value Phase 3/4 was already computing as `source_hash` on every `Feature` row; Phase 5's change is *using* it as a lookup key before computing, not just recording it after.

Each pipeline's `__init__.py` now exposes `REPRESENTATION_TYPES` (short job-parameter key -> full representation_type string, e.g. `"graph"` -> `"molecule_graph"`) and `FEATURIZERS` (public) alongside `canonical_form_of(record)` — `transform.py` needs to know a representation's full name and content hash *before* deciding whether to call its featurizer, which the old `pipeline.run()` one-shot API couldn't support (it always computed everything). `run()` itself is unchanged and still always computes — it's a plain library-style convenience API for direct/test use, not what `transform.py` calls anymore.

On a cache hit: `transform.py` still writes a new `Feature` row (linking this file to the result) but reuses the prior row's `location` — the same MinIO object is shared across every Feature row for that content, no rewrite — and appends `"cache_hit"` to `quality_checks_passed` so hits are distinguishable from fresh computation in the catalog. `GET /features?source_hash=&representation_type=&pipeline_version=` is the lookup query (`catalog/api.py`); as of Phase 6, `transform.py` reaches it via `FeatureRepository.find_cached()`, not `CatalogClient` directly (see below).

**Honest finding from verification**: for RDKit's molecule-graph featurization specifically, the compute being skipped (~31µs/molecule) is *cheaper* than the catalog HTTP round-trip the cache check costs (~2.5ms/lookup) — so at this point in the build, caching is structurally correct (100% hit rate on a re-run, zero redundant computation, zero redundant storage writes, verified directly) but not yet a wall-clock win for this specific featurizer. The mechanism is exactly what Phase 8's Ray/GPU-heavy steps (MSA generation, embedding extraction, image featurization) will need once that real compute cost exists — this phase is about the cache being *there and correct*, not about it already paying off on today's cheapest pipeline.

## Feature repository (Phase 6)

Went with the **thin custom Postgres catalog** BUILD_PLAN §2 offers as the alternative to Feast, not Feast itself — nothing about this single-user, single-node deployment has hit a reason to justify Feast's overhead (feature views, entities, materialization, an online store), and the `Feature` table already built in Phase 3 already *is* that thin catalog: pointers + metadata in Postgres, tensor bytes in MinIO, never mixed. Phase 6 formalized the read/write access to it into `engine/feature_repository/` (`client.py`: `FeatureRepository` + `FeatureRecord`; `cli.py`) rather than adding a new storage layer — matching the "write/read contract, Feast config or custom catalog" module BUILD_PLAN §9 names. Swapping to Feast later means changing this module's internals, not any of its callers.

`engine/steps/transform.py` is now the **only** writer of `Feature` rows, and it goes through `FeatureRepository` (`register()`, `find_cached()`), not `CatalogClient` directly — `CatalogClient` is still used for `File` status updates (a different table, not the feature repository's concern). Anything that wants to read produced features — the CLI, a future v3 model connector — should go through `FeatureRepository.query()` too, for the same reason: one contract, swappable backend.

```bash
# ad hoc queries (the CLI in BUILD_PLAN's "SDK / CLI / REST" consumer layer)
python -m engine.feature_repository.cli --representation-type molecule_graph --since 2026-07-29
python -m engine.feature_repository.cli --dataset-id my-dataset --dataset-version 2 --quality-status any
```

`FeatureRepository.query()` defaults to `quality_status="passed"` — pass `None` (or `--quality-status any` on the CLI) to include rejected features too, e.g. for auditing what got quarantined. `produced_since`/`produced_before` filter on `Feature.created_at` server-side (in the catalog's SQL query, not fetched-then-filtered in Python) so it scales the same way any other filter does. Pinning `dataset_id` + `dataset_version` together is how a caller gets a fully reproducible, versioned result set instead of "whatever's latest."

## Additional connectors + scheduling (Phase 7)

Added `connectors/chembl_connector.py` and `connectors/pubchem_connector.py` — two more public-DB connectors following the exact Phase 1 `Connector` interface, each with an argparse `main()` so they're invocable as `python -m connectors.<name>_connector` (an env var — `CHEMBL_IDS`/`PUBCHEM_CIDS` — or `--chembl-ids`/`--cids` controls the watchlist; there's no incremental "since last sync" state, just a fixed list re-fetched each run, matching `uniprot_connector`'s existing pattern rather than adding new complexity). `uniprot_connector.py` got the same `main()` treatment for consistency, though it isn't scheduled itself — no trigger has fired for it specifically. GEO/SRA/S3/FTP connectors from BUILD_PLAN §3's full list are not built — "two or more sources syncing on schedule" (the Phase 7 done-when) only needs two, and the rest follow this identical pattern whenever a real need for them shows up.

New: `connectors/Dockerfile` — connectors now run as pods inside the cluster, not just invoked from the host over port-forwards like Phase 1's manual testing did. Rebuild/reload after editing any connector:
```bash
docker build -f connectors/Dockerfile -t connectors:phase7 .
kind load docker-image connectors:phase7 --name ai-ready-data-platform
```

Scheduling uses Argo **CronWorkflow** (`workflows/argo/{chembl,pubchem}-sync-cronworkflow.yaml`), not a K8s CronJob — consistent with Argo already being the orchestrator for everything else. One non-obvious version gotcha: this cluster's Argo version (v4.0.8) uses `spec.schedules` (a list), not the older `spec.schedule` (singular) — the singular form is rejected with a strict-decoding error. Each CronWorkflow is a single-step `Workflow` (not a `WorkflowTemplate` DAG like ingest-validate) since a connector run isn't a multi-step pipeline; it still needs `serviceAccountName: argo-workflow` for the same emissary-executor RBAC reason documented under Phase 2.

```bash
kubectl -n data-platform get cronworkflows
kubectl -n data-platform get workflows -l workflows.argoproj.io/cron-workflow   # workflows a cron trigger created
```

**Scope boundary, stated explicitly so it isn't assumed done**: Phase 7's done-when is about connectors syncing unattended — proven by temporarily accelerating both schedules to `*/1 * * * *`, observing two consecutive autonomous firings (no manual `kubectl create`) each landing files, registering a new `dataset_version`, and emitting NATS events, then restoring the real nightly schedules (`0 2 * * *` / `15 2 * * *` UTC, offset so they don't collide). It does **not** cover the downstream pipeline: `engine/ingestion_service.py` is still a manually-invoked drain-and-exit batch (as noted since Phase 2) — a cron-landed file sits published-but-unconsumed on `PLATFORM_EVENTS` until someone runs it. Making the *whole* pipeline autonomous end-to-end would mean also scheduling the Ingestion Service (and giving its pod the RBAC to create Workflows, since it currently shells out to `kubectl create -f -`) — a reasonable next step, but not what this phase's stated criterion asked for.

## KubeRay / Ray integration (Phase 8)

**CPU-only by deliberate choice, not oversight.** This host has a real RTX 4090, but it's shared (was ~97% VRAM-occupied by another user's vLLM instance when this phase started) and Docker has no `nvidia-container-runtime` configured — wiring real GPU passthrough into `kind` needs sudo host changes plus a Docker restart that drops every running container, including this whole cluster. The user was asked and chose the CPU-only path. `infra/k8s-manifests/raycluster.yaml`'s header comment carries this reasoning — read it before "fixing" the cluster to add GPUs.

**GPU-readiness mechanism, and how it was actually verified (not just asserted):** `engine/ray_tasks.py`'s `@ray.remote(num_gpus=NUM_GPUS_PER_TASK)` tasks read `RAY_TASK_NUM_GPUS` (env var, default `"0"`) at import time rather than hardcoding a value — moving to a real GPU cluster means setting that env var and adding `nvidia.com/gpu` to the worker group's K8s resources (both marked `# GPU:` in `raycluster.yaml`), never touching task code. This claim was proven, not just asserted: setting `RAY_TASK_NUM_GPUS=1` on this 0-GPU cluster made a submitted task genuinely hang (Ray logged `No available node types can fulfill resource requests {'CPU': 1.0, 'GPU': 1.0}*1` and it timed out rather than running) — confirming Ray actually enforces the resource request rather than silently ignoring it — while the default `RAY_TASK_NUM_GPUS=0` completed normally with the exact same code.

**Three non-obvious KubeRay/Ray gotchas hit during setup, all now fixed in `raycluster.yaml`/`engine/Dockerfile` — worth knowing before touching either file again:**
1. KubeRay's default readiness/liveness probes shell out to `wget`, absent from the `python:3.12-slim` base image → pods stayed `0/1 Ready` forever with `wget: command not found` in events. Fixed by installing `wget` in `engine/Dockerfile`.
2. **Never set a custom `command`/`args` on the head/worker containers.** KubeRay generates its own `ray start ...` invocation from `rayStartParams` and *appends* it after any custom command with `&&` rather than replacing it — if your own command has `--block` (as `ray start` normally would), it never exits, so KubeRay's real (correctly configured) invocation never runs at all. Configure everything through `rayStartParams`, leave `command`/`args` unset.
3. Ray worker subprocesses don't inherit `/app` on `sys.path` the way `python -m engine...` does for the driver process — `@ray.remote` tasks failed with `ModuleNotFoundError: No module named 'engine'` until `PYTHONPATH=/app` was set explicitly as a container env var.

**How the batch driver actually connects, and why:** `engine/steps/ray_batch_transform.py` must be run via `kubectl exec` into the head pod (or eventually the Ray Jobs API), using `ray.init(address="auto")` to pick up the already-running local instance — **not** the Ray Client protocol (`ray://...:10001`) from a separate pod, which hit a reproducible gRPC crash in this environment (`Check failed: next_worker->state == KICKED` in the client proxy's epoll handling, a known class of gRPC fork/epoll issue). Also needed: `num-cpus: "0"` in the head's `rayStartParams`, or Ray schedules tasks on the head itself (it has spare CPU capacity by default) instead of spreading them across the worker pods — which defeats the entire point of proving multi-worker distribution.

```bash
# rebuild + reload after editing engine/ray_tasks.py, ray_batch_transform.py, or requirements.txt
docker build -f engine/Dockerfile -t engine-steps:phase2 .
kind load docker-image engine-steps:phase2 --name ai-ready-data-platform
kubectl -n data-platform delete pods -l ray.io/cluster=raycluster   # forces recreation with the reloaded image

kubectl -n data-platform get raycluster                              # DESIRED/AVAILABLE workers, total CPUS/GPUS
HEAD_POD=$(kubectl -n data-platform get pods -l ray.io/cluster=raycluster,ray.io/node-type=head -o jsonpath='{.items[0].metadata.name}')
kubectl -n data-platform exec "$HEAD_POD" -- ray status               # per-node resource breakdown

# run a batch (files must already be status=validated for the given dataset_id/version)
# MINIO_ACCESS_KEY/MINIO_SECRET_KEY required as of Phase 12 (no plaintext fallback default):
kubectl -n data-platform exec "$HEAD_POD" -- env \
  MINIO_ENDPOINT=http://minio.data-platform.svc.cluster.local:9000 \
  MINIO_ACCESS_KEY=$(kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootUser}' | base64 -d) \
  MINIO_SECRET_KEY=$(kubectl -n data-platform get secret minio-credentials -o jsonpath='{.data.rootPassword}' | base64 -d) \
  CATALOG_URL=http://catalog.data-platform.svc.cluster.local:8000 \
  python -m engine.steps.ray_batch_transform --dataset-id <id> --dataset-version <n>
```

Only `image_pipeline` is wired into the batch driver — it's the one of Phase 8's two named "heaviest steps" (BUILD_PLAN §10: MSA generation, image featurization) with a real, non-stubbed featurizer to actually batch. `engine/ray_tasks.generate_msa_remote` exists and is Ray-remote-wrapped, but MSA generation itself is still the Phase 3 stub (returns `None`) — there's nothing meaningful to batch-drive there yet. This Ray path is additive, not a replacement for the per-file Argo DAG (`ingest-validate` WorkflowTemplate): Argo still handles the standard per-file ingest→validate→transform flow triggered by connector events; Ray is specifically for distributing heavy batch compute, matching BUILD_PLAN's architecture diagram naming "Argo Workflows + Ray" together as the transform engine.

## Observability (Phase 9)

**Hybrid signal sourcing, deliberately** — not everything goes through Prometheus. Argo's workflow-controller and NATS's JetStream already expose real Prometheus metrics natively (no custom instrumentation needed for "pipeline success/failure" or "queue depth"), but "cache hit rate" and "per-source sync status" are fundamentally catalog questions — `Feature.quality_checks_passed` and `Dataset.source`/`created_at` already hold the answer in Postgres. Rather than build custom Prometheus counters/a Pushgateway just to duplicate what the catalog already records for ephemeral batch/Argo-step pods (which don't fit Prometheus's pull-scrape model well anyway), Grafana queries the catalog's Postgres database directly via SQL panels. Use the right tool per signal, not one tool for everything.

```bash
kubectl -n data-platform port-forward svc/kube-prometheus-stack-grafana 3000:80
# admin password lives in the `grafana-admin-credentials` Secret (Phase 12, BUILD_PLAN_COMMERCIAL.md):
kubectl -n data-platform get secret grafana-admin-credentials -o jsonpath='{.data.admin-password}' | base64 -d; echo
kubectl -n data-platform port-forward svc/kube-prometheus-stack-prometheus 9090:9090
kubectl -n data-platform get cronworkflows,podmonitor,raycluster                    # sanity-check what's being scraped
```

`observability/dashboards/platform-overview.json` is the source of truth for the one dashboard built so far (5 panels: Argo workflow phases, recently rejected files, feature cache hit rate, NATS queue depth for the `ingestion-service` consumer, per-source sync status). It's provisioned into Grafana via `infra/k8s-manifests/grafana-dashboard-platform-overview.yaml`, a *generated* ConfigMap wrapper — after editing the JSON, regenerate the YAML rather than hand-editing it (the exact command is in that file's header comment), then `kubectl apply` it. Same discovery pattern for datasources (label `grafana_datasource: "1"`, picked up the same way dashboards are via `grafana_dashboard: "1"`) — but as of Phase 12 (BUILD_PLAN_COMMERCIAL.md), `infra/k8s-manifests/grafana-postgres-datasource.template.yaml` is a **template**, not applied directly: Grafana's own datasource-provisioning format has no notion of a K8s secretKeyRef, so the Postgres password has to be inline in the YAML Grafana reads — the template gets rendered with the live password via `envsubst` and applied as a `Secret` (never a `ConfigMap`, never committed with a real value; the exact render+apply command is in the template's header comment). There's no equivalent file for Loki — `loki-stack`'s chart already auto-creates one (see gotcha #1 below); adding a second would just duplicate it.

**Three non-obvious issues hit during setup, worth knowing before re-touching this stack:**
1. `loki-stack`'s own chart auto-creates a Loki datasource ConfigMap (`loki-loki-stack`) with `isDefault: true`, which conflicts with kube-prometheus-stack's Prometheus datasource (also default) — Grafana's provisioning silently fails cluster-wide with "Only one datasource per organization can be marked as default" until one is fixed. Patched the chart-managed ConfigMap directly to `isDefault: false` (a manual, one-time fix — a future `helm upgrade loki` could revert it; there's no values.yaml knob for this in the chart).
2. Promtail (Loki's log shipper) crash-looped with "too many open files" — actually `fs.inotify.max_user_instances` (128, a low distro default), not a file-descriptor ulimit, exhausted by this shared host's many other containers. Fixed with `docker exec <kind-node> sysctl -w fs.inotify.max_user_instances=512` on each kind node directly (no host sudo needed — the kind node containers run as root in their own namespace). This doesn't persist across a node/`docker restart`; if promtail crash-loops again after one, re-run the sysctl.
3. Argo's controller serves its metrics port over HTTPS with a self-signed cert, not plain HTTP — the PodMonitor needs `scheme: https` + `tlsConfig.insecureSkipVerify: true`, or Prometheus gets "Client sent an HTTP request to an HTTPS server" (400) on every scrape.

**How the done-when was verified, and why it's genuine and not just "the panel exists":** every panel's query was run through Grafana's own `/api/ds/query` endpoint (the same path the UI uses), not just checked against raw Prometheus/Postgres — so "renders on the dashboard" is actually proven, not assumed. A deliberate Argo-level failure (a Workflow pointing `ingest` at a nonexistent MinIO object, genuinely crashing that step) pushed `argo_workflows_total_count{phase="Failed"}` up in real time and showed correctly in the phases panel. A genuinely invalid FASTA file, run through the real pipeline, was rejected by `validate.py` for real and appeared in the rejected-files table with its exact human-readable reason (`status_detail`), no log inspection needed. A batch of molecules that had never been seen before produced a real, measured 0% `cache_hit_rate_pct` for that dataset/time window. All artifacts from this verification were cleaned up afterward — the dashboard now reflects real ongoing platform activity, not leftover test noise.

## Backups (Phase 10)

`infra/backup/` is a small dedicated image (`postgres:18` base — matches the catalog's server version exactly, since pg_dump should be >= the server it dumps; note no `postgres:18-slim` tag existed at build time, `postgres:18` was used instead) with the `mc` CLI added, running `backup.sh`: `pg_dump --no-owner --no-acl` of the catalog uploaded to `backups/postgres/`, plus `mc mirror` of the `raw/` and `features/` zones into `backups/mirror/`. Scheduled nightly at 3am UTC via `workflows/argo/nightly-backup-cronworkflow.yaml`, after both connector CronWorkflows (2am/2:15am) so a night's newly-synced data is included. `--no-owner --no-acl` matters: without it, the dump includes `ALTER ... OWNER TO catalog` statements that fail on a fresh Postgres instance where that role doesn't exist yet — exactly the restore scenario this phase's done-when tests.

**"or off-box" per BUILD_PLAN §10 was not achievable here** — there's no second object store available in this environment, so `backups/mirror/` lives in the *same* MinIO instance as the data it's backing up. That protects against catalog-level mistakes (bad migration, accidental deletes) but not against losing the MinIO instance itself. Said explicitly rather than silently assumed away; a real off-box target (S3, a second cluster) is what closes this gap later.

**Locally-built images need an explicit `command:` in Argo container specs.** The emissary executor otherwise tries to look up the image's entrypoint from Docker Hub (`GET https://index.docker.io/v2/library/backup/manifests/phase10: UNAUTHORIZED`) — it doesn't know to check the local/kind image cache for images that were never pushed to a registry. Same fix as anywhere else this has come up: `command: ["/usr/local/bin/backup.sh"]` explicitly, don't rely on the image's own `ENTRYPOINT`.

**How the done-when was verified — genuinely fresh, standalone instances, zero involvement from the running platform:** downloaded the actual backup artifacts (the `.sql` dump, one feature's mirrored JSON) out of MinIO, then restored them into a plain `docker run postgres:18` container and a plain `docker run minio/minio` container — neither connected to the kind cluster or its StatefulSets in any way. The restored Postgres instance's `datasets`/`files`/`features` rows for a specific test dataset matched the live catalog exactly (same `feature_id`, same `location`, same everything). The restored MinIO object was byte-for-byte identical to the original and parsed back into the correct, valid tensor JSON. Both fresh containers were torn down afterward — this was a one-time verification drill, not new standing infrastructure.

## Tests + docs (Phase 11 — ongoing)

```bash
python -m pytest              # unit tests: fast, no cluster, no network (HTTP mocked)
python -m pytest -m integration  # needs the live cluster — port-forward commands in that test file's docstring
```

`tests/unit/` covers all four pipelines (adapters, canonical conversion, featurize, including the SMILES/SDF-equivalence and corrupted-output cases that were originally just manual verification steps in Phases 3-4, now codified so they don't silently regress), both validator gates (input + output, one good/bad case per modality), and all four connectors (`local` for real against `tests/sample_data/`, `uniprot`/`chembl`/`pubchem` with `requests.get` mocked via `unittest.mock.patch` — no `requests-mock` dependency added, stdlib was enough). `tests/integration/test_end_to_end.py` lands a real mixed-modality (`molecule`+`sequence`), multi-source (`local`+`uniprot`, including one genuine UniProt API call) batch through the actual Argo DAG and asserts every produced `Feature` matches the BUILD_PLAN §11 schema field-by-field — then cleans up its own catalog/MinIO footprint.

**Writing the unit tests immediately found a real, previously-unnoticed bug**: `engine/pipelines/sequence_pipeline/adapters/uniprot_xml.py` used `entry.find("up:accession", NS) or entry.find("accession")` to fall back when a namespace prefix doesn't match — but `xml.etree.ElementTree.Element.__bool__` is `len(elem) > 0` (child-element count), not "was this found." A leaf element like `<accession>P12345</accession>` has no children, so it's falsy even on a *successful* find, and the `or` silently fell through to the (also-failing) fallback, returning `"unknown"` instead of the real accession. Fixed with explicit `is None` checks (see `_find_with_fallback`). This adapter was never exercised by the real UniProt connector (which fetches FASTA, not XML) so nothing in production was ever wrong — but it's exactly the kind of bug that would have shipped silently wrong the day something *did* call it. Take this as the argument for why "ongoing" in this phase's name is load-bearing: more tests will keep finding more of these.

`README.md` (repo root) is the human-facing counterpart to this file — the output contract and catalog schema in prose, meant for someone picking up the project or building a v3 model connector, without needing every phase's operational gotchas. Keep the two in sync but distinct: README explains *what* and *why* the contract is; this file explains *how* to operate and debug the thing that implements it.

## Secrets remediation (Phase 12 — BUILD_PLAN_COMMERCIAL.md)

MinIO root credentials, both Postgres passwords (`catalog` user + `postgres` superuser), and the Grafana admin password were all committed in plaintext in `infra/helm/*.yaml` (and, for Postgres, duplicated again in `infra/k8s-manifests/catalog.yaml`'s `DATABASE_URL` and hand-baked into `catalog/db.py`'s fallback default). All four are now real, randomly generated values living only in Kubernetes Secrets (`minio-credentials`, `postgres-credentials`, `grafana-admin-credentials`), created imperatively — never committed — the same pattern `PUBLIC_API_KEY` already used correctly. Rotated live against the running cluster (MinIO/Grafana pick up new env on restart; Postgres needed an explicit `ALTER USER ... WITH PASSWORD ...` since the chart only sets a password at first `initdb`) and verified end-to-end afterward: a real file pushed through the actual ingest→validate→transform DAG, a real Grafana datasource health check, a real Argo-submitted connector sync — not just "the pods came back up."

**The plaintext-credential fallback defaults are gone on purpose** — `connectors/storage.py`, `catalog/public_api.py`, and `catalog/db.py` now raise immediately if `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`/`DATABASE_URL` aren't set, rather than silently defaulting to a known value. Every Argo workflow template/CronWorkflow that reads MinIO credentials now wires them from `minio-credentials` via `secretKeyRef` — including `ingest-validate-template.yaml`'s three steps and the `chembl`/`pubchem` CronWorkflows, which previously had *no* MinIO credential env at all and only worked because of the fallback default. If you add a new Argo template or CronWorkflow that touches MinIO, it needs this wiring explicitly — there's no safety net anymore. `tests/conftest.py` sets dummy `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` values for the unit test suite (which never touches real MinIO — HTTP/S3 calls are mocked), so `python -m pytest` doesn't need real credentials.

**One credential couldn't be wired via `secretKeyRef` at all**: Grafana's own datasource-provisioning file format has no notion of a Kubernetes secret reference — the Postgres password has to be inline in the YAML Grafana reads. `infra/k8s-manifests/grafana-postgres-datasource.template.yaml` is therefore a template (`${POSTGRES_CATALOG_PASSWORD}` placeholder), rendered with `envsubst` and applied as a `Secret` (not a `ConfigMap` — the datasource sidecar watches both, per its `RESOURCE: "both"` setting) rather than ever committing the real value; the exact render+apply command is in the file's own header comment.

**`gitleaks detect --source . --log-opts="--all"` was run against the full commit history and found nothing** — worth knowing what that does and doesn't prove: it confirms no *high-entropy* secret (a real API key, token, or certificate) was ever accidentally committed. It does **not** catch low-entropy, human-guessable default passwords like `minioadmin`/`catalog`/`admin` — those don't match gitleaks' default detection rules at all, which is exactly why this phase needed a manual, targeted grep across every values/manifest file rather than trusting a scanner alone. `.pre-commit-config.yaml` adds gitleaks as an install-time hook (`pip install pre-commit && pre-commit install`) going forward, with the same caveat: it's a real, useful guard against a genuinely random secret landing in a diff, not a substitute for reviewing what a new low-entropy default value might be before committing it.

**LICENSE**: still an open decision, deliberately not resolved this phase — the company hasn't specified proprietary vs. another arrangement yet. Don't assume a default (MIT, Apache, etc.) without asking; it's a legal call, not an engineering one.

## Per-customer authentication (Phase 13 — BUILD_PLAN_COMMERCIAL.md)

`/public/*` no longer trusts one shared `PUBLIC_API_KEY` — it looks up the incoming `X-API-Key` against a new `api_keys` table (`catalog/models.py`'s `ApiKey`), so every request is traceable to a specific tenant and individually revocable. Key format is `plat_<key_id>_<secret>` (`catalog/api_key_auth.py`): `key_id` is a public lookup handle, `secret` is hashed (SHA-256 — these are 256-bit random tokens, not human passwords, so a fast hash is correct, not bcrypt) and never stored raw. Issue/revoke/list keys via `catalog/manage_api_keys.py`, run inside the catalog pod (deliberately a CLI, not a public endpoint — self-service issuance is an explicitly deferred trigger):

```bash
kubectl -n data-platform exec deploy/catalog -- python -m catalog.manage_api_keys create --tenant-id <name> --label "<what this key is for>"
kubectl -n data-platform exec deploy/catalog -- python -m catalog.manage_api_keys revoke --key-id <key_id>
kubectl -n data-platform exec deploy/catalog -- python -m catalog.manage_api_keys list
```

Each key has scopes (`read`/`write`, `--scopes` flag, default both) — write endpoints (`upload`, `upload-batch`, `pull/*`) require `Depends(require_scope("write"))`; a read-only key gets a 403, not a silent downgrade. Rate limiting is now two independent checks (`_check_submission_rate_limits`): per-IP (unchanged, matters when many visitors share one tenant's key, e.g. the public demo) and per-`tenant_id` (new — so no single tenant can exceed the aggregate ceiling regardless of how many IPs it's called from). `Dataset.owner` is now the authenticated tenant_id, not the `source` label (`source` still means "how" — geo/s3/public-upload/...; `owner` now means "who") — this is what makes a Dataset traceable to a real caller ahead of Phase 14's `tenant_id` schema column.

**Internal catalog API (`catalog/api.py`) also went from zero authentication to a shared `INTERNAL_API_SECRET`** (`X-Internal-Secret` header, checked by `require_internal_secret`, applied to a new `internal_router` wrapping every `/datasets`, `/files`, `/features`, `/pipelines`, `/jobs` route — `/health` stays open for the K8s probe). `connectors/catalog_client.py`'s `CatalogClient` is the single choke point every internal caller (every connector, every `engine/steps/*.py`, `engine/feature_repository/`) goes through, so it's the only place that needed to change — it now uses a `requests.Session` with the header pre-set rather than bare `requests.get/post/patch`. **Non-obvious gotcha**: `app.include_router(internal_router)` has to happen *after* every `@internal_router...` decorator in the file has run (moved to the bottom of `catalog/api.py`, with a comment explaining why) — including it right after `internal_router = APIRouter(...)` at the top, before any routes exist on it, would have silently registered zero internal endpoints on some FastAPI versions.

Every Argo workflow/CronWorkflow that touches the catalog now also carries `INTERNAL_API_SECRET` via `secretKeyRef` (same three ingest-validate steps and two connector CronWorkflows Phase 12 already had to touch for MinIO creds). `tests/conftest.py` sets a dummy `INTERNAL_API_SECRET` for the unit suite — `Connector.run()` (the only code path that would actually use it) is never called by unit tests, only by the live-cluster integration test.

**Cutting over broke the live public site for a few minutes, as expected**: the moment the old `PUBLIC_API_KEY` check was replaced, the Vercel-hosted frontend's `CATALOG_API_KEY` env var (still holding the old static value) started getting 401s. No Vercel CLI/token is available from this environment, so the production env var had to be updated by hand (`.env.local` was updated automatically for local dev) — done, and verified afterward with a real page load and a real end-to-end upload through the live production deployment, not just "the env var is set now." If this ever needs rotating again: issue a new `public-demo`-tenant key, update the Vercel env var, redeploy, verify with a real request before considering it done.

An AI-ready bulk data platform for drug-discovery data: raw multi-modal inputs (sequences, small molecules, structures, protein-ligand complexes, microscopy images, tabular assay data) go in; validated, canonicalized, model-ready tensors come out, versioned in a feature repository with a stable output contract. It is built as decoupled services (connectors → event bus → metadata catalog → processing engine → feature repository), not a single linear script, so new data sources/modalities/scale don't require rearchitecting.

Explicitly out of scope for this build: model training, fine-tuning, and inference serving. The platform stops once data is sitting in the feature repository, versioned and queryable.

This is a dry-lab-only computational CRO (no wet lab), so connectors cover public databases, cloud storage, and manual/local uploads — no LIMS/ELN/instrument connectors are built, though the connector interface is designed so those could be added later without touching the rest of the system.

## Architecture (target — see `docs/BUILD_PLAN.md` for full detail)

Data flow: `Connector Framework → NATS (event bus) → Ingestion → Metadata Catalog → Validation → Standardization → Modality Router → Distributed Transform Engine (Argo + Ray) → Content-Addressed Cache → Output Validation → Data Lake zones → Feature Repository & Catalog API → SDK/CLI/REST`

Everything runs as Kubernetes pods on a local `kind` cluster from day one, so nothing needs rewriting for a real multi-node cluster later.

**Core design rules that shape every component:**

- **Metadata catalog (Postgres + FastAPI) is the source of truth.** Every component queries it instead of inspecting files directly. Adding a concern means adding a table, not a new service.
- **Dataset manifests are immutable** — a change is always a new `dataset_version`, never an in-place edit (same idea as an immutable Docker tag or git commit). `dataset_hash` is computed from the manifest, not just per-file checksums, which is what makes the content-addressed cache trustworthy at the dataset level.
- **Modality routing is config-driven** via catalog lookup, not hardcoded if/else logic on file extensions.
- **Format adapters, not per-format pipelines.** Each modality pipeline (e.g. `sequence_pipeline`) has one canonical internal record type (e.g. `SequenceRecord`) and a thin adapter per input format (FASTA, UniProt XML/JSON, GenBank, ...) that converts into it. Featurization code is written once against the canonical shape; adding a new input format means writing one adapter function, never touching featurization or the router.
- **A pipeline can emit multiple output representations per record** (e.g. a molecule as both a graph batch and a token sequence), tagged with `representation_type` and sharing `source_hash`/`dataset_id`. Which representations get generated is a job parameter (`representations: [...]`), not something hardcoded — default is the primary representation for that modality; more is opt-in.
- **Two separate quality gates, not one.** Input validation (Pydantic schemas) runs cheaply before any compute is spent. Output validation (per-modality tensor sanity checks — NaN/Inf coords, round-trip SMILES, shape checks, etc., see BUILD_PLAN §6) runs after transform, before the feature lands in the repository — a pipeline exiting cleanly does not imply the tensor is trustworthy. Anything failing either gate is written to `rejected/` with a logged reason, never silently dropped.
- **Every feature output conforms to one of exactly four container types** (BUILD_PLAN §11): token-ID sequence, graph batch, dense image tensor `[C,H,W]`, or SE(3) frame tensor. This is the contract future model-connector code (v3, out of scope here) is written against — don't introduce a fifth ad hoc shape.
- **New connectors/pipeline modules register via Python entry points**, not by editing a central `register_pipeline()` call.
- Reproducibility for any feature is a join across catalog tables (raw dataset hash → pipeline version → container digest → git commit → config version → feature hash → timestamp) — there is deliberately no separate provenance/lineage service in this build.

## Data lake zones (MinIO)

`landing/` (untouched fetch) → `raw/{source}/` → `validated/` (passed input QC) → `standardized/` (canonicalized) → `features/` (final tensors) → `archive/` / `rejected/`. `landing/ → archive/` moves automatically via a MinIO lifecycle policy past a retention window.

## Technology choices (don't substitute without reason — see BUILD_PLAN §2 for rationale)

Cluster: `kind`. Orchestration: Argo Workflows. Event bus: NATS JetStream (with a dead-letter subject configured from Phase 0, not added later). GPU-heavy featurization: Ray + KubeRay. Object storage: MinIO. Table format: Apache Iceberg via PyIceberg. Tensor cache: Zarr. Feature repository: Feast, or a thin custom Postgres/Parquet catalog if Feast's overhead isn't justified. Validation: Pydantic (+ optional Great Expectations). Observability: Prometheus + Grafana + Loki. Molecule/protein libs: RDKit, Biopython, BioPandas. Packaging: Docker + Helm.

## Deferred components — do not build speculatively

Kueue, a dedicated Job Service, the Kubeflow Spark Operator, OpenLineage, Keycloak/IAM, multi-tenancy, LIMS/ELN/instrument connectors, and Kafka are all intentionally deferred (BUILD_PLAN §8 has the trigger condition for each — e.g. Kueue only once 2+ concurrent jobs are actually competing for the single GPU). If a task seems to call for one of these, check whether its trigger has actually fired before adding it.

## Build order

Follow the phase sequence in BUILD_PLAN §10 (cluster foundation → metadata catalog/connectors → ingest/validate → molecule+sequence pipelines → structure+image pipelines → caching → feature repository → additional connectors → KubeRay → observability → backups → tests/docs). Each phase has an explicit "done when" condition — treat that as the acceptance check before moving to the next phase.

**v2's phases (0–11) are done.** `docs/BUILD_PLAN_COMMERCIAL.md`'s Phase 12 (secrets) and Phase 13 (per-customer auth) are also done; Phases 14–22 are not yet started. Follow that document's own suggested order (14 → 16 → 17 → 18 → 19 → 15 → 20 → 21 → 22) the same way v2's phases were followed in sequence — each of its phases also has an explicit "done when" acceptance check.
