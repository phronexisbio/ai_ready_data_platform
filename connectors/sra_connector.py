"""SRA (Sequence Read Archive) connector — public-database connector
(BUILD_PLAN.md §3, deferred in Phase 7, built out here).

Fetches *run/experiment metadata* for a given SRA accession via NCBI's
E-utilities (esearch to resolve the accession to an internal UID, esummary
to pull the record), landing it as a two-row CSV so it satisfies
`engine/validators/input/tabular.py`. Deliberately not the raw read data
(FASTQ) itself — actual sequencing reads run from megabytes to gigabytes per
run and need the separate `sra-tools`/`fastq-dump` toolchain to extract,
neither of which this platform's scope calls for; the metadata record (library
strategy, organism, run/base counts, linked BioProject/BioSample) is the part
that's useful to have cataloged, same "sync the record, not the payload"
shape as the other public-DB connectors.
"""

import argparse
import csv
import io
import os

import requests

from connectors.base import Connector, DiscoveredItem, FetchedItem

SRA_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
SRA_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

DEFAULT_SRA_ACCESSIONS = ["SRR000001"]  # a small, long-public HapMap 454 run


class SRAConnector(Connector):
    source = "sra"

    def __init__(self, accessions: list[str], **kwargs):
        super().__init__(**kwargs)
        self.accessions = accessions

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=f"{acc}.csv",
                uri=SRA_ESEARCH_URL,
                modality="tabular",
                metadata={"accession": acc},
            )
            for acc in self.accessions
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        accession = item.metadata["accession"]

        search_resp = requests.get(
            item.uri, params={"db": "sra", "term": accession, "retmode": "json"}, timeout=30
        )
        search_resp.raise_for_status()
        id_list = search_resp.json()["esearchresult"]["idlist"]
        if not id_list:
            raise ValueError(f"no SRA record found for accession {accession}")
        uid = id_list[0]

        summary_resp = requests.get(SRA_ESUMMARY_URL, params={"db": "sra", "id": uid, "retmode": "json"}, timeout=30)
        summary_resp.raise_for_status()
        record = summary_resp.json()["result"][uid]
        if "error" in record:
            raise ValueError(f"SRA esummary error for accession {accession} (uid {uid}): {record['error']}")

        header = ["accession", "uid"] + [k for k in record if k != "uid"]
        row = [accession, uid] + [str(record[k]) for k in header[2:]]

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerow(row)
        content = buf.getvalue().encode("utf-8")

        return FetchedItem(name=item.name, content=content, modality=item.modality, metadata=item.metadata)

    def validate(self, item: FetchedItem) -> bool:
        """Input-side check: did we actually get a metadata row back?"""
        return bool(item.content.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="sra-nightly-sync")
    parser.add_argument("--owner", default="sra-connector")
    parser.add_argument(
        "--accessions",
        default=os.environ.get("SRA_ACCESSIONS", ",".join(DEFAULT_SRA_ACCESSIONS)),
        help="comma-separated SRA run accessions (e.g. SRR000001)",
    )
    args = parser.parse_args()

    accessions = [a.strip() for a in args.accessions.split(",") if a.strip()]
    landed = SRAConnector(accessions=accessions).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
