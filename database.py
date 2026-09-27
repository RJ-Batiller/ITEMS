"""Database connection and schema initialization for the raw-SQL application."""

from threading import Lock

import mysql.connector
from mysql.connector import pooling

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
_profile_schema_ready = False
_message_schema_ready = False
_connection_pool = None
_pool_lock = Lock()
_schema_lock = Lock()


def ensure_profile_schema(cur):
    """Add profile image columns when an older production schema is detected."""
    cur.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'users'
          AND COLUMN_NAME IN ('profile_picture_data', 'profile_picture_mime')
        """,
        (Config.DB_NAME,),
    )
    existing = {row[0] for row in cur.fetchall()}
    if "profile_picture_data" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN profile_picture_data LONGBLOB NULL")
    if "profile_picture_mime" not in existing:
        cur.execute("ALTER TABLE users ADD COLUMN profile_picture_mime VARCHAR(120) NULL")


def ensure_message_schema(cur):
    """Keep older production databases compatible with persistent read markers."""
    cur.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'users'
          AND COLUMN_NAME = 'messages_seen_at'
        """,
        (Config.DB_NAME,),
    )
    if not cur.fetchone():
        cur.execute("ALTER TABLE users ADD COLUMN messages_seen_at DATETIME NULL AFTER login_count")
        cur.execute("UPDATE users SET messages_seen_at = CURRENT_TIMESTAMP WHERE messages_seen_at IS NULL")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_group_reads (
            group_id INT NOT NULL,
            user_id INT NOT NULL,
            last_read_message_id BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        INSERT INTO chat_group_reads (group_id, user_id, last_read_message_id)
        SELECT gm.group_id, gm.user_id, COALESCE(MAX(m.id), 0)
        FROM chat_group_members gm
        LEFT JOIN chat_messages m ON m.group_id = gm.group_id
        GROUP BY gm.group_id, gm.user_id
        ON DUPLICATE KEY UPDATE last_read_message_id = chat_group_reads.last_read_message_id
        """
    )


def get_connection():
    """Get a pooled MySQL connection with a bounded connection wait."""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = pooling.MySQLConnectionPool(
                    pool_name="items_db_pool",
                    pool_size=Config.DB_POOL_SIZE,
                    pool_reset_session=True,
                    connection_timeout=Config.DB_CONNECT_TIMEOUT,
                    host=Config.DB_HOST,
                    port=Config.DB_PORT,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    database=Config.DB_NAME,
                )

    conn = _connection_pool.get_connection()
    # MySQL TIMESTAMP values are converted using the connection timezone.
    # Keep database reads and CURRENT_TIMESTAMP writes on Philippine time.
    cursor = conn.cursor()
    try:
        cursor.execute("SET time_zone = '+08:00'")
    finally:
        cursor.close()
    return conn


def get_db_connection():
    """Open a connection and ensure the maintenance table exists once."""
    global _maintenance_schema_ready, _profile_schema_ready, _message_schema_ready
    conn = get_connection()
    if not _maintenance_schema_ready:
        with _schema_lock:
            if not _maintenance_schema_ready:
                cur = conn.cursor()
                try:
                    cur.execute(MAINTENANCE_SCHEMA_SQL)
                    ensure_profile_schema(cur)
                    ensure_message_schema(cur)
                    conn.commit()
                    _maintenance_schema_ready = True
                    _profile_schema_ready = True
                    _message_schema_ready = True
                finally:
                    cur.close()
    return conn
