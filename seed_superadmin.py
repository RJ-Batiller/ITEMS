import os

import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


load_dotenv(dotenv_path=".env")

username = os.getenv("SUPERADMIN_USERNAME", "superadmin1")
password = os.getenv("SUPERADMIN_PASSWORD")
full_name = os.getenv("SUPERADMIN_FULL_NAME", "System Super Admin")
email = os.getenv("SUPERADMIN_EMAIL", "admin@mcc.edu.ph")

if not password:
    raise RuntimeError("SUPERADMIN_PASSWORD must be set in .env before seeding.")

connection = mysql.connector.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "items_db"),
)

try:
    cursor = connection.cursor(dictionary=True)

    cursor.execute(
        """
        INSERT INTO roles (name, description)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE description = VALUES(description)
        """,
        ("Super Admin", "Highest-level system administrator"),
    )

    cursor.execute("SELECT id FROM roles WHERE name = %s", ("Super Admin",))
    role_id = cursor.fetchone()["id"]

    cursor.execute(
        "SELECT id FROM users WHERE username = %s",
        (username,),
    )
    existing_user = cursor.fetchone()
    password_hash = generate_password_hash(password)

    if existing_user:
        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s,
                full_name = %s,
                email = %s,
                role_id = %s,
                is_active = 1
            WHERE id = %s
            """,
            (password_hash, full_name, email, role_id, existing_user["id"]),
        )
        action = "updated"
    else:
        cursor.execute(
            """
            INSERT INTO users
                (username, password_hash, full_name, email, role_id, is_active)
            VALUES (%s, %s, %s, %s, %s, 1)
            """,
            (username, password_hash, full_name, email, role_id),
        )
        action = "created"

    connection.commit()
    print(f"Super Admin account {action}: {username}")
finally:
    cursor.close()
    connection.close()