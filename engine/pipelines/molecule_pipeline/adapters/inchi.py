"""InChI input (one InChI string per line) -> canonical MoleculeRecord."""

from rdkit import Chem

from engine.pipelines.molecule_pipeline.canonical import MoleculeParseError, MoleculeRecord, from_mol


def parse(content: bytes) -> list[MoleculeRecord]:
    text = content.decode("utf-8")
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        mol = Chem.MolFromInchi(line)
        if mol is None:
            raise MoleculeParseError(f"RDKit could not parse InChI: {line!r}")
        records.append(from_mol(mol))
    return records
