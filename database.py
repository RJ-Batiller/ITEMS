"""Database connection and schema initialization for the raw-SQL application."""

import mysql.connector

from config import Config


MAINTENANCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS maintenance_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    started_by INT NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    remarks TEXT NOT NULL,
    status ENUM('Open','Completed') NOT NULL DEFAULT 'Open',
    completed_by INT,
    completed_at DATETIME NULL,
    completion_remarks TEXT,
    INDEX idx_maintenance_equipment_status (equipment_id, status),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY (started_by) REFERENCES users(id),
    FOREIGN KEY (completed_by) REFERENCES users(id)
)
"""

_maintenance_schema_ready = False


def get_connection():
    """Open a configured MySQL connection."""
    return mysql.connector.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database=Config.DB_NAME,
    )


def get_db_connection():
    """Open a connection and ensure the maintenance table exists once."""
    global _maintenance_schema_ready
    conn = get_connection()
    if not _maintenance_schema_ready:
        cur = conn.cursor()
        try:
            cur.execute(MAINTENANCE_SCHEMA_SQL)
            conn.commit()
            _maintenance_schema_ready = True
        finally:
            cur.close()
    return conn
