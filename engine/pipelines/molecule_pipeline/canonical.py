"""The canonical shape every molecule_pipeline adapter converts into, and the
RDKit canonicalization every input format shares — BUILD_PLAN.md §4a.

Featurization (featurize.py) is written once against `MoleculeRecord`; a new
input format only needs an adapter that produces one.
"""

from dataclasses import dataclass

from rdkit import Chem


@dataclass
class MoleculeRecord:
    name: str
    canonical_smiles: str
    mol: Chem.Mol  # sanitized RDKit mol, ready for featurization


class MoleculeParseError(ValueError):
    pass


def from_smiles(smiles: str, name: str = "") -> MoleculeRecord:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise MoleculeParseError(f"RDKit could not parse SMILES: {smiles!r}")
    return from_mol(mol, name=name)


def from_mol(mol: Chem.Mol, name: str = "") -> MoleculeRecord:
    if mol is None:
        raise MoleculeParseError("RDKit mol is None")
    canonical_smiles = Chem.MolToSmiles(mol, canonical=True)
    return MoleculeRecord(name=name or canonical_smiles, canonical_smiles=canonical_smiles, mol=mol)
