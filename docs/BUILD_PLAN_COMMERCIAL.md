# Build Plan v3 — Commercial Multi-Tenant Hardening

**Why this document exists.** v2 (`BUILD_PLAN.md`) is complete — every Phase 0–11 "done when" was independently re-verified against live evidence (real backup/restore into disposable containers, real corrupted fixtures caught by output validation, a real Grafana panel queried through its own API, a real GEO pull re-processed end-to-end). v2 was also explicitly scoped, in its own words, for a single internal operator: §2 defers Keycloak "until a second person... gets access," and §8 defers multi-tenancy "until you onboard a client onto the same running instance." That trigger has now fired — this is contractual work a company will run commercially, for (implicitly) more than one customer over time. Nearly every gap below is the direct, predictable consequence of that single fact.

**Where this comes from.** A full evidence-based audit of the current repo (not a recollection — every claim below cites the specific file, config key, or command that supports it) turned up 23 concrete findings across 11 categories. This plan turns that audit into an executable, phase-by-phase build order, the same way v2 turned an architecture sketch into Phases 0–11. Nothing here is spec­ulative hardening for its own sake — each phase exists because a specific, cited gap makes the platform unsafe or unusable for real paying customers today.

**What does not change.** The v2 architecture (connectors → NATS → catalog → Argo/Ray transform → content-addressed cache → feature repository) is sound and stays as-is. This plan adds security, isolation, and operational maturity *around* it — it is not a rewrite. The four-container output contract (§11 of v2) and the connector/pipeline plugin interfaces are untouched.

**Explicitly still out of scope**, same boundary as v2: model training, fine-tuning, inference serving.

---

## Architecture deltas this plan introduces

```
                                    ┌─────────────────────┐
                                    │   Identity/Auth      │  NEW — Phase 13
                                    │ (per-customer keys,  │
                                    │  OIDC once needed)    │
                                    └──────────┬───────────┘
                                               │ every request now carries
                                               │ a tenant identity
                                               ▼
  Connectors → NATS → Ingestion → ┌─────────────────────────┐ → Transform (Argo+Ray) → ...
                                  │  Metadata Catalog         │
                                  │  + tenant_id on every      │  NEW — Phase 14
                                  │  Dataset/File/Feature row  │
                                  │  + query-level scoping     │
                                  └─────────────────────────┘
```

The rest of v2's diagram (validation, standardization, modality router, cache, feature repository) is unchanged — every new phase either wraps it in a security/tenancy boundary or hardens its operational posture, without touching its internals.

---

## Technology selections (additions to v2 §2)

| Layer | Technology | Why this one |
|---|---|---|
| Per-customer auth (Phase 13) | Hashed API keys in Postgres (new `api_keys` table), not a new service | A full IAM system (Keycloak) is still overkill for a handful of B2B customers with server-to-server integrations — matches v2's own "don't add infra before the trigger fires" discipline. Promote to OIDC/Keycloak when the trigger in the deferred table below fires. |
| Tenant scoping (Phase 14) | `tenant_id` column + enforced query filter, same Postgres instance | Matches v2 §8's own stated alternative to full multi-tenancy infrastructure — cheaper than per-client clusters, sufficient until real regulatory/contractual data-segregation requirements say otherwise (see Phase 14's own deferred note). |
| Secrets (Phase 12) | Kubernetes `Secret` objects, created imperatively or via `kubectl create secret --from-literal`, same pattern already used correctly for `PUBLIC_API_KEY` | No new infrastructure needed — the correct pattern already exists in this repo once, it just needs applying everywhere else. Move to Vault/External Secrets Operator only once secret *rotation* or *multi-cluster* sharing is a real requirement. |
| Schema migrations (Phase 17) | Alembic | Standard SQLAlchemy migration tool; already the dependency the codebase is one layer away from (SQLAlchemy models exist, autogeneration works against them directly). |
| Alerting (Phase 18) | Prometheus Alertmanager (already bundled in `kube-prometheus-stack`, currently `enabled: false`) + a webhook receiver (Slack at minimum, pick a paging tool the company already has a contract with) | Zero new infrastructure — it's one Helm value away from working. |
| CI (Phase 19) | GitHub Actions | Repo is already on GitHub; zero new hosting to stand up. |
| Image scanning (Phase 19) | Trivy | Free, fast, no external account needed, runs as a GitHub Action step. |
| Rate-limit store (Phase 21) | Redis (single small instance) or the existing Postgres instance if adding Redis isn't justified yet | Needed the moment the catalog runs more than one replica — a hard requirement from Phase 16's HA work, not optional once that lands. |

---

## Phase 12 — Secrets remediation & credential hygiene

**Do this first — it's fast, blocking, and requires no architecture changes.**

- Rotate every credential currently committed in plaintext: MinIO root user/password (`infra/helm/minio-values.yaml`), Postgres password (`infra/helm/postgres-values.yaml`, and hardcoded again in `infra/k8s-manifests/catalog.yaml`'s `DATABASE_URL`), Grafana admin password (`infra/helm/kube-prometheus-stack-values.yaml`).
- Move each into a Kubernetes `Secret`, referenced via `valueFrom.secretKeyRef` — the exact pattern `PUBLIC_API_KEY` already uses correctly in `catalog.yaml`. Values files keep a placeholder/reference, never the real value.
- Add a `LICENSE` file to the repository root (currently absent) — the codebase's own IP terms are undefined, which matters directly for a contractual commercial handoff.
- Add a secret-scanning check (e.g. `gitleaks`) as a pre-commit hook and/or the first CI job (Phase 19) so this can't regress.
- Decide and document: can the exposed credential history be considered a real incident (was this repo ever public, was it ever cloned outside the team)? If yes, treat this as an actual credential-compromise response, not just a cleanup — assume the old values are burned and can never be reused.

**Done when**: no plaintext credential exists in any file tracked by git (verified by grep across the full working tree, not just the files known today); `gitleaks detect` (or equivalent) run against the full history passes or has a documented, accepted residual-risk note; the platform still deploys and passes the Phase 0–11 verification steps with the new secrets wired in.

---

## Phase 13 — Authentication: per-customer API keys

**The foundational phase everything else in this plan depends on** — tenant scoping (Phase 14) needs a request to carry a tenant identity, and that identity has to come from somewhere real.

- New `api_keys` table (Postgres): `key_id, key_hash, tenant_id, scopes (read/write), label, created_at, revoked_at`. Keys are generated once, shown once, stored only as a hash (bcrypt or similar) — same principle as every other API-key system, no reason to reinvent it.
- Replace `catalog/public_api.py`'s single `PUBLIC_API_KEY` check with a lookup against `api_keys` (hash the incoming key, look up, check `revoked_at IS NULL`, attach `tenant_id` and `scopes` to the request context for Phase 14 to use).
- Internal catalog API (`catalog/api.py`) currently has **zero** authentication — anything on the cluster network can read/write every table. At minimum, add a shared service-to-service secret (every internal caller — connectors, engine steps — presents it) as an interim measure; this is not full per-service identity, but it closes "any pod, no exceptions" down to "any pod holding the one internal secret," which is a real reduction in blast radius and can ship in this phase without a bigger identity project.
- Rate limiting (currently per-IP, in-memory) becomes per-`tenant_id` in addition to per-IP, so one customer's usage can't exhaust another's quota.
- Build the minimum key-management surface needed to operate this: an internal (not public) endpoint or CLI to issue/revoke a key for a given tenant. A full self-service customer portal is explicitly not in scope for this phase.

**Done when**: two different customer API keys can be issued, each independently scoped; revoking one key immediately rejects requests using it while the other continues to work; every `/public/*` request is traceable to a specific `tenant_id` in logs/catalog records, not an anonymous `source` string; the internal catalog API rejects requests without the service-to-service secret.

---

## Phase 14 — Multi-tenancy in the data model

- Add `tenant_id` to `Dataset` (propagates to `File` via `dataset_pk`, and directly to `Feature` too, for query performance and defense-in-depth rather than relying purely on a join).
- Every read/write path in `catalog/api.py` and `catalog/public_api.py` filters by the calling request's `tenant_id` (from Phase 13's auth context) — enforced in the query itself, not just convention. Add a test that specifically tries a cross-tenant read and asserts it comes back empty/403, not just "the happy path works."
- Decide and document explicitly (this is a product/business decision as much as an engineering one): does the company run **one shared instance with tenant-scoped rows** (this phase's approach — cheaper, faster to onboard a new client), or **one isolated deployment per client** (v2 §8's original fallback — simpler security model, more infrastructure to operate per client, no cross-client resource sharing)? This plan builds the shared-instance path because it's the cheaper default and the schema change is needed either way for internal reporting across clients (billing, usage) — but if the company's contracts require physical data segregation (common in regulated pharma work), the one-deployment-per-client path may be the actual requirement regardless of what's cheaper to build.
- Backfill existing rows (connector-synced public data, prior demo data) into a `public-demo` or `internal` tenant so nothing is orphaned.

**Done when**: a request authenticated as tenant A cannot read, list, or download any Dataset/File/Feature belonging to tenant B, verified by an actual attempted cross-tenant request in a test, not just code review; every new Dataset/File/Feature row has a non-null `tenant_id` from the moment this phase ships.

---

## Phase 15 — Public demo re-scoping

- The existing public site (`engine.phronexis.bio`) was built as a portfolio showcase and lets any visitor browse every dataset ever produced — that is fundamentally incompatible with also being (or sharing infrastructure with) the commercial product the moment any real client's tenant exists.
- Recommended shape: the public demo becomes its own tenant (`public-demo`, from Phase 14) containing only synthetic/public-database data, explicitly never a real client's tenant — enforced by the tenant model itself, not by hoping nobody uploads something sensitive to it.
- If the commercial product and the public demo are meant to share one running cluster, the frontend's `/datasets`, `/features`, `/sources` pages must be scoped to the `public-demo` tenant only, with no code path that can widen that to "all tenants."
- If budget/ops allow, prefer a **fully separate deployment** for the public demo instead — simpler to reason about than "one instance, carefully scoped," and removes the public demo as an attack surface against real client data entirely.

**Done when**: there is no code path, misconfiguration, or default setting under which a real client's tenant data can appear on the public site; this is documented as an explicit architectural decision the company has signed off on, not an implicit default.

---

## Phase 16 — Network & pod security hardening

- `NetworkPolicy` resources restricting traffic to only necessary paths: catalog → Postgres, catalog → MinIO, engine-step pods → catalog/MinIO, connector pods → catalog/MinIO/NATS + their specific external API (never lateral to each other or to Postgres directly). Default-deny within the namespace, explicit allow rules on top.
- `securityContext` on every container: `runAsNonRoot: true`, `readOnlyRootFilesystem` where the workload allows it, dropped Linux capabilities, no privilege escalation.
- Every Deployment gets: a `livenessProbe` in addition to the existing `readinessProbe` (today only the catalog's readiness is checked — a hung, non-crashed process is never restarted); `resources.limits` in addition to `requests` (today only requests are set — no ceiling exists on any container); a `PodDisruptionBudget` where more than one replica exists.
- Bump the catalog Deployment to `replicas: 2+` once Phase 13's internal auth and Phase 21's shared rate-limit store make that safe (today, `replicas: 1` is partly load-bearing for the in-memory rate limiter — don't scale replicas before Phase 21, or the rate limit silently weakens).

**Done when**: a NetworkPolicy test — attempting a connection between two pods that shouldn't be able to reach each other — is actually denied; `kubectl get pods -o yaml` across the namespace shows non-root security contexts and resource limits on every container; killing the catalog's underlying process (not the pod) triggers an automatic restart via the new liveness probe.

---

## Phase 17 — High availability & disaster recovery

- Postgres: move from `architecture: standalone` to a primary + read-replica setup (the Bitnami chart already supports this via a values change) with automatic failover, or move to a managed Postgres service if this phase coincides with leaving `kind` for a real cloud cluster.
- MinIO: move from `mode: standalone` to distributed mode (minimum 4 nodes, erasure-coded) or migrate to managed object storage (S3, GCS) if leaving `kind`.
- Backups: the current nightly `pg_dump` + `mc mirror` land in the *same* MinIO instance they're backing up — genuine data loss protection requires an off-box target (a different account, region, or provider). This was explicitly scoped out of v2 for lack of a second environment; it's a hard requirement now.
- Adopt Alembic for schema migrations — today `Base.metadata.create_all()` on startup is the entire migration story, meaning any schema change against live data means either hand-written SQL or data loss. This blocks every other phase in this plan that touches the schema (14 already does) from being safely deployable against a live system.
- Define RTO/RPO targets explicitly (even a first-pass number like "restore within 4 hours, lose at most 24 hours of data" is better than the current answer, which is "undefined") and validate them with an actual timed restore drill.

**Done when**: a simulated primary Postgres failure results in automatic failover with no data loss beyond the replication lag; a restore from the off-box backup succeeds into a genuinely fresh environment (same bar v2 Phase 10 already proved for the on-box case) within the documented RTO; a schema change ships via an Alembic migration against a database with existing data, without data loss.

---

## Phase 18 — Observability & incident response

- Flip `alertmanager.enabled` from `false` to `true` (`infra/helm/kube-prometheus-stack-values.yaml`) and define real `PrometheusRule` alerts: workflow failure-rate threshold, MinIO/Postgres disk usage, pod crash-loop detection, NATS queue-depth threshold, catalog API error rate.
- Wire Alertmanager to an actual notification channel — Slack webhook at minimum, a real paging tool (PagerDuty/Opsgenie) if the company has an on-call rotation.
- Implement the NATS dead-letter subject v2 §3 promised but never built: a consumer-level `max_deliver`, a subscriber on the JetStream advisory subject, republishing exhausted messages onto `platform.dlq.*`. `infra/setup-nats-streams.sh` already documents exactly what's needed in its own comments — this phase is executing that comment, not designing something new.
- Convert the operational knowledge currently living as prose in `CLAUDE.md` (promtail crash-loop fix, Argo RBAC requirements, etc.) into short, procedural runbooks — one per known failure mode — that a support engineer under incident pressure could follow without first understanding the whole system.

**Done when**: a deliberately induced failure (kill a pod, force a workflow to fail, fill a disk past threshold) results in an actual notification arriving at the chosen channel, not just a dashboard changing color; a message that exhausts its redelivery attempts appears on a `platform.dlq.*` subject and is inspectable, rather than silently vanishing.

---

## Phase 19 — CI/CD & supply chain

- GitHub Actions workflow running the existing 68 unit tests (`python -m pytest`) and lint on every push/PR — today they only protect the codebase if a human remembers to run them locally, which `CLAUDE.md` itself has to remind readers to do.
- Add Trivy image scanning as a pipeline step for `catalog`, `engine-steps`, `connectors`, and `infra/backup` images, failing the build on critical/high CVEs above an agreed threshold.
- Add Dependabot or Renovate for dependency-update PRs across the Python and Node (frontend) dependency trees.
- Environment separation: at minimum, a distinct namespace (or cluster) for dev/staging vs. whatever is designated production, with a promotion step between them rather than one shared environment serving as dev, demo, and production simultaneously.

**Done when**: a PR introducing a failing test or a critical CVE in a base image is automatically blocked from merging; a real dependency update PR is opened automatically at least once and successfully merged through the new pipeline.

---

## Phase 20 — Data governance & legal compliance

**The category most likely to be skipped by an engineering-only read of this plan — flagged as its own phase specifically so it isn't.**

- Populate `Dataset.license` for real, per source, at ingest time (today the column exists but every connector call site passes the default `None` — verified, not assumed). ChEMBL (CC BY-SA), UniProt, PubChem, GEO, and SRA each carry real usage/attribution/redistribution terms that matter the moment any commercial output derives from them.
- Get an actual legal review (external to this engineering work) of redistribution/attribution obligations for each public data source this platform ingests, before any client-facing deliverable includes data derived from them.
- Add a Terms of Service, Privacy Policy, and (if any client integration requires it) a Data Processing Agreement for any user- or client-facing upload path — currently the visitor upload path has none of these, and previously made results publicly browsable with no consent flow.
- Add basic content scanning (e.g. a ClamAV sidecar, or a hosted scanning API) on ingested files before they reach a parser — today only structural validation exists (is this valid FASTA/CSV), nothing scans for malicious content.
- Add a data-classification field to `Dataset` (`public` / `internal` / `confidential` / `regulated`) — a drug-discovery CRO plausibly handles data with real regulatory weight (human-subject-derived sequences, GxP-relevant assay data), and nothing today distinguishes that from a public ChEMBL sync. This should drive which storage/retention/access rules apply per Phase 14's tenant model, not be a label with no effect.

**Done when**: every connector-landed dataset has a non-null, accurate `license` value; a legal sign-off document (external to this repo, but referenced from it) exists covering redistribution terms for every ingested public source; a ToS/Privacy Policy is live wherever visitor/client uploads are accepted; an uploaded file with known-bad content (an EICAR test file) is caught by the new content scan before reaching a parser.

---

## Phase 21 — Scale-readiness

- Move the public-upload rate limiter from an in-process Python dict to a shared store (Redis, or the existing Postgres instance) — required before the catalog can safely run more than one replica (Phase 16), since today each replica would get its own counter and silently weaken the effective limit.
- Add connection pooling (PgBouncer) in front of Postgres — not yet a bottleneck, but a known one the moment concurrent Argo workflow pods multiply.
- API versioning: `/v1/public/...` instead of the current unversioned `/public/...`, so a future breaking change doesn't break every existing customer integration with no migration path.
- Revisit JSON-blob feature tensors vs. the originally-planned Zarr/Parquet/Iceberg (v2 §2) — explicitly **trigger-based, not immediate**: do this once a real GPU-scale workload (full MSA tensors, embedding matrices, multi-channel image stacks) actually runs at volume, matching this codebase's own stated discipline of not building ahead of a real need.

**Done when**: the catalog runs at `replicas: 2+` with the shared rate limiter correctly enforcing one customer's limit regardless of which replica handles the request (verified with an actual load test hitting both replicas); a new `/v1/` route exists alongside (not replacing) the current one, proving the versioning seam works before it's ever actually needed for a breaking change.

---

## Phase 22 — Modality/connector completion (scoped to the actual contract)

- v2 named `complex_pipeline` (protein–ligand complexes), `text_pipeline`, and `pdb_connector.py` and never built any of them. Do **not** build all three speculatively — build only whichever the specific commercial contract's real data mix requires, against a real sample of the client's actual data, not a synthetic guess at the format.
- If `complex_pipeline` is needed: revisit v2 §4's original design (structure + ligand conformer merge via RDKit ETKDG) against what the client's real complex data actually looks like before assuming it's still the right shape.
- Tabular's current representation (a hashed-token sequence, chosen in this session to stay inside the four-container contract rather than the originally-specified "cleaned Parquet/Iceberg table") should get explicit product/client sign-off before it's load-bearing for a contract deliverable — flagged here again because it's an open decision, not a settled one.

**Done when**: whatever modality/connector gap the signed contract actually requires is closed, verified against a real sample of that client's data — and nothing is built against this list that the contract doesn't actually need.

---

## What's still deferred, and the trigger to add it

Same spirit as v2 §8 — kept in mind so nothing needs rearchitecting later, not built now because the trigger hasn't fired:

| Component | Deferred because | Add it when |
|---|---|---|
| Full OIDC/Keycloak (replacing Phase 13's API-key auth) | A handful of B2B server-to-server customers don't need human SSO yet | A customer needs their own end-users (not just their backend) to log in individually |
| Vault / External Secrets Operator (replacing Phase 12's plain K8s Secrets) | Plain Secrets are sufficient once nothing is committed in plaintext | Secret rotation needs to be automatic, or secrets need to be shared across more than one cluster |
| Physical per-client deployment isolation (instead of Phase 14's shared-instance tenant scoping) | Shared-instance scoping is cheaper and faster to onboard a new client | A specific contract requires physical data segregation (common in regulated pharma work) that row-level scoping can't satisfy |
| Kueue / dedicated Job Service | Still no real GPU contention — the CPU-only environment means this trigger literally cannot fire yet | A real GPU exists and 2+ customers' jobs compete for it concurrently |
| Kafka (replacing NATS) | NATS JetStream still covers current throughput | Event volume or multi-consumer fan-out genuinely outgrows NATS in practice |
| Zarr/Parquet/Iceberg tensor storage (replacing JSON blobs) | Not a bottleneck at today's data volume | A real GPU-scale workload (MSA/embeddings/image stacks) runs at volume and JSON becomes measurably slow or large |
| Self-service customer key-management portal | One-off internal issuance (Phase 13) is enough for a handful of customers | Onboarding a new customer without engineering involvement becomes a real operational need |

---

## Realistic timeline

| Phase | Time | Depends on |
|---|---|---|
| 12 – Secrets remediation | 0.5–1 day | none — do first |
| 13 – Per-customer authentication | 2–3 days | Phase 12 |
| 14 – Multi-tenancy in the data model | 2–3 days | Phase 13 |
| 15 – Public demo re-scoping | 0.5–1 day | Phase 14 |
| 16 – Network & pod security hardening | 1–2 days | Phase 13 (internal auth) |
| 17 – HA & disaster recovery | 3–5 days | none, but blocks safely deploying Phase 14's schema change to a live system |
| 18 – Observability & incident response | 1–2 days | none |
| 19 – CI/CD & supply chain | 1–2 days | none |
| 20 – Data governance & legal | 2–4 days (+ external legal review time, not engineering-controlled) | none |
| 21 – Scale-readiness | 1–2 days | Phase 16 (replica scaling) |
| 22 – Modality/connector completion | 1–2 days per item | whatever the signed contract requires |
| **Total (engineering, excl. legal review turnaround and Phase 22)** | **~15–25 focused days** | |

Suggested execution order for maximum risk reduction per day spent: **12 → 13 → 14 → 16 → 17 → 18 → 19 → 15 → 20 → 21 → 22** — get secrets and auth fixed before anything else touches the schema, get the schema/tenancy change onto HA infrastructure before it's load-bearing for real customer data, then close out observability/CI/legal/scale work as parallel tracks once the core security posture is sound.
