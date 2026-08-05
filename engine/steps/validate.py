"""Argo `validate` step — BUILD_PLAN.md §10 Phase 2 / §6 input gate.

Runs the modality's input validator against the raw file. Passing files move
to validated/; failing files are quarantined to rejected/ with the failure
reason recorded on the catalog record — this is what stops "ran without
erroring" from being mistaken for "the input was fine."

A rejection is an expected business outcome, not a bug, so this step exits 0
either way — pass/fail is recorded in the catalog's `status` field, not in
whether the Argo node itself succeeded.
"""

import argparse
from pathlib import Path

from connectors.catalog_client import CatalogClient
from connectors.storage import get, land, parse_location
from engine.validators.input.registry import validate as validate_input

# Argo captures these as the step's `outputs.parameters` (see
# workflows/argo/ingest-validate-template.yaml) so the DAG can decide whether
# to run `transform`, and pass it the new (validated/) location — main/wait
# containers only share /var/run/argo, not /tmp.
RESULT_OUTPUT_PATH = Path("/var/run/argo/outputs/validation_result.txt")
LOCATION_OUTPUT_PATH = Path("/var/run/argo/outputs/validated_location.txt")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--modality", required=True)
    parser.add_argument("--location", required=True, help="current bucket/key, e.g. raw/local/ds/file.fasta")
    args = parser.parse_args()

    content = get(args.location)
    ok, reason = validate_input(args.modality, content)

    _, key = parse_location(args.location)
    dest_bucket = "validated" if ok else "rejected"
    new_location = land(key, content, bucket=dest_bucket)

    CatalogClient().update_file(
        args.file_id,
        status="validated" if ok else "rejected",
        status_detail=reason,
        location=new_location,
    )

    RESULT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_OUTPUT_PATH.write_text("PASSED" if ok else "REJECTED")
    LOCATION_OUTPUT_PATH.write_text(new_location)

    print(f"{'VALIDATED' if ok else 'REJECTED'}: {new_location}" + (f" ({reason})" if reason else ""))


if __name__ == "__main__":
    main()
