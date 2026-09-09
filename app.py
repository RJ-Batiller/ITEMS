import os
import hmac
import csv
import secrets
from functools import wraps
from datetime import date

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_file,
    make_response,
    g
)

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import mysql.connector
from mysql.connector.errors import IntegrityError
from dotenv import load_dotenv

import qrcode
from io import BytesIO, StringIO


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


def env_flag(name, default=False):

    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on"
    }


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

secret_key = os.getenv("SECRET_KEY")

if not secret_key:

    raise RuntimeError(
        "SECRET_KEY must be set in the environment or .env file."
    )

app.secret_key = secret_key
app.config.update(
    DEBUG=env_flag("APP_DEBUG"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("SESSION_COOKIE_SECURE"),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024
)

PROFILE_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads", "profiles")
PROFILE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


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


@app.context_processor
def inject_security_helpers():
    def csrf_token():
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    return {"csrf_token": csrf_token}


@app.before_request
def protect_state_changing_requests():
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            return "Invalid or missing CSRF token.", 400


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; frame-ancestors 'self'")
    return response


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db():
    global _maintenance_schema_ready

    conn = mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "items_db")
    )

    if not _maintenance_schema_ready:
        cur = conn.cursor()
        try:
            cur.execute(MAINTENANCE_SCHEMA_SQL)
            conn.commit()
            _maintenance_schema_ready = True
        finally:
            cur.close()

    return conn

VALID_EQUIPMENT_STATUSES = {
    "Available",
    "Under Maintenance",
    "Assigned",
    "Archived",
    "Disposed"
}

TRANSACTION_ACTIONS = (
    "Created",
    "Updated",
    "Assigned",
    "Unassigned",
    "Maintenance",
    "Repair",
    "Archived",
    "Restored",
    "Disposed",
)

MANAGEMENT_ROLES = {"super admin", "admin"}
EQUIPMENT_WRITE_ACTIONS = {
    "add",
    "edit",
    "archive",
    "restore",
    "dispose",
    "assign",
    "unassign",
    "maintenance",
}


def validate_status_change(old_status, new_status):
    if new_status not in VALID_EQUIPMENT_STATUSES:
        return False
    if old_status in {"Archived", "Disposed"}:
        return new_status == old_status
    if old_status == "Under Maintenance" or new_status == "Under Maintenance":
        return old_status == new_status
    if new_status == "Assigned":
        return old_status == "Assigned"
    return True


def clean_text(value, field_name, max_length):
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{field_name} is required.")
    if len(value) > max_length:
        raise ValueError(f"{field_name} must be {max_length} characters or fewer.")
    return value


def optional_text(value, max_length):
    value = (value or "").strip()
    if len(value) > max_length:
        raise ValueError(f"Text fields must be {max_length} characters or fewer.")
    return value


def parse_optional_date(value, field_name):
    value = (value or "").strip()
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Please provide a valid {field_name}.") from error
    return value


def csv_safe(value):
    """Prevent spreadsheet formulas from executing when a CSV is opened."""
    value = "" if value is None else str(value)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def can_perform_action(role_name, action):
    """Return whether a role may perform a named application action."""
    role = (role_name or "").strip().lower()
    if action == "manage_users":
        return role in MANAGEMENT_ROLES
    if action in EQUIPMENT_WRITE_ACTIONS:
        return role in MANAGEMENT_ROLES
    return action in {"view", "view_transactions", "view_qr"} and bool(role)


def can_start_maintenance(status):
    return status not in {"Assigned", "Archived", "Disposed"}


def can_complete_maintenance(has_open_record):
    return bool(has_open_record)


# ============================================================
# LOGIN REQUIRED
# ============================================================

def login_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user = get_current_user()
        if not user:
            session.clear()
            return redirect(url_for("login"))

        g.current_user = user

        return fn(*args, **kwargs)

    return wrapper


# ============================================================
# WORKER CHECK
# ============================================================

def is_worker():
    user = get_current_user()
    return user and user["role_name"].strip().lower() == "worker"


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.username, u.full_name, u.email, u.profile_picture, u.is_active, r.name AS role_name
        FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s AND u.is_active = 1
    """, (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user


def super_admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if g.current_user["role_name"].strip().lower() != "super admin":
            flash("Super Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)

    return wrapper


def user_management_required(fn):
    """Allow only Super Admin and Admin accounts to manage users."""
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not can_perform_action(g.current_user["role_name"], "manage_users"):
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)

    return wrapper


def creatable_roles(role_name):
    """Return role names that an account may assign to a new user."""
    role = role_name.strip().lower()
    if role == "super admin":
        return ("Admin", "Worker")
    if role == "admin":
        return ("Worker",)
    return ()


def manageable_roles(role_name):
    """Return account roles this manager may edit, excluding their own role."""
    role = (role_name or "").strip().lower()
    if role == "super admin":
        return ("Admin", "Worker")
    if role == "admin":
        return ("Worker",)
    return ()


# ============================================================
# ADMIN / MANAGEMENT REQUIRED
# Worker accounts cannot modify equipment/categories
# ============================================================

def admin_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user = get_current_user()
        if not user:
            session.clear()
            return redirect(url_for("login"))

        g.current_user = user
        if not can_perform_action(user["role_name"], "edit"):

            flash(
                "Worker accounts have view-only access.",
                "danger"
            )

            return redirect(
                url_for("equipment")
            )

        return fn(*args, **kwargs)

    return wrapper


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = db()
        cur = conn.cursor(dictionary=True)

        cur.execute("""
            SELECT
                u.*,
                r.name AS role_name
            FROM users u
            JOIN roles r
                ON u.role_id = r.id
            WHERE u.username = %s
              AND u.is_active = 1
        """, (username,))

        user = cur.fetchone()

        cur.close()
        conn.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ):

            session.clear()
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role_name"]
            session["csrf_token"] = secrets.token_urlsafe(32)

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid username or password.",
            "danger"
        )

    return render_template("login.html")


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT status, COUNT(*) AS status_count
        FROM equipment
        GROUP BY status
    """)
    status_counts = {
        row["status"]: row["status_count"]
        for row in cur.fetchall()
    }

    total = sum(
        count for status, count in status_counts.items()
        if status not in {"Archived", "Disposed"}
    )
    available = status_counts.get("Available", 0)
    assigned = status_counts.get("Assigned", 0)
    maintenance = status_counts.get("Under Maintenance", 0)
    archived = status_counts.get("Archived", 0)
    disposed = status_counts.get("Disposed", 0)

    cur.execute("""
        SELECT
            t.created_at,
            t.action,
            t.details,
            e.id AS equipment_id,
            e.asset_code,
            e.name AS equipment_name,
            u.full_name
        FROM transactions t
        JOIN equipment e ON e.id = t.equipment_id
        JOIN users u ON u.id = t.user_id
        ORDER BY t.created_at DESC
        LIMIT 8
    """)
    recent_transactions = cur.fetchall()

    cur.execute("""
        SELECT
            m.id,
            m.started_at,
            m.remarks,
            e.id AS equipment_id,
            e.asset_code,
            e.name AS equipment_name
        FROM maintenance_records m
        JOIN equipment e ON e.id = m.equipment_id
        WHERE m.status = 'Open'
        ORDER BY m.started_at DESC
        LIMIT 5
    """)
    open_maintenance = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        available=available,
        assigned=assigned,
        maintenance=maintenance,
        archived=archived,
        disposed=disposed,
        recent_transactions=recent_transactions,
        open_maintenance=open_maintenance
    )


# ============================================================
# EQUIPMENT LIST
# ============================================================

@app.route("/equipment")
@login_required
def equipment():

    q = request.args.get(
        "q",
        ""
    ).strip()
    category_id = request.args.get("category", "").strip()
    office_id = request.args.get("office", "").strip()
    status = request.args.get("status", "").strip()
    try:
        category_id = int(category_id) if category_id else None
    except ValueError:
        category_id = None
    try:
        office_id = int(office_id) if office_id else None
    except ValueError:
        office_id = None
    if status not in VALID_EQUIPMENT_STATUSES:
        status = ""

    conn = db()
    cur = conn.cursor(dictionary=True)

    sql = """
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            a.person_name AS accountable_person
        FROM equipment e

        LEFT JOIN categories c
            ON e.category_id = c.id

        LEFT JOIN offices o
            ON e.office_id = o.id

        LEFT JOIN accountability a
            ON e.id = a.equipment_id
            AND a.is_current = 1
    """

    filters = []
    params = []

    if category_id is not None:
        filters.append("e.category_id = %s")
        params.append(category_id)

    if office_id is not None:
        filters.append("e.office_id = %s")
        params.append(office_id)

    if status:
        filters.append("e.status = %s")
        params.append(status)

    if q:

        filters.append("""
            (
                e.asset_code LIKE %s
                OR e.name LIKE %s
                OR e.serial_number LIKE %s
                OR c.name LIKE %s
                OR o.name LIKE %s
                OR e.status LIKE %s
                OR a.person_name LIKE %s
            )
        """)

        search = f"%{q}%"

        params.extend([
            search,
            search,
            search,
            search,
            search,
            search,
            search
        ])

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += """
        ORDER BY e.id DESC
    """

    cur.execute(
        sql,
        params
    )

    rows = cur.fetchall()

    if request.args.get("export") == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "Asset Code",
            "Equipment",
            "Category",
            "Office",
            "Serial Number",
            "Status",
            "Accountable Person",
            "Acquisition Date",
        ])
        for row in rows:
            writer.writerow([
                csv_safe(row["asset_code"]),
                csv_safe(row["name"]),
                csv_safe(row["category_name"]),
                csv_safe(row["office_name"]),
                csv_safe(row["serial_number"]),
                csv_safe(row["status"]),
                csv_safe(row["accountable_person"]),
                csv_safe(row["acquisition_date"]),
            ])
        response = make_response("\ufeff" + output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=items-equipment-report.csv"
        return response

    cur.execute("""
        SELECT
            c.id,
            c.name,
            COUNT(e.id) AS equipment_count
        FROM categories c
        LEFT JOIN equipment e
            ON e.category_id = c.id
        GROUP BY c.id, c.name
        ORDER BY c.name
    """)

    categories = cur.fetchall()
    selected_category_name = next(
        (
            category["name"]
            for category in categories
            if category["id"] == category_id
        ),
        None
    )

    cur.execute("""
        SELECT id, name
        FROM offices
        ORDER BY name
    """)

    offices = cur.fetchall()
    selected_office_name = next(
        (
            office["name"]
            for office in offices
            if office["id"] == office_id
        ),
        None
    )

    cur.execute("""
        SELECT status, COUNT(*) AS status_count
        FROM equipment
        GROUP BY status
    """)
    status_counts = {
        row["status"]: row["status_count"]
        for row in cur.fetchall()
    }

    cur.close()
    conn.close()

    if request.args.get("print") == "1":
        return render_template(
            "equipment_print.html",
            equipment=rows,
            q=q,
            selected_category_name=selected_category_name,
            selected_status=status,
            selected_office_id=office_id,
            selected_office_name=selected_office_name
        )

    return render_template(
        "equipment.html",
        equipment=rows,
        categories=categories,
        offices=offices,
        q=q,
        selected_category_id=category_id,
        selected_category_name=selected_category_name,
        selected_office_id=office_id,
        selected_office_name=selected_office_name,
        selected_status=status,
        status_counts=status_counts
    )


# ============================================================
# VIEW EQUIPMENT
# Worker is allowed
# ============================================================

@app.route("/equipment/view/<int:item_id>")
@login_required
def view_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            a.person_name AS accountable_person
        FROM equipment e

        LEFT JOIN categories c
            ON e.category_id = c.id

        LEFT JOIN offices o
            ON e.office_id = o.id

        LEFT JOIN accountability a
            ON e.id = a.equipment_id
            AND a.is_current = 1

        WHERE e.id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    cur.execute("""
        SELECT
            m.*,
            started.full_name AS started_by_name,
            completed.full_name AS completed_by_name
        FROM maintenance_records m
        JOIN users started ON started.id = m.started_by
        LEFT JOIN users completed ON completed.id = m.completed_by
        WHERE m.equipment_id = %s
        ORDER BY m.started_at DESC
    """, (item_id,))
    maintenance_history = cur.fetchall()
    maintenance_record = next(
        (
            record for record in maintenance_history
            if record["status"] == "Open"
        ),
        None
    )

    cur.close()
    conn.close()

    if not equipment:

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    return render_template(
        "equipment_view.html",
        equipment=equipment,
        is_worker=is_worker(),
        maintenance_record=maintenance_record,
        maintenance_history=maintenance_history
    )


# ============================================================
# MAINTENANCE WORKFLOW
# ============================================================

@app.route(
    "/equipment/<int:item_id>/maintenance",
    methods=["GET", "POST"]
)
@admin_required
def equipment_maintenance(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status
        FROM equipment
        WHERE id = %s
    """, (item_id,))
    equipment = cur.fetchone()

    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    cur.execute("""
        SELECT *
        FROM maintenance_records
        WHERE equipment_id = %s AND status = 'Open'
        ORDER BY started_at DESC
        LIMIT 1
    """, (item_id,))
    open_record = cur.fetchone()

    if request.method == "GET":
        cur.close()
        conn.close()
        return render_template(
            "equipment_maintenance.html",
            equipment=equipment,
            maintenance_record=open_record
        )

    maintenance_action = request.form.get("maintenance_action", "start")
    remarks = request.form.get("remarks", "").strip()

    if not remarks:
        cur.close()
        conn.close()
        flash("Maintenance remarks are required.", "danger")
        return redirect(url_for("equipment_maintenance", item_id=item_id))

    if maintenance_action == "start":
        if open_record:
            cur.close()
            conn.close()
            flash("This equipment already has an open maintenance record.", "danger")
            return redirect(url_for("equipment_maintenance", item_id=item_id))
        if not can_start_maintenance(equipment["status"]):
            cur.close()
            conn.close()
            flash("Assigned, archived, or disposed equipment cannot enter maintenance.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))

        cur.execute("""
            INSERT INTO maintenance_records
                (equipment_id, started_by, remarks)
            VALUES (%s, %s, %s)
        """, (item_id, session["user_id"], remarks))
        cur.execute("""
            UPDATE equipment
            SET status = 'Under Maintenance'
            WHERE id = %s
        """, (item_id,))
        cur.execute("""
            INSERT INTO transactions (equipment_id, user_id, action, details)
            VALUES (%s, %s, 'Maintenance', %s)
        """, (item_id, session["user_id"], f"Maintenance started: {remarks}"))
        conn.commit()
        flash("Maintenance started.", "success")
    elif maintenance_action == "complete":
        if not can_complete_maintenance(open_record):
            cur.close()
            conn.close()
            flash("There is no open maintenance record for this equipment.", "danger")
            return redirect(url_for("equipment_maintenance", item_id=item_id))

        cur.execute("""
            UPDATE maintenance_records
            SET status = 'Completed',
                completed_by = %s,
                completed_at = NOW(),
                completion_remarks = %s
            WHERE id = %s
        """, (session["user_id"], remarks, open_record["id"]))
        cur.execute("""
            UPDATE equipment
            SET status = 'Available'
            WHERE id = %s
        """, (item_id,))
        cur.execute("""
            INSERT INTO transactions (equipment_id, user_id, action, details)
            VALUES (%s, %s, 'Repair', %s)
        """, (item_id, session["user_id"], f"Maintenance completed: {remarks}"))
        conn.commit()
        flash("Maintenance completed. Equipment is now available.", "success")
    else:
        cur.close()
        conn.close()
        flash("Invalid maintenance action.", "danger")
        return redirect(url_for("equipment_maintenance", item_id=item_id))

    cur.close()
    conn.close()
    return redirect(url_for("view_equipment", item_id=item_id))


# ============================================================
# ADD EQUIPMENT
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/add",
    methods=["POST"]
)
@admin_required
def add_equipment():

    data = request.form
    try:
        asset_code = clean_text(data.get("asset_code"), "Asset code", 80)
        name = clean_text(data.get("name"), "Equipment name", 150)
        description = optional_text(data.get("description"), 5000)
        serial_number = optional_text(data.get("serial_number"), 150)
        specifications = optional_text(data.get("specifications"), 5000)
        acquisition_date = parse_optional_date(data.get("acquisition_date"), "acquisition date")
        status = data.get("status", "Available")
        if status not in {"Available", "Under Maintenance"}:
            raise ValueError("New equipment must be Available or Under Maintenance.")
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("equipment"))

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO equipment
            (
                asset_code,
                name,
                description,
                category_id,
                office_id,
                serial_number,
                specifications,
                acquisition_date,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            asset_code,
            name,
            description,
            data.get("category_id") or None,
            data.get("office_id") or None,
            serial_number,
            specifications,
            acquisition_date,
            status
        ))

        equipment_id = cur.lastrowid

        cur.execute("""
            INSERT INTO transactions
            (
                equipment_id,
                user_id,
                action,
                details
            )
            VALUES (%s, %s, 'Created', 'Equipment record created')
        """, (equipment_id, session["user_id"]))

        conn.commit()
    except IntegrityError as error:
        conn.rollback()
        if error.errno == 1062:
            flash("That asset code already exists. Please use a unique asset code.", "danger")
        else:
            flash("The equipment could not be added. Please check the entered values.", "danger")
        return redirect(url_for("equipment"))
    finally:
        cur.close()
        conn.close()

    flash(
        "Equipment added successfully.",
        "success"
    )

    return redirect(
        url_for("equipment")
    )


# ============================================================
# EDIT EQUIPMENT - PAGE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/edit/<int:item_id>",
    methods=["GET"]
)
@admin_required
def edit_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT *
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:

        cur.close()
        conn.close()

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    cur.execute("""
        SELECT id, name
        FROM categories
        ORDER BY name
    """)

    categories = cur.fetchall()

    cur.execute("""
        SELECT id, name
        FROM offices
        ORDER BY name
    """)

    offices = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "equipment_edit.html",
        equipment=equipment,
        categories=categories,
        offices=offices
    )


# ============================================================
# EDIT EQUIPMENT - SAVE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/edit/<int:item_id>",
    methods=["POST"]
)
@admin_required
def update_equipment(item_id):

    data = request.form

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT *
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    old = cur.fetchone()

    if not old:

        cur.close()
        conn.close()

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    try:
        asset_code = clean_text(data.get("asset_code"), "Asset code", 80)
        name = clean_text(data.get("name"), "Equipment name", 150)
        description = optional_text(data.get("description"), 5000)
        serial_number = optional_text(data.get("serial_number"), 150)
        specifications = optional_text(data.get("specifications"), 5000)
        acquisition_date = parse_optional_date(data.get("acquisition_date"), "acquisition date")
        new_status = data.get("status", "Available")
    except ValueError as error:
        cur.close()
        conn.close()
        flash(str(error), "danger")
        return redirect(url_for("edit_equipment", item_id=item_id))

    if not validate_status_change(old["status"], new_status):
        cur.close()
        conn.close()
        flash("That equipment status change is not allowed.", "danger")
        return redirect(url_for("edit_equipment", item_id=item_id))

    cur.execute("""
        UPDATE equipment
        SET
            asset_code = %s,
            name = %s,
            description = %s,
            category_id = %s,
            office_id = %s,
            serial_number = %s,
            specifications = %s,
            acquisition_date = %s,
            status = %s
        WHERE id = %s
    """, (

        asset_code,

        name,

        description,

        data.get(
            "category_id"
        ) or None,

        data.get(
            "office_id"
        ) or None,

        serial_number,

        specifications,

        acquisition_date,

        new_status,

        item_id
    ))

    cur.execute("""
        INSERT INTO transactions
        (
            equipment_id,
            user_id,
            action,
            details
        )
        VALUES
        (
            %s,
            %s,
            'Updated',
            %s
        )
    """, (
        item_id,
        session["user_id"],
        "Equipment information updated"
    ))

    conn.commit()

    cur.close()
    conn.close()

    flash(
        "Equipment updated successfully.",
        "success"
    )

    return redirect(
        url_for(
            "view_equipment",
            item_id=item_id
        )
    )


# ============================================================
# ARCHIVE EQUIPMENT
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/archive/<int:item_id>",
    methods=["POST"]
)
@admin_required
def archive_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:

        cur.close()
        conn.close()

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    if equipment["status"] in {"Archived", "Disposed", "Under Maintenance"}:
        cur.close()
        conn.close()
        flash("Archived, disposed, or maintained equipment cannot be archived.", "danger")
        return redirect(url_for("equipment"))

    cur.execute("""
        UPDATE equipment
        SET status = 'Archived'
        WHERE id = %s
    """, (item_id,))

    cur.execute("""
        UPDATE accountability
                SET is_current = 0,
                        released_at = COALESCE(released_at, NOW())
        WHERE equipment_id = %s
          AND is_current = 1
    """, (item_id,))

    cur.execute("""
        INSERT INTO transactions
        (
            equipment_id,
            user_id,
            action,
            details
        )
        VALUES
        (
            %s,
            %s,
            'Archived',
            'Equipment moved to archive'
        )
    """, (
        item_id,
        session["user_id"]
    ))

    cur.execute("""
        INSERT INTO archives
        (
            equipment_id,
            archived_by,
            reason
        )
        VALUES
        (
            %s,
            %s,
            %s
        )
    """, (
        item_id,
        session["user_id"],
        request.form.get(
            "reason",
            "Equipment moved to archive"
        ).strip() or "Equipment moved to archive"
    ))

    conn.commit()

    cur.close()
    conn.close()

    flash(
        "Equipment archived.",
        "success"
    )

    return redirect(
        url_for("equipment")
    )


# ============================================================
# RESTORE ARCHIVED EQUIPMENT
# ============================================================

@app.route(
    "/equipment/restore/<int:item_id>",
    methods=["POST"]
)
@admin_required
def restore_equipment(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, status
        FROM equipment
        WHERE id = %s
    """, (item_id,))
    equipment = cur.fetchone()

    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    if equipment["status"] != "Archived":
        cur.close()
        conn.close()
        flash("Only archived equipment can be restored.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    cur.execute("""
        UPDATE equipment
        SET status = 'Available'
        WHERE id = %s AND status = 'Archived'
    """, (item_id,))
    cur.execute("""
        INSERT INTO transactions (equipment_id, user_id, action, details)
        VALUES (%s, %s, 'Restored', 'Equipment restored from archive')
    """, (item_id, session["user_id"]))
    conn.commit()
    cur.close()
    conn.close()

    flash("Equipment restored and marked available.", "success")
    return redirect(url_for("view_equipment", item_id=item_id))


# ============================================================
# DISPOSE EQUIPMENT
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/dispose/<int:item_id>",
    methods=["GET", "POST"]
)
@admin_required
def dispose_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    if equipment["status"] in {"Archived", "Disposed", "Under Maintenance"}:
        cur.close()
        conn.close()
        flash("This equipment cannot be disposed in its current status.", "danger")
        return redirect(url_for("equipment"))

    if request.method == "GET":
        cur.close()
        conn.close()
        return render_template(
            "equipment_dispose.html",
            equipment=equipment,
            current_date=date.today().isoformat()
        )

    reason = request.form.get("reason", "").strip()
    disposal_date = request.form.get("disposal_date", "").strip()
    reference_no = request.form.get("reference_no", "").strip()

    try:
        date.fromisoformat(disposal_date)
    except ValueError:
        cur.close()
        conn.close()
        flash("Please provide a valid disposal date.", "danger")
        return redirect(url_for("dispose_equipment", item_id=item_id))

    if not reason:
        cur.close()
        conn.close()
        flash("A disposal reason is required.", "danger")
        return redirect(url_for("dispose_equipment", item_id=item_id))

    cur.execute("""
        UPDATE accountability
        SET is_current = 0,
            released_at = COALESCE(released_at, NOW())
        WHERE equipment_id = %s
          AND is_current = 1
    """, (item_id,))

    cur.execute("""
        UPDATE equipment
        SET status = 'Disposed'
        WHERE id = %s
    """, (item_id,))

    cur.execute("""
        INSERT INTO disposals
        (
            equipment_id,
            disposed_by,
            reason,
            disposal_date,
            reference_no
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """, (
        item_id,
        session["user_id"],
        reason,
        disposal_date,
        reference_no or None
    ))

    cur.execute("""
        INSERT INTO transactions
        (
            equipment_id,
            user_id,
            action,
            details
        )
        VALUES
        (
            %s,
            %s,
            'Disposed',
            %s
        )
    """, (
        item_id,
        session["user_id"],
        f"Equipment disposed: {reason}"
    ))

    conn.commit()
    cur.close()
    conn.close()

    flash("Equipment disposed.", "success")
    return redirect(url_for("equipment"))


# ============================================================
# ASSIGN EQUIPMENT - PAGE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/assign/<int:item_id>",
    methods=["GET"]
)
@admin_required
def assign_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name
        FROM equipment e

        LEFT JOIN categories c
            ON e.category_id = c.id

        LEFT JOIN offices o
            ON e.office_id = o.id

        WHERE e.id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:

        cur.close()
        conn.close()

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    if equipment["status"] in {"Archived", "Disposed", "Under Maintenance"}:
        cur.close()
        conn.close()
        flash("Archived, disposed, or maintained equipment cannot be assigned.", "danger")
        return redirect(url_for("equipment"))

    cur.execute("""
                SELECT
                        a.*,
                        o.name AS office_name
                FROM accountability a
                LEFT JOIN offices o
                        ON a.office_id = o.id
                WHERE a.equipment_id = %s
                    AND a.is_current = 1
                ORDER BY a.id DESC
                LIMIT 1
    """, (item_id,))

    accountable = cur.fetchone()

    cur.execute("""
        SELECT id, name
        FROM offices
        ORDER BY name
    """)

    offices = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "equipment_assign.html",
        equipment=equipment,
        accountable=accountable,
        offices=offices,
        current_date=date.today().isoformat()
    )


# ============================================================
# ASSIGN EQUIPMENT - SAVE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/assign/<int:item_id>",
    methods=["POST"]
)
@admin_required
def save_assignment(item_id):

    person_name = request.form.get(
        "person_name",
        ""
    ).strip()
    person_position = request.form.get(
        "person_position",
        ""
    ).strip()
    office_id = request.form.get("office_id") or None
    assigned_at = request.form.get("assigned_at", "").strip()

    if not person_name or not person_position or not office_id or not assigned_at:

        flash(
            "Please complete all assignment fields.",
            "danger"
        )

        return redirect(
            url_for(
                "assign_equipment",
                item_id=item_id
            )
        )

    try:
        date.fromisoformat(assigned_at)
    except ValueError:
        flash("Please provide a valid assignment date.", "danger")
        return redirect(
            url_for(
                "assign_equipment",
                item_id=item_id
            )
        )

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT *
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:

        cur.close()
        conn.close()

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    cur.execute("""
        UPDATE accountability
                SET is_current = 0,
                        released_at = COALESCE(released_at, NOW())
        WHERE equipment_id = %s
          AND is_current = 1
    """, (item_id,))

    cur.execute("""
        INSERT INTO accountability
        (
            equipment_id,
            person_name,
            person_position,
            office_id,
            assigned_at,
            is_current
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            1
        )
    """, (
        item_id,
        person_name,
        person_position,
        office_id,
        f"{assigned_at} 00:00:00"
    ))

    cur.execute("""
        UPDATE equipment
        SET status = 'Assigned'
        WHERE id = %s
    """, (item_id,))

    cur.execute("""
        INSERT INTO transactions
        (
            equipment_id,
            user_id,
            action,
            details
        )
        VALUES
        (
            %s,
            %s,
            'Assigned',
            %s
        )
    """, (
        item_id,
        session["user_id"],
        f"Equipment assigned to {person_name} ({person_position})"
    ))

    conn.commit()

    cur.close()
    conn.close()

    flash(
        "Equipment assigned successfully.",
        "success"
    )

    return redirect(
        url_for(
            "view_equipment",
            item_id=item_id
        )
    )


# ============================================================
# UNASSIGN EQUIPMENT
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/unassign/<int:item_id>",
    methods=["POST"]
)
@admin_required
def unassign_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT id, status FROM equipment WHERE id = %s", (item_id,))
    equipment = cur.fetchone()
    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))
    if equipment["status"] != "Assigned":
        cur.close()
        conn.close()
        flash("Only assigned equipment can be unassigned.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    cur.execute("""
        UPDATE accountability
                SET is_current = 0,
                        released_at = COALESCE(released_at, NOW())
        WHERE equipment_id = %s
          AND is_current = 1
    """, (item_id,))

    cur.execute("""
        UPDATE equipment
        SET status = 'Available'
        WHERE id = %s
          AND status = 'Assigned'
    """, (item_id,))

    cur.execute("""
        INSERT INTO transactions
        (
            equipment_id,
            user_id,
            action,
            details
        )
        VALUES
        (
            %s,
            %s,
            'Unassigned',
            'Accountable person removed'
        )
    """, (
        item_id,
        session["user_id"]
    ))

    conn.commit()

    cur.close()
    conn.close()

    flash(
        "Equipment is now available.",
        "success"
    )

    return redirect(
        url_for(
            "view_equipment",
            item_id=item_id
        )
    )


# ============================================================
# TRANSACTION HISTORY
# Worker can VIEW
# ============================================================

@app.route("/transactions")
@login_required
def transactions():

    q = request.args.get("q", "").strip()
    action = request.args.get("action", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if action not in TRANSACTION_ACTIONS:
        action = ""

    try:
        date_from = parse_optional_date(date_from, "start date")
        date_to = parse_optional_date(date_to, "end date")
    except ValueError:
        date_from = ""
        date_to = ""

    conn = db()
    cur = conn.cursor(dictionary=True)

    sql = """
        SELECT
            t.*,
            e.asset_code,
            e.name AS equipment_name,
            u.full_name
        FROM transactions t

        JOIN equipment e
            ON t.equipment_id = e.id

        JOIN users u
            ON t.user_id = u.id
    """

    filters = []
    params = []

    if q:
        filters.append("""
            (
                CAST(t.created_at AS CHAR) LIKE %s
                OR e.asset_code LIKE %s
                OR e.name LIKE %s
                OR t.action LIKE %s
                OR u.full_name LIKE %s
            )
        """)
        search = f"%{q}%"
        params.extend([search] * 5)

    if action:
        filters.append("t.action = %s")
        params.append(action)

    if date_from:
        filters.append("t.created_at >= %s")
        params.append(date_from)

    if date_to:
        filters.append("t.created_at < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(date_to)

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += " ORDER BY t.created_at DESC"

    cur.execute(sql, params)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if request.args.get("export") == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "Date",
            "Asset Code",
            "Equipment",
            "Action",
            "Details",
            "Performed By",
        ])
        for row in rows:
            writer.writerow([
                csv_safe(row["created_at"]),
                csv_safe(row["asset_code"]),
                csv_safe(row["equipment_name"]),
                csv_safe(row["action"]),
                csv_safe(row["details"]),
                csv_safe(row["full_name"]),
            ])

        response = make_response("\ufeff" + output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=items-transaction-report.csv"
        return response

    return render_template(
        "transactions.html",
        transactions=rows,
        q=q,
        action=action,
        date_from=date_from or "",
        date_to=date_to or "",
        transaction_actions=TRANSACTION_ACTIONS
    )


# ============================================================
# EQUIPMENT TRANSACTION HISTORY
# Worker can VIEW
# ============================================================

@app.route(
    "/equipment/<int:item_id>/transactions"
)
@login_required
def equipment_transactions(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            t.*,
            e.asset_code,
            e.name AS equipment_name,
            u.full_name
        FROM transactions t

        JOIN equipment e
            ON t.equipment_id = e.id

        JOIN users u
            ON t.user_id = u.id

        WHERE t.equipment_id = %s

        ORDER BY t.created_at DESC
    """, (item_id,))

    rows = cur.fetchall()

    cur.execute("""
        SELECT id, asset_code, name
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    cur.close()
    conn.close()

    if not equipment:

        flash(
            "Equipment not found.",
            "danger"
        )

        return redirect(
            url_for("equipment")
        )

    return render_template(
        "equipment_transactions.html",
        transactions=rows,
        equipment=equipment
    )


# ============================================================
# CATEGORIES
# Worker can VIEW
# ============================================================

@app.route("/categories")
@login_required
def categories():

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT *
        FROM categories
        ORDER BY name
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "categories.html",
        categories=rows
    )


# ============================================================
# ADD CATEGORY
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/categories/add",
    methods=["POST"]
)
@admin_required
def add_category():

    try:
        name = clean_text(request.form.get("name"), "Category name", 100)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("categories"))

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO categories
            (
                name,
                description
            )
            VALUES
            (
                %s,
                %s
            )
        """, (
            name,
            description
        ))

        conn.commit()
    except IntegrityError:
        conn.rollback()
        flash("That category already exists.", "danger")
        return redirect(url_for("categories"))
    finally:
        cur.close()
        conn.close()

    flash("Category added.", "success")

    return redirect(
        url_for("categories")
    )


# ============================================================
# QR CODE GENERATION
# Worker can VIEW QR
# ============================================================

@app.route(
    "/equipment/<int:item_id>/qr"
)
@login_required
def equipment_qr(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            id,
            asset_code,
            name
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    cur.close()
    conn.close()

    if not equipment:

        return jsonify({
            "error": "Equipment not found"
        }), 404

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4
    )

    qr.add_data(equipment["asset_code"])
    qr.make(fit=True)

    image = qr.make_image()

    buffer = BytesIO()

    image.save(
        buffer,
        format="PNG"
    )

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png",
        download_name=f"{equipment['asset_code']}.png"
    )


# ============================================================
# QR SCANNER PAGE
# Worker can USE
# ============================================================

@app.route("/qr-scanner")
@login_required
def qr_scanner():

    return render_template(
        "qr_scanner.html"
    )


# ============================================================
# QR LOOKUP BY ASSET CODE
# Worker can VIEW
# ============================================================

@app.route(
    "/api/equipment/<asset_code>"
)
@login_required
def api_equipment(asset_code):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            a.person_name AS accountable_person
        FROM equipment e

        LEFT JOIN categories c
            ON e.category_id = c.id

        LEFT JOIN offices o
            ON e.office_id = o.id

        LEFT JOIN accountability a
            ON e.id = a.equipment_id
            AND a.is_current = 1

        WHERE e.asset_code = %s
    """, (asset_code,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    return jsonify(
        row or {
            "error": "Equipment not found"
        }
    )


# ============================================================
# USERS
# SUPER ADMIN AND ADMIN
# ============================================================

@app.route("/users")
@user_management_required
def users():
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            u.id,
            u.username,
            u.full_name,
            u.email,
            u.profile_picture,
            u.is_active,
            r.name AS role_name
        FROM users u

        JOIN roles r
            ON u.role_id = r.id

        ORDER BY u.id DESC
    """)

    rows = cur.fetchall()

    allowed_roles = creatable_roles(g.current_user["role_name"])
    role_placeholders = ", ".join(["%s"] * len(allowed_roles))
    cur.execute(f"""
        SELECT id, name
        FROM roles
        WHERE name IN ({role_placeholders})
        ORDER BY id
    """, allowed_roles)

    roles = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "users.html",
        users=rows,
        roles=roles
    )


# ============================================================
# ADD USER
# SUPER ADMIN AND ADMIN
# ============================================================

@app.route(
    "/users/add",
    methods=["POST"]
)
@user_management_required
def add_user():
    # ========================================================
    # GET SELECTED ROLE
    # ========================================================

    try:
        username = clean_text(request.form.get("username"), "Username", 80)
        password = clean_text(request.form.get("password"), "Password", 255)
        confirm_password = request.form.get("confirm_password", "")
        full_name = clean_text(request.form.get("full_name"), "Full name", 150)
        email = optional_text(request.form.get("email"), 150)
        role_id = request.form.get("role_id")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if password != confirm_password:
            raise ValueError("Password and confirmation do not match.")
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("users"))

    conn = db()
    cur = conn.cursor(dictionary=True)

    # ========================================================
    # CHECK SELECTED ROLE
    # ========================================================

    cur.execute("""
        SELECT
            id,
            name
        FROM roles
        WHERE id = %s
    """, (role_id,))

    selected_role = cur.fetchone()

    # ========================================================
    # CHECK ROLE PERMISSION
    # ========================================================

    allowed_roles = creatable_roles(g.current_user["role_name"])
    allowed_role_names = {role.lower() for role in allowed_roles}
    if (
        not selected_role
        or selected_role["name"].strip().lower() not in allowed_role_names
    ):

        cur.close()
        conn.close()

        flash(
            "You are not allowed to create an account with that role.",
            "danger"
        )

        return redirect(
            url_for("users")
        )

    # ========================================================
    # CREATE USER
    # ========================================================

    cur.execute("""
        INSERT INTO users
        (
            username,
            password_hash,
            full_name,
            email,
            role_id
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """, (

            username,

        generate_password_hash(
            password
        ),

        full_name,

        email,

        role_id
    ))

    conn.commit()

    cur.close()
    conn.close()

    flash(
        "User created.",
        "success"
    )

    return redirect(
        url_for("users")
    )


# ============================================================
# PROFILE
# Every signed-in user may maintain their own contact details and password.
# ============================================================

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        conn = None
        cur = None
        try:
            full_name = clean_text(request.form.get("full_name"), "Full name", 150)
            email = optional_text(request.form.get("email"), 150)
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if new_password or confirm_password:
                if not current_password:
                    raise ValueError("Current password is required to change your password.")
                if len(new_password) < 8:
                    raise ValueError("New password must be at least 8 characters long.")
                if new_password != confirm_password:
                    raise ValueError("New password and confirmation do not match.")

            profile_picture = None
            uploaded_picture = request.files.get("profile_picture")
            if uploaded_picture and uploaded_picture.filename:
                if g.current_user["role_name"].strip().lower() not in MANAGEMENT_ROLES:
                    raise ValueError("Only Super Admin and Admin accounts may change profile pictures.")
                safe_name = secure_filename(uploaded_picture.filename)
                extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
                if extension not in PROFILE_IMAGE_EXTENSIONS:
                    raise ValueError("Profile picture must be JPG, PNG, GIF, or WEBP.")
                profile_picture = f"{g.current_user['id']}_{secrets.token_hex(8)}.{extension}"
                os.makedirs(PROFILE_UPLOAD_DIR, exist_ok=True)
                uploaded_picture.save(os.path.join(PROFILE_UPLOAD_DIR, profile_picture))

            conn = db()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT password_hash, profile_picture FROM users WHERE id = %s", (g.current_user["id"],))
            account = cur.fetchone()
            if new_password and (not account or not check_password_hash(account["password_hash"], current_password)):
                raise ValueError("Current password is incorrect.")

            if new_password:
                cur.execute("""
                    UPDATE users
                    SET full_name = %s, email = %s, password_hash = %s, profile_picture = COALESCE(%s, profile_picture)
                    WHERE id = %s
                """, (full_name, email, generate_password_hash(new_password), profile_picture, g.current_user["id"]))
            else:
                cur.execute("""
                    UPDATE users SET full_name = %s, email = %s,
                    profile_picture = COALESCE(%s, profile_picture)
                    WHERE id = %s
                """, (full_name, email, profile_picture, g.current_user["id"]))
            conn.commit()
            session["full_name"] = full_name
            if profile_picture:
                session["profile_picture"] = profile_picture
                if account and account.get("profile_picture") and account["profile_picture"] != profile_picture:
                    old_picture = os.path.join(PROFILE_UPLOAD_DIR, account["profile_picture"])
                    if os.path.isfile(old_picture):
                        os.remove(old_picture)
            flash("Profile updated.", "success")
        except ValueError as error:
            flash(str(error), "danger")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()
        return redirect(url_for("profile"))

    return render_template("profile.html", user=g.current_user)


# ============================================================
# SUPER ADMIN ACCOUNT CONTROLS
# Super Admin may reset or disable Admin/Worker accounts only.
# ============================================================

@app.route("/users/<int:user_id>/update", methods=["POST"])
@user_management_required
def update_managed_user(user_id):
    conn = None
    cur = None
    new_picture_path = None
    try:
        full_name = clean_text(request.form.get("full_name"), "Full name", 150)
        email = optional_text(request.form.get("email"), 150)
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if new_password and len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")

        conn = db()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT u.id, u.username, u.profile_picture, r.name AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.id = %s
        """, (user_id,))
        target = cur.fetchone()
        allowed = {role.lower() for role in manageable_roles(g.current_user["role_name"])}
        if not target or target["role_name"].strip().lower() not in allowed:
            raise ValueError("You are not allowed to update this account.")

        uploaded_picture = request.files.get("profile_picture")
        profile_picture = None
        if uploaded_picture and uploaded_picture.filename:
            safe_name = secure_filename(uploaded_picture.filename)
            extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
            if extension not in PROFILE_IMAGE_EXTENSIONS:
                raise ValueError("Profile picture must be JPG, PNG, GIF, or WEBP.")
            profile_picture = f"{user_id}_{secrets.token_hex(8)}.{extension}"
            os.makedirs(PROFILE_UPLOAD_DIR, exist_ok=True)
            new_picture_path = os.path.join(PROFILE_UPLOAD_DIR, profile_picture)
            uploaded_picture.save(new_picture_path)

        cur.execute("""
            UPDATE users
            SET full_name = %s,
                email = %s,
                profile_picture = COALESCE(%s, profile_picture),
                password_hash = COALESCE(%s, password_hash)
            WHERE id = %s
        """, (full_name, email, profile_picture, generate_password_hash(new_password) if new_password else None, user_id))
        conn.commit()
        if profile_picture and target.get("profile_picture") and target["profile_picture"] != profile_picture:
            old_picture_path = os.path.join(PROFILE_UPLOAD_DIR, target["profile_picture"])
            if os.path.isfile(old_picture_path):
                os.remove(old_picture_path)
        flash(f"{target['username']} account updated.", "success")
    except ValueError as error:
        if new_picture_path and os.path.isfile(new_picture_path):
            os.remove(new_picture_path)
        flash(str(error), "danger")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/reset-password", methods=["POST"])
@user_management_required
def reset_user_password(user_id):
    try:
        new_password = clean_text(request.form.get("new_password"), "New password", 255)
        confirm_password = request.form.get("confirm_password", "")
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("users"))

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.username, r.name AS role_name
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s
    """, (user_id,))
    target = cur.fetchone()
    allowed = {role.lower() for role in manageable_roles(g.current_user["role_name"])}
    if not target or target["role_name"].strip().lower() not in allowed:
        cur.close()
        conn.close()
        flash("Only Admin and Worker passwords can be reset here.", "danger")
        return redirect(url_for("users"))

    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(new_password), user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Password reset for {target['username']}.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/toggle-status", methods=["POST"])
@user_management_required
def toggle_user_status(user_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id, u.username, u.is_active, r.name AS role_name
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s
    """, (user_id,))
    target = cur.fetchone()
    allowed = {role.lower() for role in manageable_roles(g.current_user["role_name"])}
    if not target or target["role_name"].strip().lower() not in allowed:
        cur.close()
        conn.close()
        flash("Only Admin and Worker accounts can be activated or deactivated here.", "danger")
        return redirect(url_for("users"))

    new_status = 0 if target["is_active"] else 1
    cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{target['username']} is now {'active' if new_status else 'inactive'}.", "success")
    return redirect(url_for("users"))


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=app.config["DEBUG"],
        host="127.0.0.1",
        port=5000
    )
