"""SDF input -> canonical MoleculeRecord, via RDKit's SDMolSupplier."""

import tempfile

from rdkit import Chem

from engine.pipelines.molecule_pipeline.canonical import MoleculeParseError, MoleculeRecord, from_mol


def parse(content: bytes) -> list[MoleculeRecord]:
    records = []
    with tempfile.NamedTemporaryFile(suffix=".sdf", mode="wb") as f:
        f.write(content)
        f.flush()
        supplier = Chem.SDMolSupplier(f.name)
        for mol in supplier:
            if mol is None:
                continue
            name = mol.GetProp("_Name") if mol.HasProp("_Name") else ""
            records.append(from_mol(mol, name=name))
    if not records:
        raise MoleculeParseError("no valid molecules found in SDF")
    return records
