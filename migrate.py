"""Apply numbered SQL migrations to the ITEMS database."""

import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


load_dotenv()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "items_db"),
    )


def apply_migrations():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(120) PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    cur.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cur.fetchall()}

    for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration_path.name
        if version in applied:
            continue

        statements = [
            statement.strip()
            for statement in migration_path.read_text(encoding="utf-8").split(";")
            if statement.strip()
        ]
        try:
            for statement in statements:
                cur.execute(statement)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (version,),
            )
            conn.commit()
            print(f"Applied migration: {version}")
        except Exception:
            conn.rollback()
            raise

    cur.close()
    conn.close()
    print("Database is up to date.")


if __name__ == "__main__":
    apply_migrations()
