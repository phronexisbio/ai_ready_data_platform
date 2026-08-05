"""Input validation for PDB/mmCIF structure files — BUILD_PLAN.md §6, input gate.

Cheap, format-only checks before any compute is spent: can this be parsed at
all, and does it contain at least one atom record. Deep structural handling
(backbone extraction, SE(3) frames) belongs to engine/pipelines/structure_pipeline
(Phase 4).
"""

import io

from Bio.PDB import MMCIFParser, PDBParser


def _looks_like_mmcif(text: str) -> bool:
    return text.lstrip().startswith("data_") or "_atom_site." in text


def validate(content: bytes) -> tuple[bool, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not valid utf-8 text"

    if not text.strip():
        return False, "empty file"

    parser = MMCIFParser(QUIET=True) if _looks_like_mmcif(text) else PDBParser(QUIET=True)
    try:
        structure = parser.get_structure("structure", io.StringIO(text))
        atom_count = sum(1 for _ in structure.get_atoms())
    except Exception as e:
        return False, f"could not parse structure file: {e}"

    if atom_count == 0:
        return False, "no atom records found"

    return True, None
