"""Local/manual connector — analyst-provided batches (BUILD_PLAN.md §3).

The one connector every "no wet lab" engagement still needs: an analyst drops
a folder of files, this picks them up and runs them through the same
land -> register -> emit path as every scheduled connector.
"""

from pathlib import Path

from connectors.base import Connector, DiscoveredItem, FetchedItem

_MODALITY_BY_SUFFIX = {
    ".fasta": "sequence",
    ".fa": "sequence",
    ".fna": "sequence",
    ".pdb": "structure",
    ".cif": "structure",
    ".mmcif": "structure",
    ".smi": "molecule",
    ".sdf": "molecule",
    ".mol2": "molecule",
    ".inchi": "molecule",
    ".tif": "image",
    ".tiff": "image",
    ".csv": "tabular",
    ".tsv": "tabular",
    ".parquet": "tabular",
    ".xlsx": "tabular",
    ".json": "text",
    ".txt": "text",
}


def guess_modality(path: Path) -> str:
    return _MODALITY_BY_SUFFIX.get(path.suffix.lower(), "unknown")


class LocalConnector(Connector):
    source = "local"

    def __init__(self, batch_dir: str, **kwargs):
        super().__init__(**kwargs)
        self.batch_dir = Path(batch_dir)

    def discover(self) -> list[DiscoveredItem]:
        if not self.batch_dir.is_dir():
            raise FileNotFoundError(f"batch directory not found: {self.batch_dir}")
        return [
            DiscoveredItem(name=p.name, uri=str(p), modality=guess_modality(p))
            for p in sorted(self.batch_dir.iterdir())
            if p.is_file()
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        content = Path(item.uri).read_bytes()
        return FetchedItem(name=item.name, content=content, modality=item.modality)
