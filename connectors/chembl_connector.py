"""ChEMBL connector — public-database connector (BUILD_PLAN.md §3, Phase 7).

Fetches a given set of ChEMBL molecule IDs as canonical SMILES via the
ChEMBL REST API, landing each as a .smi file. Scheduled nightly via
workflows/argo/chembl-sync-cronworkflow.yaml.
"""

import argparse
import os

import requests

from connectors.base import Connector, DiscoveredItem, FetchedItem

CHEMBL_MOLECULE_URL = "https://www.ebi.ac.uk/chembl/api/data/molecule/{chembl_id}.json"

DEFAULT_CHEMBL_IDS = ["CHEMBL25", "CHEMBL112", "CHEMBL521"]  # aspirin, paracetamol, ibuprofen — a stable watchlist


class ChEMBLConnector(Connector):
    source = "chembl"

    def __init__(self, chembl_ids: list[str], **kwargs):
        super().__init__(**kwargs)
        self.chembl_ids = chembl_ids

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=f"{chembl_id}.smi",
                uri=CHEMBL_MOLECULE_URL.format(chembl_id=chembl_id),
                modality="molecule",
                metadata={"chembl_id": chembl_id},
            )
            for chembl_id in self.chembl_ids
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        resp = requests.get(item.uri, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        smiles = data["molecule_structures"]["canonical_smiles"]
        content = f"{smiles} {item.metadata['chembl_id']}\n".encode("utf-8")
        return FetchedItem(name=item.name, content=content, modality=item.modality, metadata=item.metadata)

    def validate(self, item: FetchedItem) -> bool:
        """Input-side check: did we actually get a SMILES string back?"""
        return bool(item.content.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="chembl-nightly-sync")
    parser.add_argument("--owner", default="chembl-connector")
    parser.add_argument(
        "--chembl-ids",
        default=os.environ.get("CHEMBL_IDS", ",".join(DEFAULT_CHEMBL_IDS)),
        help="comma-separated ChEMBL molecule IDs",
    )
    args = parser.parse_args()

    chembl_ids = [c.strip() for c in args.chembl_ids.split(",") if c.strip()]
    landed = ChEMBLConnector(chembl_ids=chembl_ids).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
