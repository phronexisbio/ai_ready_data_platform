"""UniProt connector — first public-database connector (BUILD_PLAN.md §3, Phase 1).

Fetches a given set of accessions as FASTA via the UniProt REST API. A full
scheduled nightly sync across all of UniProt is Phase 7 scope (BUILD_PLAN.md
§10); Phase 1 only needed one public-DB connector proven end-to-end through
the catalog and event bus. `main()` (added in Phase 7) makes it schedulable
the same way as chembl_connector/pubchem_connector, though it isn't wired
into a CronWorkflow itself yet — no trigger has fired for it specifically.
"""

import argparse
import os

import requests

from connectors.base import Connector, DiscoveredItem, FetchedItem

UNIPROT_FASTA_URL = "https://rest.uniprot.org/uniprotkb/{accession}.fasta"

DEFAULT_ACCESSIONS = ["P69905", "P68871", "P00533"]  # hemoglobin alpha/beta, EGFR — a stable watchlist


class UniProtConnector(Connector):
    source = "uniprot"

    def __init__(self, accessions: list[str], **kwargs):
        super().__init__(**kwargs)
        self.accessions = accessions

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=f"{accession}.fasta",
                uri=UNIPROT_FASTA_URL.format(accession=accession),
                modality="sequence",
                metadata={"accession": accession},
            )
            for accession in self.accessions
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        resp = requests.get(item.uri, timeout=30)
        resp.raise_for_status()
        return FetchedItem(name=item.name, content=resp.content, modality=item.modality, metadata=item.metadata)

    def validate(self, item: FetchedItem) -> bool:
        """Input-side check: is this actually a well-formed FASTA record?"""
        return item.content.startswith(b">")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="uniprot-nightly-sync")
    parser.add_argument("--owner", default="uniprot-connector")
    parser.add_argument(
        "--accessions",
        default=os.environ.get("UNIPROT_ACCESSIONS", ",".join(DEFAULT_ACCESSIONS)),
        help="comma-separated UniProt accessions",
    )
    args = parser.parse_args()

    accessions = [a.strip() for a in args.accessions.split(",") if a.strip()]
    landed = UniProtConnector(accessions=accessions).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
