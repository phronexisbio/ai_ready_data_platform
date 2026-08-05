"""Input validation for SMILES-format molecule files — BUILD_PLAN.md §6, input gate.

Character-set / non-empty checks only. Real parsing (RDKit canonicalization,
sanitization) belongs to engine/pipelines/molecule_pipeline (Phase 3) — this
just catches obviously malformed input before it reaches that expensive step.
"""

import re

_SMILES_CHARS = re.compile(r"^[A-Za-z0-9@+\-\[\]()=#$:/\\.%]+$")


def validate(content: bytes) -> tuple[bool, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not valid utf-8 text"

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False, "empty file"

    for i, line in enumerate(lines, start=1):
        token = line.split()[0]
        if not _SMILES_CHARS.match(token):
            return False, f"line {i}: '{token}' contains characters not valid in SMILES"

    return True, None
