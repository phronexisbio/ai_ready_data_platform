"""PDB input -> canonical StructureRecord, via Biopython.

Waters and other heteroatoms are stripped by construction: Biopython only
assigns the ' ' (blank) hetero flag to standard residues, so filtering on
that excludes HETATM records — including water — without a separate resname
denylist.
"""

import io

from Bio.PDB import PDBParser

from engine.pipelines.structure_pipeline.canonical import Residue, StructureRecord


def _extract(structure, name: str) -> StructureRecord:
    residues = []
    model = next(iter(structure))
    for chain in model:
        for res in chain:
            if res.id[0] != " ":
                continue  # HETATM (waters, ligands, ...) — not part of the backbone
            coords = {atom.get_name(): tuple(float(v) for v in atom.get_coord()) for atom in res}
            residues.append(
                Residue(
                    name=res.resname,
                    seq_id=res.id[1],
                    chain_id=chain.id,
                    n=coords.get("N"),
                    ca=coords.get("CA"),
                    c=coords.get("C"),
                    o=coords.get("O"),
                )
            )
    return StructureRecord(name=name, residues=residues)


def parse(content: bytes) -> list[StructureRecord]:
    text = content.decode("utf-8")
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("structure", io.StringIO(text))
    return [_extract(structure, name="structure")]
