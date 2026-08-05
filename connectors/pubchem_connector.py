"""PubChem connector — public-database connector (BUILD_PLAN.md §3, Phase 7).

Fetches a given set of PubChem CIDs as canonical SMILES via the PubChem
PUG-REST API, landing each as a .smi file. Scheduled nightly via
workflows/argo/pubchem-sync-cronworkflow.yaml.
"""

import argparse
import os

import requests

from connectors.base import Connector, DiscoveredItem, FetchedItem

PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/CanonicalSMILES/JSON"

DEFAULT_CIDS = ["2244", "1983", "3672"]  # aspirin, acetaminophen, ibuprofen — a stable watchlist


class PubChemConnector(Connector):
    source = "pubchem"

    def __init__(self, cids: list[str], **kwargs):
        super().__init__(**kwargs)
        self.cids = cids

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=f"{cid}.smi",
                uri=PUBCHEM_URL.format(cid=cid),
                modality="molecule",
                metadata={"cid": cid},
            )
            for cid in self.cids
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        resp = requests.get(item.uri, timeout=30)
        resp.raise_for_status()
        props = resp.json()["PropertyTable"]["Properties"][0]
        # PubChem has renamed this property's response key before (observed
        # returning ConnectivitySMILES for a CanonicalSMILES request) — take
        # whichever SMILES-shaped key actually came back rather than assuming.
        smiles = props.get("CanonicalSMILES") or props.get("ConnectivitySMILES") or props.get("IsomericSMILES")
        if not smiles:
            raise ValueError(f"no SMILES property in PubChem response for CID {item.metadata['cid']}: {props}")
        content = f"{smiles} {item.metadata['cid']}\n".encode("utf-8")
        return FetchedItem(name=item.name, content=content, modality=item.modality, metadata=item.metadata)

    def validate(self, item: FetchedItem) -> bool:
        """Input-side check: did we actually get a SMILES string back?"""
        return bool(item.content.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="pubchem-nightly-sync")
    parser.add_argument("--owner", default="pubchem-connector")
    parser.add_argument(
        "--cids",
        default=os.environ.get("PUBCHEM_CIDS", ",".join(DEFAULT_CIDS)),
        help="comma-separated PubChem CIDs",
    )
    args = parser.parse_args()

    cids = [c.strip() for c in args.cids.split(",") if c.strip()]
    landed = PubChemConnector(cids=cids).run(dataset_id=args.dataset_id, owner=args.owner)
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
