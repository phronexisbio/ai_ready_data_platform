"""CSV input -> canonical TabularRecord(s), one per data row.

Ragged rows are already rejected at input validation
(engine/validators/input/tabular.py checks every row has the header's column
count) before a file ever reaches this adapter, so no re-validation here.
"""

import csv
import io

from engine.pipelines.tabular_pipeline.canonical import TabularRecord


def parse(content: bytes) -> list[TabularRecord]:
    text = content.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    header, data_rows = rows[0], rows[1:]
    return [TabularRecord(name=f"row_{i}", columns=header, values=row) for i, row in enumerate(data_rows, start=1)]
