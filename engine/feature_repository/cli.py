"""Feature repository CLI — the "CLI" in BUILD_PLAN's "SDK / CLI / REST"
consumer layer (§1 architecture diagram), for ad-hoc querying without
writing a script. Example:

  python -m engine.feature_repository.cli \\
      --representation-type molecule_graph --since 2026-07-29
"""

import argparse
from datetime import datetime

from engine.feature_repository import FeatureRepository


def main():
    parser = argparse.ArgumentParser(description="Query the feature repository.")
    parser.add_argument("--dataset-id")
    parser.add_argument("--dataset-version", type=int)
    parser.add_argument("--representation-type")
    parser.add_argument("--modality")
    parser.add_argument("--quality-status", default="passed", help="'passed' (default), 'rejected', or 'any'")
    parser.add_argument("--since", help="ISO 8601 date/datetime, e.g. 2026-07-29")
    parser.add_argument("--before", help="ISO 8601 date/datetime")
    args = parser.parse_args()

    results = FeatureRepository().query(
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        representation_type=args.representation_type,
        modality=args.modality,
        quality_status=None if args.quality_status == "any" else args.quality_status,
        produced_since=datetime.fromisoformat(args.since) if args.since else None,
        produced_before=datetime.fromisoformat(args.before) if args.before else None,
    )

    if not results:
        print("no matching features")
        return

    header = f"{'feature_id':<36} {'dataset_id':<24} {'ver':<4} {'representation_type':<18} {'quality':<9} {'created_at'}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r.feature_id:<36} {r.dataset_id:<24} {r.dataset_version:<4} {r.representation_type:<18} {r.quality_status:<9} {r.created_at}")
    print(f"\n{len(results)} feature(s)")


if __name__ == "__main__":
    main()
