# Build Plan — AI-Ready Bulk Data Platform (v2, Industrial-Grade)

**v2 is complete** — every phase below was built and its "done when" verified (see `CLAUDE.md`'s phase-by-phase notes). v2 was explicitly scoped for a single internal operator (see §2/§8's deferral of Keycloak, multi-tenancy, IAM). Now that this platform is being used commercially by a company for its (potentially multiple) customers, that trigger has fired — **[`BUILD_PLAN_COMMERCIAL.md`](BUILD_PLAN_COMMERCIAL.md)** is the v3 plan that closes the resulting gaps (auth, tenancy, secrets, HA, compliance) phase by phase, without changing the architecture below.

**Scope of v2**: same end goal as v1 — raw multi-modal drug-discovery data in, validated/canonicalized/featurized model-ready tensors out, sitting in a feature store with a stable output contract. What changes is *how it's built*: as a platform of decoupled services (connectors, event bus, metadata catalog, dataset registry, processing engine, feature repository) instead of a single linear pipeline, so it can absorb new data sources, new modalities, and new scale without rearchitecting.

**Still explicitly out of scope**: model training, fine-tuning loops, inference serving. v2 stops at "data is sitting in the feature repository, versioned and queryable, ready to be pulled." That boundary hasn't moved.

**One adaptation from the generic industrial pattern**: this is a dry-lab-only computational CRO — no wet lab, no in-house instruments. So the connector layer below includes the public-database, cloud-storage, and local/manual-upload connectors, and *omits* LIMS/ELN/sequencer/microscope connectors that a wet-lab org would need. The connector framework is built so those slot in later without changes elsewhere, if a client engagement ever requires ingesting from their lab systems — but nothing is built for them speculatively.

---

## 1. Platform architecture

```
                External Data Sources
        ┌───────────────┬────────────────┐
    Public DBs      Cloud Storage      Local/Manual
  (UniProt, PDB,    (S3/MinIO/FTP)    (analyst-provided
  ChEMBL, PubChem,                     files, one-off
  GEO, SRA)                            batches)
        └───────────────┼────────────────┘
                         ▼
                Connector Framework
                         ▼
                  Event Bus (NATS)
                         ▼
                 Ingestion Service
                         ▼
                 Metadata Catalog
                         ▼
                Validation Engine
                         ▼
                Standardization
              (canonicalize before
                    routing)
                         ▼
                 Modality Router
              (config-driven lookup
               in the catalog, not
                  hardcoded logic)
                         ▼
          Distributed Transform Engine
             (Argo Workflows + Ray)
                         ▼
          Content-Addressed Cache
                         ▼
                Output Validation
             (per-modality sanity
              checks on tensors,
              not just raw input)
                         ▼
       Data Lake (Landing → Raw → Validated →
              Standardized → Features)
                         ▼
        Feature Repository & Catalog API
                         ▼
        SDK / CLI / REST — ready for
           v3 model connectors
```

Everything runs as Kubernetes pods via a local `kind` cluster from day one, so nothing needs rewriting when this moves to a real multi-node cluster.

---

## 2. Technology selections

| Layer | Technology | Why this one |
|---|---|---|
| Cluster substrate | **kind** | Real K8s API locally, zero cost |
| Pipeline orchestration | **Argo Workflows** | K8s-native DAG engine; same tool used under Kubeflow Pipelines in production |
| Event bus | **NATS (JetStream)** | Lighter-weight than Kafka for a single-node deploy; same pub/sub + persistence semantics, trivial to swap for Kafka later if throughput demands it |
| GPU-heavy featurization | **Ray + KubeRay operator** | Handles MSA generation, embedding extraction, image featurization; scales unchanged to a real GPU pool later |
| Bulk tabular ETL | **Kubeflow Spark Operator** (deferred — see §8) | Declarative `SparkApplication` CRD |
| Object storage | **MinIO** | S3-compatible, same API as real AWS S3 |
| Table format | **Apache Iceberg** via PyIceberg over Parquet | ACID + schema evolution + time travel |
| Array/tensor cache | **Zarr** | Chunked, cloud-native storage for MSA tensors, image stacks, embeddings |
| Feature repository | **Feast** (metadata layer only), or a thin custom Postgres/Parquet catalog if Feast overhead isn't justified yet | Queryable, versioned feature retrieval — the "any model can connect" contract. Metadata (Postgres) and tensor data (Zarr/Parquet/Iceberg) are kept separate — scientific tensors are large, and you don't want Feast (or its replacement) holding bytes, only pointers + stats + schema |
| Metadata catalog | **Postgres** + a thin FastAPI service | Dataset/file/pipeline/status registry — the source of truth every component queries |
| Dataset registry | Same Postgres instance, separate schema | Versioned manifests instead of individual files |
| GPU quota/scheduling | **Kueue** (deferred — see §8) | Quota-based scheduling |
| Data validation | **Pydantic** schemas + **Great Expectations** (optional) | Schema gate before expensive compute |
| Lineage | **OpenLineage** (deferred — see §8) | Which raw file produced which feature |
| Observability | **Prometheus + Grafana + Loki** | Metrics, dashboards, logs from day one, minimal setup cost |
| Auth | **Keycloak** (deferred — see §8) | Full IAM/RBAC only once there's more than one user |
| Molecule/protein libs | **RDKit**, **Biopython**, **BioPandas** | Canonicalization, sequence parsing, structure parsing |
| Container/package mgmt | **Docker** + **Helm** | Standard packaging for every operator above |

---

## 3. Connector framework

Replaces a bare "Ingest" step with a pluggable interface every source implements the same way:

```
connectors/
├── base.py              # Connector ABC: discover() / fetch() / validate() / emit_event()
├── uniprot_connector.py
├── pdb_connector.py
├── chembl_connector.py
├── pubchem_connector.py
├── geo_connector.py
├── sra_connector.py
├── s3_connector.py
├── ftp_connector.py
└── local_connector.py   # manual/analyst-provided batches
```

Each connector runs on a schedule (cron, hourly/nightly sync against public DBs) or reacts to an event (new object landed in a watched bucket path). On new data it emits an event to NATS rather than calling the pipeline directly — this is what gives retry, buffering, and audit for free, and what lets a `lims_connector` or `microscope_connector` be added later without touching anything downstream.

New connectors and new pipeline modules register via Python **entry points** (`pip install some-plugin` → the platform discovers and registers it) rather than a hardcoded `register_pipeline()` call edited into core code. Same effect as a "plugin SDK," at the cost of a packaging convention instead of a new service.

NATS JetStream is configured with a **dead-letter subject** from Phase 0 on: an event that fails processing retries with backoff, and after N failures lands in a DLQ subject instead of silently vanishing. This is a config flag, not extra infrastructure.

---

## 4. Data-type coverage matrix

| Raw input type | File formats accepted | Preprocessing module | Representations it can produce |
|---|---|---|---|
| Protein/RNA/DNA sequence | FASTA, UniProt XML/JSON, GenBank | `sequence_pipeline` — validate alphabet, tokenize, optional MSA (MMseqs2) | Token ID tensor `[seq_len]` + optional MSA tensor |
| Small molecule | SMILES, SDF, InChI, Mol2, CSV of SMILES+labels | `molecule_pipeline` — RDKit canonicalize, sanitize, build molecular graph | Graph batch **and/or** canonical SMILES token sequence — a molecule can produce both, since Chemprop wants a graph and a SMILES-LM wants tokens |
| Protein/complex structure | PDB, mmCIF, PDBx | `structure_pipeline` — parse backbone/all-atom coords, strip waters, build residue graph or SE(3) frame tensor | Graph tensor **and/or** SE(3) frame tensor `[N_res, 3x3+3]` |
| Protein–ligand complex | PDB/mmCIF + SMILES, or Boltz-style YAML | `complex_pipeline` — merge structure + ligand conformer (RDKit ETKDG) | Unified atom-token tensor |
| Cellular microscopy images | TIFF, OME-TIFF (multi-channel Cell Painting) | `image_pipeline` — illumination correction, segmentation/crop, normalization, patchify | Image tensor `[C, H, W]` float32 |
| Assay/tabular data | CSV, TSV, Parquet, XLSX | `tabular_pipeline` — dedupe, unit normalization, outlier flagging, z-score/log-transform | Cleaned feature table (Parquet/Iceberg) |
| Text/instructions (task metadata) | plain text, JSON | `text_pipeline` — tokenize with target tokenizer | Token ID tensor `[seq_len]` |

The **modality router** inspects each incoming file/record (via the Metadata Catalog, not raw file sniffing alone) and dispatches to the correct pipeline — the single piece of logic that makes the engine "handle all raw data types."

### 4a. Multiple input formats — format adapters, not per-format pipelines

Same modality, different file formats, arrives constantly: a UniProt entry and a plain FASTA are both "a protein sequence," but they're structured completely differently on disk. The fix is **not** a separate pipeline per format — that multiplies maintenance for zero benefit, since the featurization logic downstream is identical either way. Instead, each pipeline gets a thin adapter layer in front of it:

```
engine/pipelines/sequence_pipeline/
├── adapters/
│   ├── fasta.py          # FASTA → canonical SequenceRecord
│   ├── uniprot_xml.py    # UniProt XML → canonical SequenceRecord
│   ├── uniprot_json.py   # UniProt JSON → canonical SequenceRecord
│   └── genbank.py        # GenBank → canonical SequenceRecord
├── canonical.py           # the one shape everything downstream operates on
└── featurize.py           # canonical SequenceRecord → representations (below)
```

Adding a new input format (say, a client hands you a SwissProt flat file) means writing one adapter function, not touching the featurization code or the router. The Metadata Catalog records which adapter handled a given file, so "why did this record look different" is always answerable.

### 4b. Multiple/selective output representations

The v1 rule was "every pipeline terminates in exactly one container type." That was too rigid — as the table above shows, a molecule or a structure often has more than one valid representation, and different downstream models want different ones. Two changes:

- **A pipeline can emit more than one representation per record.** `molecule_pipeline` can write both a graph batch and a token sequence for the same molecule; each is stored as its own feature record, tagged with `representation_type` and `model_compatibility_tags`, sharing the same `source_hash`/`dataset_id` so they're clearly linked as different views of the same input.
- **Which representations get generated is a job parameter, not hardcoded.** A batch run can request `representations: [graph]` if that's all that's needed right now, instead of always paying the compute cost for every possible representation. Default is "generate the primary representation for that modality"; requesting more is opt-in.

The Feature Catalog API (§11) filters by `representation_type` on retrieval too — "give me this dataset's molecules as graphs" vs "as token sequences" is a query parameter, not two different storage layouts to know about.

---

## 5. Data lake zones

```
MinIO
├── landing/          # exactly what the connector fetched, untouched
├── raw/
│   ├── uniprot/
│   ├── chembl/
│   ├── pdb/
│   └── ...
├── validated/         # passed schema/QC gate
├── standardized/       # canonicalized, pre-tensor
├── features/          # final model-ready tensors (Zarr/Parquet/Iceberg)
├── archive/
└── rejected/          # quarantined, with a logged reason
```

MinIO lifecycle rules move objects `landing/ → archive/` automatically past a retention window, so storage doesn't grow unbounded by default — a bucket policy, not a service.

---

## 6. Data quality — input and output

Two separate gates, not one. Passing the first doesn't guarantee the second.

**Input validation** (already covered — Phase 2, `validate` step): schema/type checks against the raw file before any compute is spent. Is this a well-formed FASTA? Does this SMILES string parse at all? Right alphabet, right column types. Cheap, fast, catches malformed input before it wastes GPU time.

**Output validation** (the gap — new step, sits after Transform/Cache and before Load into the feature repository): a pipeline can run without erroring and still produce a bad tensor. Input validation can't catch this because the input was fine — the failure is in the featurization step itself. Per-modality checks, cheap to write, expensive to skip:

| Modality | Output checks |
|---|---|
| Sequence tokens | No unexpected tokens/padding overrun; length matches source; MSA depth above a minimum threshold if MSA was requested |
| Molecule graph | Round-trips: canonical SMILES re-parses to the same graph; no orphan/disconnected nodes; atom/bond feature ranges sane |
| Structure tensor | No NaN/Inf coordinates; bond lengths and angles within physically plausible ranges; residue count matches source structure |
| Image tensor | No all-zero or all-saturated channels post-normalization; expected `[C, H, W]` shape; not a corrupted/truncated read |
| Tabular feature table | No unexpected nulls post-cleaning; z-scored columns actually centered; no duplicate rows slipped through dedup |

Each check is cheap (assertions on the tensor, not a model) and runs once per feature, right before it's written. A feature that fails is written to `rejected/` with the failure reason, same as a bad input file — it never reaches the feature repository. The catalog's feature record carries a `quality_status` + `quality_checks_passed` field so a bad feature that somehow slipped through is traceable, not just silently wrong.

This is the piece that stops "the pipeline ran successfully" from being mistaken for "the output is trustworthy" — those are different claims, and only the input-side check was covering the first one.

---

## 7. Metadata catalog & dataset registry

Every component queries this instead of inspecting files directly. Schema grows in place (adding tables costs nothing) rather than spinning up separate services for each concern.

**File-level record**: `file_id, dataset_id, source, checksum, modality, pipeline_version, container_digest, git_commit, status, location, created_at`

**Dataset-level record**: `dataset_id, dataset_version, dataset_hash, owner, source, license, manifest, schema_version, created_at`

**Pipeline record**: `pipeline_name, pipeline_version (semver — e.g. sequence_pipeline:1.2.0), config_version, git_commit, container_digest`

**Job record** (a lightweight table, not a separate service — see §8): `job_id, dataset_id, pipeline_version, requested_representations, status, content_hash, started_at, finished_at`

Two rules that matter more than the schema itself:

- **Dataset manifests are immutable.** Never edit a dataset in place — a change is always a new `dataset_version`, the same way a Docker image tag or a Git commit is never mutated. This is what makes "rerun this exact analysis in a year" possible.
- **`dataset_hash` is computed from the manifest**, not just per-file checksums. Same hash means same dataset, which is what makes the content-addressed cache (§ Phase 5) trustworthy at the dataset level, not just the file level.

Together, a feature's full **reproducibility manifest** is just a join across these tables: raw dataset hash → pipeline version → container digest → git commit → config version → feature hash → timestamp. No separate provenance service needed — it's a query, not new infrastructure.

This is what makes re-runs reproducible and answers "what produced this feature" without spelunking through storage.

---

## 8. What's deferred, and the trigger to add it

Kept in the design so nothing needs rearchitecting, but not built in v2 unless the trigger fires:

| Component | Deferred because | Add it when |
|---|---|---|
| Kueue (GPU quota scheduling) | One GPU, no contention yet | You're regularly running 2+ concurrent jobs competing for the 4090 |
| Dedicated Job Service (priority/dedup/cancellation between NATS and Argo) | Same root cause as Kueue — no concurrent job contention to manage. Argo's native retry + a catalog idempotency check (skip if `content_hash + pipeline_version` already processed) covers dedup and retry for free | Same trigger as Kueue — once job contention is real, a proper scheduler replaces both at once |
| Kubeflow Spark Operator | Tabular volume is small | A single tabular batch exceeds what Pandas/DuckDB handles comfortably |
| OpenLineage | Metadata catalog already gives basic provenance | You need cross-pipeline lineage graphs, not just "what produced this file" |
| Keycloak / full IAM-RBAC | Single user | A second person or external org gets access to the same instance |
| Multi-tenancy (org/project isolation) | Single tenant | You onboard a client onto the same running instance rather than a separate deploy |
| LIMS / ELN / instrument connectors | No wet lab | A client engagement requires ingesting directly from their lab systems |
| Kafka (vs. NATS) | NATS JetStream covers current throughput | Event volume or multi-consumer fan-out outgrows NATS in practice |

---

## 9. Repository structure

```
data-platform/
├── infra/
│   ├── kind-cluster.yaml
│   ├── helm/                # Argo, KubeRay, MinIO, NATS, Prometheus/Grafana charts
│   └── k8s-manifests/
├── connectors/
│   ├── base.py
│   └── ...                  # one file per source, see §3
├── catalog/
│   ├── models.py             # Postgres schema: files, datasets, pipelines, status
│   └── api.py                 # FastAPI service every component queries
├── engine/
│   ├── router/                # modality detection + dispatch, queries catalog
│   ├── validators/
│   │   ├── input/                # Pydantic schemas per raw data type (Phase 2 gate)
│   │   └── output/                # per-modality tensor sanity checks (§6 table, Phase 3-4 gate)
│   ├── pipelines/
│   │   ├── sequence_pipeline.py
│   │   ├── molecule_pipeline.py
│   │   ├── structure_pipeline.py
│   │   ├── complex_pipeline.py
│   │   ├── image_pipeline.py
│   │   └── tabular_pipeline.py
│   ├── caching/                # content-hash cache layer
│   └── feature_repository/      # write/read contract, Feast config or custom catalog
├── workflows/
│   └── argo/                    # DAG YAMLs: ingest → validate → transform → load
├── observability/
│   └── dashboards/               # Grafana dashboard defs
├── tests/
│   └── sample_data/
└── README.md
```

---

## 10. Build phases

### Phase 0 — Cluster foundation (0.5–1 day)
- Install Docker, `kind`, `kubectl`, `helm`
- `kind create cluster` (3 nodes)
- Deploy MinIO + NATS via Helm; verify read/write and pub/sub from a pod
- **Done when**: `kubectl get nodes` works, a test file round-trips through MinIO, a test message round-trips through NATS

### Phase 1 — Metadata catalog + connector framework (1–2 days)
- Stand up Postgres + the FastAPI catalog service (file/dataset schema from §7)
- Build `base.py` connector interface and the `local_connector` (manual batches) + one public-DB connector (start with UniProt or PDB)
- **Done when**: running the connector registers a dataset + files in the catalog and emits a NATS event per new file

### Phase 2 — Ingest + Validate (1–2 days)
- Argo Workflow: `ingest` step lands files from `landing/` to `raw/{source}/`, `validate` step runs Pydantic schema checks against the catalog record, quarantines failures to `rejected/`
- **Done when**: a mixed batch of good+bad sample files runs through, bad ones quarantined with a logged reason, catalog status updated for each

### Phase 3 — Transform: molecule + sequence pipelines (2–3 days)
- Implement `molecule_pipeline` (RDKit canonicalization + graph builder) with format adapters for SMILES, SDF, InChI, Mol2 (§4a) and support for emitting both graph and token-sequence representations (§4b)
- Implement `sequence_pipeline` (validation + tokenization; MSA generation stubbed initially) with format adapters for FASTA, UniProt XML/JSON, GenBank (§4a)
- Add each pipeline's **output validation** checks (§6 table) as the last step before the tensor is cached
- Wire as Argo steps or Ray remote tasks
- **Done when**: the same molecule fed in as SMILES and as SDF produces an identical canonical record; a batch of SMILES and FASTA files produce correct canonical/tokenized outputs, verified against known test cases; requesting only `representations: [graph]` skips token-sequence generation; and a deliberately corrupted output (e.g. an all-zero graph) is caught and quarantined by the output check

### Phase 4 — Transform: structure + image pipelines (2–3 days)
- Implement `structure_pipeline` (Biopython/BioPandas parsing, backbone extraction)
- Implement `image_pipeline` (normalization, segmentation stub)
- Add output validation checks (§6 table) for both
- **Done when**: PDB files produce structure tensors, TIFF stacks produce normalized image tensors, and a NaN-coordinate or all-saturated test case is caught before reaching cache/storage

### Phase 5 — Content-addressed caching (1–2 days)
- Key cache by `(content_hash, feature_type, pipeline_version)`, check before every pipeline run
- **Done when**: re-running the same batch twice is near-instant on the second pass

### Phase 6 — Feature repository (2 days)
- Stand up Feast, or the lightweight Postgres/Parquet catalog if Feast overhead isn't worth it yet
- Define the output contract (§11) with consistent metadata
- **Done when**: you can query "give me all molecule graph features produced this week" and get consistent, versioned results

### Phase 7 — Additional connectors (1–2 days per source)
- Add ChEMBL, PubChem, GEO/SRA, S3/FTP connectors using the Phase 1 interface
- Each is scheduled (nightly sync) or event-triggered (new object in watched bucket)
- **Done when**: two or more sources are syncing on schedule without manual intervention

### Phase 8 — KubeRay integration for GPU-scale steps (1–2 days)
- Install KubeRay operator, deploy a `RayCluster` sized for the 4090
- Move the heaviest steps (MSA generation, image featurization) onto Ray remote tasks
- **Done when**: a batch job scales across Ray workers on the single GPU node without code changes needed for a future multi-GPU cluster

### Phase 9 — Observability (1 day)
- Prometheus + Grafana + Loki via Helm
- Dashboards for: pipeline success/failure, cache hit rate, queue depth, per-source sync status
- **Done when**: a failed pipeline run and a low cache-hit-rate period are both visible on a dashboard without checking logs manually

### Phase 10 — Backups (0.5 day)
- Nightly `pg_dump` of the metadata catalog + dataset registry to a separate MinIO bucket (or off-box)
- `mc mirror` for the `raw/` and `features/` MinIO zones on a schedule
- **Done when**: you can restore the catalog and a feature dataset from backup on a fresh instance without any other running component

### Phase 11 — Validation, tests, and documentation (ongoing)
- Unit tests per pipeline module and per connector against `tests/sample_data/`
- End-to-end test: run a small mixed-modality, multi-source batch through the full path, assert feature repository contents match expected schema
- Write the README documenting the output contract and the catalog schema — what future connectors and future model-connector code will rely on

---

## 11. The "model-ready" output contract

Revised from v1 per §4b: every feature record is one of four container **types**, but a single input can produce more than one such record — the rigid part isn't "one per input," it's that every representation, however many get generated, conforms to one of these four shapes. That's what any future model connector builds against:

1. **Token-ID sequence** `[seq_len]` + attention mask — ESM-2/3, TxGemma, REINVENT, ProtBERT-style models
2. **Graph batch** (node features, edge index, edge features) — Chemprop, ProteinMPNN, GNN-based ADMET models
3. **Dense image tensor** `[C, H, W]` — Phenom-style phenomics models
4. **SE(3) frame tensor** (rotation + translation per residue/atom) — RFdiffusion, AlphaFold/Boltz-class structure models

Each stored with metadata: `{source_hash, dataset_id, modality, representation_type, pipeline_version, created_at, model_compatibility_tags}`. `source_hash` + `dataset_id` are what tie multiple representations of the same input back together; `representation_type` is what the Feature Catalog API filters on when a caller wants a specific one.

---

## 12. Realistic timeline

| Phase | Time |
|---|---|
| 0 – Cluster foundation | 0.5–1 day |
| 1 – Metadata catalog + connector framework | 1–2 days |
| 2 – Ingest + Validate | 1–2 days |
| 3 – Molecule + sequence pipelines | 2–3 days |
| 4 – Structure + image pipelines | 2–3 days |
| 5 – Caching layer | 1–2 days |
| 6 – Feature repository | 2 days |
| 7 – Additional connectors (per source) | 1–2 days each |
| 8 – KubeRay GPU integration | 1–2 days |
| 9 – Observability | 1 day |
| 10 – Backups | 0.5 day |
| 11 – Tests/docs | ongoing, ~1–2 days buffer |
| **Total (core, excl. extra connectors)** | **~3–4 weeks part-time, ~15–17 focused days full-time** |

---

## 13. What v3 will add later (still not in this build plan)

- Model connector layer: pulls from the feature repository, wraps fine-tuning loops (LoRA/QLoRA for ESM, RL fine-tuning for REINVENT, affinity-head fine-tuning for Boltz-2, etc.)
- Inference serving via KServe/vLLM/Triton
- Kueue + dedicated Job Service (same trigger — real job contention), Spark Operator, OpenLineage, Keycloak, multi-tenancy — promoted from §8 once their triggers fire
- LIMS/ELN/instrument connectors — only if a client engagement requires them
- Argo CD for GitOps deployment of pipeline changes
- Multi-node scale-out (real cluster, not `kind`)

The output contract in §11 and the connector/catalog interfaces in §3 and §7 are what guarantee none of this requires rearchitecting when it's added.
