"""The canonical shape every tabular_pipeline adapter converts into —
BUILD_PLAN.md §4a. One record per data row (mirrors sequence_pipeline's one
record per FASTA entry): featurization is written once against
`TabularRecord`, a new delimited format only needs an adapter that produces
one.
"""

from dataclasses import dataclass


@dataclass
class TabularRecord:
    name: str
    columns: list[str]
    values: list[str]
