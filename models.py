"""Data-model compatibility exports.

This project uses raw SQL rather than an ORM. Database connectivity lives in
``database.py`` while this module remains available for older imports.
"""

from database import MAINTENANCE_SCHEMA_SQL, get_connection, get_db_connection


__all__ = ["MAINTENANCE_SCHEMA_SQL", "get_connection", "get_db_connection"]
