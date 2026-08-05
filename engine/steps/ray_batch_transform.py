"""Ray batch transform driver — BUILD_PLAN.md §10 Phase 8.

Distributes image featurization for a whole dataset across the RayCluster's
workers at once, instead of engine/steps/transform.py's one-file-per-Argo-pod
model — this is what "a batch job scales across Ray workers" (the Phase 8
done-when) actually means: N tasks submitted together, Ray's scheduler
spreading them across however many workers exist, not this driver processing
them one at a time itself.

Run this from inside the head pod (`kubectl exec` into it, or a job the
KubeRay Jobs API places there) — `ray.init(address="auto")` picks up the
already-running local Ray instance, same as `ray attach`-ing to a node. The
alternative, connecting from an entirely separate pod over the Ray Client
protocol (`ray://...:10001`), hit a reproducible crash in this environment's
gRPC (`Check failed: next_worker->state == KICKED` in the client proxy's
epoll handling — a known class of gRPC fork/epoll issue, not something fixable
here) — worth knowing if a future refactor is tempted to route through it
again instead of exec/Jobs-API.

Only image_pipeline is wired here — it's the one Phase 8's two named "heaviest
steps" (BUILD_PLAN §10) that has a real, non-stubbed featurizer to batch; MSA
generation (engine/ray_tasks.generate_msa_remote) stays a stub per Phase 3,
so there's nothing meaningful to batch-drive for it yet.
"""

import argparse
import hashlib
import json

import ray

from connectors.catalog_client import CatalogClient
from connectors.storage import get, land, parse_location
from engine.feature_repository import FeatureRepository
from engine.pipelines import image_pipeline
from engine.ray_tasks import featurize_image_remote
from engine.validators.output import image as image_output

RAY_ADDRESS = "auto"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True, type=int)
    parser.add_argument("--representations", default="", help="comma-separated; empty = pipeline default")
    args = parser.parse_args()

    catalog = CatalogClient()
    repo = FeatureRepository()

    files = [
        f
        for f in catalog.list_files(args.dataset_id, args.dataset_version)
        if f["modality"] == "image" and f["status"] == "validated"
    ]
    if not files:
        print(f"no validated image files found in {args.dataset_id}@{args.dataset_version}")
        return

    representations = [r.strip() for r in args.representations.split(",") if r.strip()] or None

    ray.init(address=RAY_ADDRESS)
    print(f"connected to Ray cluster: {ray.cluster_resources()}")

    # Submit every file's featurization as an independent task up front, then
    # collect results after — this is what lets Ray's scheduler place them
    # across multiple workers concurrently instead of running sequentially.
    task_refs = {}
    for f in files:
        content = get(f["location"])
        filename = f["location"].rsplit("/", 1)[-1]
        task_refs[f["file_id"]] = featurize_image_remote.remote(content, filename, representations)

    print(f"submitted {len(task_refs)} task(s) to the cluster")

    pipeline_version = f"{image_pipeline.PIPELINE_NAME}:{image_pipeline.PIPELINE_VERSION}"
    workers_used = set()

    for f in files:
        outcome = ray.get(task_refs[f["file_id"]])
        workers_used.add(outcome["worker"])
        print(f"file_id={f['file_id']} ran on worker={outcome['worker']}")

        for result in outcome["results"]:
            representation_type = result["representation_type"]
            tensor = result["tensor"]
            canonical_form = result["canonical_form"]
            source_hash = hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()

            cached = repo.find_cached(source_hash, representation_type, pipeline_version)
            if cached is not None:
                repo.register(
                    source_file_id=f["file_id"],
                    dataset_id=args.dataset_id,
                    dataset_version=args.dataset_version,
                    modality="image",
                    representation_type=representation_type,
                    pipeline_version=pipeline_version,
                    source_hash=source_hash,
                    location=cached.location,
                    model_compatibility_tags=cached.model_compatibility_tags,
                    quality_status=cached.quality_status,
                    quality_checks_passed=cached.quality_checks_passed + ["cache_hit"],
                    quality_detail=cached.quality_detail,
                )
                print(f"  CACHE HIT {representation_type}: reusing {cached.location}")
                continue

            ok, reason = image_output.validate_image_tensor(tensor)
            dest_bucket = "features" if ok else "rejected"
            _, key = parse_location(f["location"])
            filename = key.rsplit("/", 1)[-1]
            out_key = f"{f['source']}/{args.dataset_id}/{filename}.{representation_type}.json"
            out_location = land(out_key, json.dumps(tensor).encode("utf-8"), bucket=dest_bucket)

            repo.register(
                source_file_id=f["file_id"],
                dataset_id=args.dataset_id,
                dataset_version=args.dataset_version,
                modality="image",
                representation_type=representation_type,
                pipeline_version=pipeline_version,
                source_hash=source_hash,
                location=out_location,
                model_compatibility_tags=["phenom", "cell-painting-cnn"],
                quality_status="passed" if ok else "rejected",
                quality_checks_passed=[f"output_validation:{representation_type}"] if ok else [],
                quality_detail=reason,
            )
            print(f"  {'PASSED' if ok else 'REJECTED'} {representation_type}: {out_location}")

        catalog.update_file(f["file_id"], status="featurized")

    print(f"\n{len(files)} file(s) processed across {len(workers_used)} distinct worker(s): {sorted(workers_used)}")


if __name__ == "__main__":
    main()
