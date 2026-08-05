"""Mol2 input -> canonical MoleculeRecord, via RDKit's Mol2 parser."""

from rdkit import Chem

from engine.pipelines.molecule_pipeline.canonical import MoleculeParseError, MoleculeRecord, from_mol


def parse(content: bytes) -> list[MoleculeRecord]:
    mol = Chem.MolFromMol2Block(content.decode("utf-8"))
    if mol is None:
        raise MoleculeParseError("RDKit could not parse Mol2 block")
    return [from_mol(mol)]
