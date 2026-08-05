"""Input validation for FASTA sequence files — BUILD_PLAN.md §6, input gate.

Cheap, format-only checks before any compute is spent. Deep sequence handling
(tokenization, MSA) belongs to engine/pipelines/sequence_pipeline (Phase 3).
"""

_VALID_RESIDUES = set("ACDEFGHIKLMNPQRSTVWYXBZJUO*-GTUN")  # superset covering protein + nucleotide alphabets


def validate(content: bytes) -> tuple[bool, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not valid utf-8 text"

    if not text.strip():
        return False, "empty file"

    if not text.lstrip().startswith(">"):
        return False, "does not start with a FASTA header ('>')"

    records = 0
    for raw_record in text.split(">")[1:]:
        records += 1
        lines = raw_record.splitlines()
        header, seq_lines = lines[0], lines[1:]
        if not header.strip():
            return False, "empty FASTA header"
        sequence = "".join(seq_lines).strip()
        if not sequence:
            return False, f"record '{header.strip()}' has no sequence"
        bad_chars = set(sequence.upper()) - _VALID_RESIDUES
        if bad_chars:
            return False, f"record '{header.strip()}' contains invalid residues: {''.join(sorted(bad_chars))}"

    if records == 0:
        return False, "no FASTA records found"

    return True, None
