"""Shared framework-independent helpers."""

import csv
import io


def csv_safe(value):
    """Prevent spreadsheet formulas from executing when a CSV is opened."""
    value = "" if value is None else str(value)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_datetime(value):
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value)


def csv_buffer():
    """Return a UTF-8 CSV buffer for report services."""
    return io.StringIO(newline="")
