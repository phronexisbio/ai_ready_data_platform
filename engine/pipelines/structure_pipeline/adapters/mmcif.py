"""mmCIF/PDBx input -> canonical StructureRecord, via Biopython.

Same extraction logic as the PDB adapter (adapters/pdb.py) — Biopython's
MMCIFParser produces the identical Structure/Model/Chain/Residue/Atom object
hierarchy as PDBParser, so both adapters share one code path for turning
that hierarchy into a StructureRecord.
"""

import io

from Bio.PDB import MMCIFParser

from engine.pipelines.structure_pipeline.adapters.pdb import _extract
from engine.pipelines.structure_pipeline.canonical import StructureRecord


def parse(content: bytes) -> list[StructureRecord]:
    text = content.decode("utf-8")
    parser = MMCIFParser(QUIET=True)
    structure = parser.get_structure("structure", io.StringIO(text))
    return [_extract(structure, name="structure")]
