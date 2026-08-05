"""The canonical shape every sequence_pipeline adapter converts into —
BUILD_PLAN.md §4a. Featurization (featurize.py) is written once against
`SequenceRecord`; a new input format only needs an adapter that produces one.
"""

from dataclasses import dataclass

_NUCLEOTIDE_CHARS = set("ACGTUN")


@dataclass
class SequenceRecord:
    name: str
    sequence: str
    alphabet: str  # "protein" or "nucleotide"


def guess_alphabet(sequence: str) -> str:
    chars = set(sequence.upper())
    return "nucleotide" if chars and chars <= _NUCLEOTIDE_CHARS else "protein"
