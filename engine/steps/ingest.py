"""Argo `ingest` step — BUILD_PLAN.md §10 Phase 2.

Copies a file from the landing/ zone to raw/{source}/{dataset_id}/, untouched,
and updates its catalog status. This step never inspects file content — that's
the validate step's job.
"""

import argparse
from pathlib import Path

from connectors.catalog_client import CatalogClient
from connectors.storage import get, land, parse_location

# Argo captures this as the step's `outputs.parameters` (see
# workflows/argo/ingest-validate-template.yaml) so the validate step can take
# it as an input — container templates (unlike script templates) don't
# auto-capture stdout, so the location has to be written to a known path.
# Must be under /var/run/argo: that's the only volume this executor mounts
# into both the main and wait containers (main's own /tmp is not shared).
OUTPUT_PATH = Path("/var/run/argo/outputs/raw_location.txt")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--location", required=True, help="current bucket/key, e.g. landing/local/ds/file.fasta")
    args = parser.parse_args()

    content = get(args.location)
    _, key = parse_location(args.location)
    filename = key.rsplit("/", 1)[-1]
    raw_location = land(f"{args.source}/{args.dataset_id}/{filename}", content, bucket="raw")

    CatalogClient().update_file(args.file_id, status="raw", location=raw_location)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(raw_location)
    print(raw_location)


if __name__ == "__main__":
    main()
