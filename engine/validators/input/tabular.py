"""Input validation for CSV/TSV tabular files — BUILD_PLAN.md §6, input gate."""

import csv
import io


def validate(content: bytes) -> tuple[bool, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not valid utf-8 text"

    if not text.strip():
        return False, "empty file"

    first_line = text.splitlines()[0]
    dialect = csv.excel_tab if "\t" in first_line else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect=dialect))

    if len(rows) < 2:
        return False, "no data rows (header only or empty)"

    header = rows[0]
    if not header or any(not col.strip() for col in header):
        return False, "malformed or empty header"

    ncols = len(header)
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != ncols:
            return False, f"row {i} has {len(row)} columns, expected {ncols}"

    return True, None
