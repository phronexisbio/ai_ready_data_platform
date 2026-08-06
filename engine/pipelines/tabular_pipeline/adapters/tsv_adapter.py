"""TSV input -> canonical TabularRecord(s), one per data row. Same shape as
csv_adapter.py, just tab-delimited — kept as its own module (rather than a
delimiter parameter on one adapter) to match this codebase's one-module-per-
input-format convention (see molecule_pipeline's smiles/sdf/inchi/mol2).
"""

import csv
import io

from engine.pipelines.tabular_pipeline.canonical import TabularRecord


def parse(content: bytes) -> list[TabularRecord]:
    text = content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text), dialect=csv.excel_tab))
    header, data_rows = rows[0], rows[1:]
    return [TabularRecord(name=f"row_{i}", columns=header, values=row) for i, row in enumerate(data_rows, start=1)]
