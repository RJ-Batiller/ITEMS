"""Reusable report-generation services."""

import csv
from io import StringIO


def build_csv(headers, rows):
    """Build a UTF-8 CSV document with a BOM for spreadsheet compatibility."""
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()
