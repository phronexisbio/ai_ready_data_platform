"""GEO (Gene Expression Omnibus) connector — public-database connector
(BUILD_PLAN.md §3, deferred in Phase 7, built out here).

Fetches a GEO Series' metadata record via NCBI's `acc.cgi` endpoint (a plain
`^KEY = value` / `!KEY = value` text format, no API key or UID lookup
needed — one request per accession) and lands it as a two-row CSV (header +
one data row) so it satisfies `engine/validators/input/tabular.py`.

GEO Series are expression matrices, not sequences — this connector lands
their *metadata* as `modality="tabular"`, matching BUILD_PLAN §4's own
data-type coverage table. `tabular_pipeline` doesn't exist yet (see
CLAUDE.md's Phase 3 notes), so these files pass input validation and then
get skipped (not rejected) at the transform step, same as any other tabular
file today — that's the documented, correct behavior for this modality, not
a shortcoming specific to this connector.
"""

import argparse
import csv
import io
import os

import requests

from connectors.base import Connector, DiscoveredItem, FetchedItem

GEO_ACC_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}&targ=self&form=text&view=brief"

DEFAULT_GEO_ACCESSIONS = ["GSE2553"]  # a small, stable, long-public sarcoma expression series


class GEOConnector(Connector):
    source = "geo"

    def __init__(self, accessions: list[str], **kwargs):
        super().__init__(**kwargs)
        self.accessions = accessions

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=f"{acc}.csv",
                uri=GEO_ACC_URL.format(accession=acc),
                modality="tabular",
                metadata={"accession": acc},
            )
            for acc in self.accessions
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        resp = requests.get(item.uri, timeout=30)
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="replace")

        fields: dict[str, list[str]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.lstrip("^!#").strip()
            value = value.strip()
            if not key:
                continue
            fields.setdefault(key, []).append(value)

        if not fields:
            raise ValueError(f"no metadata fields parsed from GEO response for {item.metadata['accession']}")

        header = list(fields.keys())
        row = ["; ".join(v for v in values if v) for values in fields.values()]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerow(row)
        content = buf.getvalue().encode("utf-8")

        return FetchedItem(name=item.name, content=content, modality=item.modality, metadata=item.metadata)

    def validate(self, item: FetchedItem) -> bool:
        """Input-side check: did we actually get parseable metadata back?"""
        return bool(item.content.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="geo-nightly-sync")
    parser.add_argument("--owner", default="geo-connector")
    parser.add_argument(
        "--accessions",
        default=os.environ.get("GEO_ACCESSIONS", ",".join(DEFAULT_GEO_ACCESSIONS)),
        help="comma-separated GEO Series accessions (e.g. GSE2553)",
    )
    args = parser.parse_args()

    accessions = [a.strip() for a in args.accessions.split(",") if a.strip()]
    landed = GEOConnector(accessions=accessions).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
