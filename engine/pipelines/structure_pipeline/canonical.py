"""The canonical shape every structure_pipeline adapter converts into —
BUILD_PLAN.md §4a. Only backbone atoms (N, CA, C, O) are kept per residue —
enough to build a residue graph or SE(3) frame tensor (§11); side-chain atoms
aren't needed for either representation Phase 4 implements.
"""

from dataclasses import dataclass

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
}


@dataclass
class Residue:
    name: str  # 3-letter code, e.g. ALA
    seq_id: int
    chain_id: str
    n: tuple[float, float, float] | None
    ca: tuple[float, float, float] | None
    c: tuple[float, float, float] | None
    o: tuple[float, float, float] | None

    @property
    def has_full_backbone(self) -> bool:
        return self.n is not None and self.ca is not None and self.c is not None


@dataclass
class StructureRecord:
    name: str
    residues: list[Residue]

    @property
    def one_letter_sequence(self) -> str:
        """A structure's residue sequence, one-letter coded — the closest
        thing a structure has to a molecule's canonical SMILES: a compact,
        deterministic string identifying what was parsed."""
        return "".join(THREE_TO_ONE.get(r.name, "X") for r in self.residues)
