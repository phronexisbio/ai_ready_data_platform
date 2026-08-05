"""SMILES input (.smi: one `SMILES [name]` per line) -> canonical MoleculeRecord."""

from engine.pipelines.molecule_pipeline.canonical import MoleculeRecord, from_smiles


def parse(content: bytes) -> list[MoleculeRecord]:
    text = content.decode("utf-8")
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        smiles, name = parts[0], (parts[1] if len(parts) > 1 else "")
        records.append(from_smiles(smiles, name=name))
    return records
