"""Argo `transform` step — BUILD_PLAN.md §10 Phase 3/4/5/6.

Dispatches a validated file to its modality's pipeline (molecule, sequence,
structure, image — tabular and text still have no transform pipeline).

Before generating each requested representation, checks the content-addressed
cache (BUILD_PLAN §10 Phase 5) via engine/feature_repository — keyed on
(content_hash, representation_type, pipeline_version), computed from the
record's canonical form. A cache hit reuses the previous result's location
and quality outcome without calling the featurizer or writing to storage
again; a miss runs the output-validation gate (§6) and writes to features/
(passed) or rejected/ (failed), same as before caching existed. Either way a
new Feature row is written via the feature repository, linking this file to
the result — the cache saves the compute and the storage write, not the
bookkeeping. A rejection is the same kind of expected outcome as a
validate-step rejection: the step still exits 0, and pass/fail lives in the
catalog's quality_status field.

This is the only writer of Feature rows — engine/feature_repository is the
read/write contract (BUILD_PLAN §9/§11/Phase 6), so anything that wants to
query produced features (the CLI, a future model connector) goes through it
too, not the catalog API directly.

Any modality with no entry in _PIPELINES is out of scope for this pipeline
version and is skipped, not rejected.
"""

import argparse
import hashlib
import json

from connectors.catalog_client import CatalogClient
from connectors.storage import get, land, parse_location
from engine.feature_repository import FeatureRepository
from engine.pipelines import image_pipeline, molecule_pipeline, sequence_pipeline, structure_pipeline
from engine.validators.output import image as image_output
from engine.validators.output import molecule as molecule_output
from engine.validators.output import sequence as sequence_output
from engine.validators.output import structure as structure_output

_PIPELINES = {
    "molecule": molecule_pipeline,
    "sequence": sequence_pipeline,
    "structure": structure_pipeline,
    "image": image_pipeline,
}

_MODEL_TAGS = {
    "molecule_graph": ["chemprop", "gnn-admet"],
    "molecule_tokens": ["smiles-lm", "reinvent"],
    "sequence_tokens": ["esm", "protbert"],
    "structure_graph": ["proteinmpnn"],
    "structure_frames": ["rfdiffusion", "boltz", "alphafold-class"],
    "image_tensor": ["phenom", "cell-painting-cnn"],
}


def _validate_output(representation_type: str, canonical_form: str, tensor: dict) -> tuple[bool, str | None]:
    if representation_type == "molecule_graph":
        return molecule_output.validate_graph(canonical_form, tensor)
    if representation_type == "molecule_tokens":
        return molecule_output.validate_tokens(tensor)
    if representation_type == "sequence_tokens":
        return sequence_output.validate_tokens(tensor)
    if representation_type == "structure_graph":
        return structure_output.validate_graph(tensor)
    if representation_type == "structure_frames":
        return structure_output.validate_frames(tensor)
    if representation_type == "image_tensor":
        return image_output.validate_image_tensor(tensor)
    raise ValueError(f"no output validator registered for representation '{representation_type}'")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True, type=int)
    parser.add_argument("--modality", required=True)
    parser.add_argument("--location", required=True, help="current bucket/key, e.g. validated/local/ds/file.fasta")
    parser.add_argument("--representations", default="", help="comma-separated; empty = pipeline default")
    args = parser.parse_args()

    pipeline = _PIPELINES.get(args.modality)
    if pipeline is None:
        print(f"skipping: modality '{args.modality}' has no transform pipeline in Phase 3")
        return

    content = get(args.location)
    _, key = parse_location(args.location)
    filename = key.rsplit("/", 1)[-1]
    representations = [r.strip() for r in args.representations.split(",") if r.strip()] or pipeline.DEFAULT_REPRESENTATIONS

    adapter = pipeline.adapter_for(filename)
    records = adapter.parse(content)
    pipeline_version = f"{pipeline.PIPELINE_NAME}:{pipeline.PIPELINE_VERSION}"
    repo = FeatureRepository()

    for record in records:
        canonical_form = pipeline.canonical_form_of(record)
        source_hash = hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()

        for rep_key in representations:
            representation_type = pipeline.REPRESENTATION_TYPES.get(rep_key)
            if representation_type is None:
                raise ValueError(f"unknown representation '{rep_key}' for {pipeline.PIPELINE_NAME}")

            cached = repo.find_cached(source_hash, representation_type, pipeline_version)
            if cached is not None:
                repo.register(
                    source_file_id=args.file_id,
                    dataset_id=args.dataset_id,
                    dataset_version=args.dataset_version,
                    modality=args.modality,
                    representation_type=representation_type,
                    pipeline_version=pipeline_version,
                    source_hash=source_hash,
                    location=cached.location,
                    model_compatibility_tags=cached.model_compatibility_tags,
                    quality_status=cached.quality_status,
                    quality_checks_passed=cached.quality_checks_passed + ["cache_hit"],
                    quality_detail=cached.quality_detail,
                )
                print(f"CACHE HIT {representation_type}: reusing {cached.location}")
                continue

            tensor = pipeline.FEATURIZERS[rep_key](record)
            ok, reason = _validate_output(representation_type, canonical_form, tensor)

            dest_bucket = "features" if ok else "rejected"
            out_key = f"{args.source}/{args.dataset_id}/{filename}.{representation_type}.json"
            out_location = land(out_key, json.dumps(tensor).encode("utf-8"), bucket=dest_bucket)

            repo.register(
                source_file_id=args.file_id,
                dataset_id=args.dataset_id,
                dataset_version=args.dataset_version,
                modality=args.modality,
                representation_type=representation_type,
                pipeline_version=pipeline_version,
                source_hash=source_hash,
                location=out_location,
                model_compatibility_tags=_MODEL_TAGS.get(representation_type, []),
                quality_status="passed" if ok else "rejected",
                quality_checks_passed=[f"output_validation:{representation_type}"] if ok else [],
                quality_detail=reason,
            )
            print(f"{'PASSED' if ok else 'REJECTED'} {representation_type}: {out_location}" + (f" ({reason})" if reason else ""))

    CatalogClient().update_file(args.file_id, status="featurized")


if __name__ == "__main__":
    main()
