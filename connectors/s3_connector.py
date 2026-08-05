"""S3 connector — cloud storage connector (BUILD_PLAN.md §3, deferred in
Phase 7, built out here).

Pulls a fixed watchlist of object keys from an S3-compatible bucket — same
"fixed list re-fetched each run, no incremental state" shape as
`chembl_connector`/`pubchem_connector`, just against object storage instead
of a REST API. Works against real AWS S3 (anonymous or credentialed) as well
as any other S3-compatible endpoint (including this platform's own MinIO)
via `endpoint_url` — boto3 doesn't distinguish. Modality is guessed from the
object key's extension using the same table `local_connector` uses, since
"a file dropped in a bucket" and "a file dropped on disk" are the same
problem.
"""

import argparse
import os
from pathlib import PurePosixPath

import boto3
from botocore import UNSIGNED
from botocore.client import Config

from connectors.base import Connector, DiscoveredItem, FetchedItem
from connectors.local_connector import guess_modality

DEFAULT_BUCKET = "1000genomes"
# small, stable, public files — real S3 object keys often carry no useful
# extension (these are plain text), so the modality is given explicitly
# rather than guessed and getting misfiled as "unknown"
DEFAULT_KEYS = [("README.ebi_aspera_info", "text"), ("README.analysis_history", "text")]


class S3Connector(Connector):
    source = "s3"

    def __init__(
        self,
        bucket: str,
        keys: list[str | tuple[str, str]],
        endpoint_url: str | None = None,
        anonymous: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bucket = bucket
        self.keys = keys
        client_kwargs = {"endpoint_url": endpoint_url} if endpoint_url else {}
        if anonymous:
            client_kwargs["config"] = Config(signature_version=UNSIGNED)
        self._client = boto3.client("s3", **client_kwargs)

    def discover(self) -> list[DiscoveredItem]:
        items = []
        for entry in self.keys:
            key, modality = entry if isinstance(entry, tuple) else (entry, guess_modality(PurePosixPath(entry)))
            items.append(
                DiscoveredItem(
                    name=PurePosixPath(key).name,
                    uri=f"s3://{self.bucket}/{key}",
                    modality=modality,
                    metadata={"bucket": self.bucket, "key": key},
                )
            )
        return items

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        obj = self._client.get_object(Bucket=item.metadata["bucket"], Key=item.metadata["key"])
        content = obj["Body"].read()
        return FetchedItem(name=item.name, content=content, modality=item.modality, metadata=item.metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="s3-nightly-sync")
    parser.add_argument("--owner", default="s3-connector")
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET", DEFAULT_BUCKET))
    parser.add_argument(
        "--keys",
        default=os.environ.get("S3_KEYS"),
        help="comma-separated S3 object keys (modality guessed from extension); omit to use the built-in demo watchlist",
    )
    parser.add_argument("--endpoint-url", default=os.environ.get("S3_ENDPOINT_URL"))
    parser.add_argument(
        "--credentialed",
        action="store_true",
        help="use the environment's real AWS/MinIO credentials instead of anonymous/unsigned access",
    )
    args = parser.parse_args()

    keys = [k.strip() for k in args.keys.split(",") if k.strip()] if args.keys else DEFAULT_KEYS
    landed = S3Connector(
        bucket=args.bucket,
        keys=keys,
        endpoint_url=args.endpoint_url,
        anonymous=not args.credentialed,
    ).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
