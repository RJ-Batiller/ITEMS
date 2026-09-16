import os
import hmac
import csv
import json
import secrets
import re
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
CHAT_UPLOAD_DIR = os.path.join(app.root_path, "private_uploads", "chat")
PROFILE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
CHAT_FILE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx", "xls", "xlsx", "txt", "zip"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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
            return render_template(
                "error.html",
                code=400,
                title="Request could not be verified",
                message="The CSRF token is missing or expired. Refresh the page and try again.",
            ), 400


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; frame-ancestors 'self'")
    return response


@app.errorhandler(404)
def page_not_found(error):
    return render_template("error.html", code=404, title="Page not found", message="The page you requested does not exist."), 404


@app.errorhandler(413)
def request_too_large(error):
    return render_template("error.html", code=413, title="File too large", message="The uploaded file is larger than the 4 MB limit."), 413


@app.errorhandler(500)
def internal_server_error(error):
    app.logger.exception("Unhandled application error")
    return render_template("error.html", code=500, title="Something went wrong", message="The request could not be completed. Please try again."), 500


@app.errorhandler(mysql.connector.Error)
def database_error(error):
    app.logger.exception("Database error")
    return render_template("error.html", code=503, title="Database unavailable", message="The database could not complete this request. Confirm that MySQL is running, then try again."), 503


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

EQUIPMENT_ITEM_TYPES = ("Consumable", "Non-Consumable")

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

RESTRICTABLE_FEATURES = (
    ("add", "Add equipment"),
    ("edit", "Edit equipment"),
    ("assign", "Assign equipment"),
    ("maintenance", "Maintenance"),
    ("archive", "Archive and restore"),
    ("dispose", "Dispose equipment"),
    ("categories", "Manage categories"),
    ("offices", "Manage offices"),
    ("messages", "Manage message groups"),
)


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


def optional_email(value):
    value = optional_text(value, 150)
    if value and not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("Please provide a valid email address.")
    return value


def required_id(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} is required.") from error
    if parsed <= 0:
        raise ValueError(f"{field_name} is required.")
    return parsed


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


def role_allows_action(role_name, action):
    role = (role_name or "").strip().lower()
    if action == "manage_users":
        return role in MANAGEMENT_ROLES
    if action in {"categories", "offices", "messages"}:
        return role in MANAGEMENT_ROLES
    if action in EQUIPMENT_WRITE_ACTIONS:
        return role in MANAGEMENT_ROLES
    return action in {"view", "view_transactions", "view_qr"} and bool(role)


def parse_feature_permissions(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def can_perform_action(role_name, action, feature_permissions=None):
    """Return whether a role/user may perform a named application action."""
    permissions = parse_feature_permissions(feature_permissions)
    if action in permissions:
        return bool(permissions[action])
    return role_allows_action(role_name, action)


def user_feature_enabled(feature_permissions, role_name, action):
    return can_perform_action(role_name, action, feature_permissions)


app.template_global("user_feature_enabled")(user_feature_enabled)


def can_start_maintenance(status):
    return status not in {"Assigned", "Archived", "Disposed"}


def can_complete_maintenance(has_open_record):
    return bool(has_open_record)


# ============================================================
# LOGIN REQUIRED
# ============================================================

def sync_session_user(user):
    """Keep the session display data aligned with the active database account."""
    session["full_name"] = user["full_name"]
    session["role"] = user["role_name"]
    if user.get("profile_picture"):
        session["profile_picture"] = user["profile_picture"]
    else:
        session.pop("profile_picture", None)


def login_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        user = get_current_user()
        if not user:
            session.clear()
            return redirect(url_for("login"))

        g.current_user = user
        sync_session_user(user)

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
        SELECT u.id, u.username, u.full_name, u.email, u.profile_picture, u.feature_permissions, u.is_active, r.name AS role_name
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

def admin_required(action="edit"):
    def decorator(fn):

        @wraps(fn)
        def wrapper(*args, **kwargs):

            user = get_current_user()
            if not user:
                session.clear()
                return redirect(url_for("login"))

            g.current_user = user
            sync_session_user(user)
            if not can_perform_action(user["role_name"], action, user.get("feature_permissions")):

                flash(
                    "Your account does not have access to this feature.",
                    "danger"
                )

                return redirect(
                    url_for("equipment")
                )

            return fn(*args, **kwargs)

        return wrapper
    return decorator


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

    cur.execute("""
        SELECT c.name, COUNT(e.id) AS equipment_count
        FROM categories c
        LEFT JOIN equipment e
            ON e.category_id = c.id
            AND e.status NOT IN ('Archived', 'Disposed')
        GROUP BY c.id, c.name
        ORDER BY equipment_count DESC, c.name
        LIMIT 8
    """)
    category_breakdown = cur.fetchall()

    cur.execute("""
        SELECT o.name, COUNT(e.id) AS equipment_count
        FROM offices o
        LEFT JOIN equipment e
            ON e.office_id = o.id
            AND e.status NOT IN ('Archived', 'Disposed')
        GROUP BY o.id, o.name
        ORDER BY equipment_count DESC, o.name
        LIMIT 8
    """)
    office_breakdown = cur.fetchall()

    cur.execute("""
        SELECT action, COUNT(*) AS action_count
        FROM transactions
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY action
        ORDER BY action_count DESC, action
        LIMIT 6
    """)
    activity_breakdown = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS completed_count
        FROM maintenance_records
        WHERE status = 'Completed'
          AND completed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """)
    completed_maintenance_30d = cur.fetchone()["completed_count"]

    active_total = total or 1
    utilization_rate = round((assigned / active_total) * 100)
    ready_rate = round(((available + assigned) / active_total) * 100)
    category_max = max((row["equipment_count"] for row in category_breakdown), default=0)
    office_max = max((row["equipment_count"] for row in office_breakdown), default=0)
    activity_max = max((row["action_count"] for row in activity_breakdown), default=0)
    activity_30d = sum(row["action_count"] for row in activity_breakdown)

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
        open_maintenance=open_maintenance,
        category_breakdown=category_breakdown,
        office_breakdown=office_breakdown,
        activity_breakdown=activity_breakdown,
        completed_maintenance_30d=completed_maintenance_30d,
        utilization_rate=utilization_rate,
        ready_rate=ready_rate,
        category_max=category_max,
        office_max=office_max,
        activity_max=activity_max,
        activity_30d=activity_30d
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
    item_type = request.args.get("type", "").strip()
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
    if item_type not in EQUIPMENT_ITEM_TYPES:
        item_type = ""

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

    if item_type:
        filters.append("e.item_type = %s")
        params.append(item_type)

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
        writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "Property Number",
            "Equipment",
            "Category",
            "Type",
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
                csv_safe(row["item_type"]),
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

    cur.execute("""
        SELECT item_type, COUNT(*) AS item_type_count
        FROM equipment
        GROUP BY item_type
    """)
    item_type_counts = {
        row["item_type"]: row["item_type_count"]
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
            selected_item_type=item_type,
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
        selected_item_type=item_type,
        item_type_counts=item_type_counts,
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
@admin_required("maintenance")
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
@admin_required("add")
def add_equipment():

    data = request.form
    try:
        asset_code = clean_text(data.get("asset_code"), "Property Number", 80)
        name = clean_text(data.get("name"), "Equipment name", 150)
        description = optional_text(data.get("description"), 5000)
        serial_number = optional_text(data.get("serial_number"), 150)
        specifications = optional_text(data.get("specifications"), 5000)
        acquisition_date = parse_optional_date(data.get("acquisition_date"), "acquisition date")
        category_id = required_id(data.get("category_id"), "Category")
        office_id = required_id(data.get("office_id"), "Office")
        item_type = data.get("item_type", "").strip()
        if item_type not in EQUIPMENT_ITEM_TYPES:
            raise ValueError("Select Consumable or Non-Consumable.")
        status = "Available"
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
                item_type,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            asset_code,
            name,
            description,
            category_id,
            office_id,
            serial_number,
            specifications,
            acquisition_date,
            item_type,
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
            flash("That Property Number already exists. Please use a unique Property Number.", "danger")
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
@admin_required("edit")
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
@admin_required("edit")
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
        asset_code = clean_text(data.get("asset_code"), "Property Number", 80)
        name = clean_text(data.get("name"), "Equipment name", 150)
        description = optional_text(data.get("description"), 5000)
        serial_number = optional_text(data.get("serial_number"), 150)
        specifications = optional_text(data.get("specifications"), 5000)
        acquisition_date = parse_optional_date(data.get("acquisition_date"), "acquisition date")
        category_id = required_id(data.get("category_id"), "Category")
        office_id = required_id(data.get("office_id"), "Office")
        item_type = data.get("item_type", "").strip()
        if item_type not in EQUIPMENT_ITEM_TYPES:
            raise ValueError("Select Consumable or Non-Consumable.")
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
            item_type = %s,
            status = %s
        WHERE id = %s
    """, (

        asset_code,

        name,

        description,

        category_id,

        office_id,

        serial_number,

        specifications,

        acquisition_date,

        item_type,

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
@admin_required("archive")
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
@admin_required("archive")
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
@admin_required("dispose")
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
@admin_required("assign")
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
@admin_required("assign")
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
@admin_required("unassign")
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

    if len(action) > 80:
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

    cur.execute("SELECT DISTINCT action FROM transactions WHERE action IS NOT NULL AND action <> '' ORDER BY action")
    stored_actions = {row["action"] for row in cur.fetchall()}
    transaction_actions = tuple(sorted(set(TRANSACTION_ACTIONS) | stored_actions))

    cur.close()
    conn.close()

    if request.args.get("export") == "csv":
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "Date",
            "Property Number",
            "Equipment",
            "Action",
            "Details",
            "Performed By",
        ])
        for row in rows:
            writer.writerow([
                csv_datetime(row["created_at"]),
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
        transaction_actions=transaction_actions
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

    q = request.args.get("q", "").strip()

    conn = db()
    cur = conn.cursor(dictionary=True)

    sql = """
        SELECT *
        FROM categories
    """
    params = []
    if q:
        sql += " WHERE name LIKE %s OR description LIKE %s"
        params = [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY name"
    cur.execute(sql, params)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "categories.html",
        categories=rows,
        q=q
    )


@app.route("/transactions/organization-history")
@login_required
def organization_history():
    q = request.args.get("q", "").strip()
    entity_type = request.args.get("entity_type", "").strip()
    action = request.args.get("action", "").strip()
    allowed_types = {"Account", "Category", "Office"}
    allowed_actions = {"Created", "Updated", "Deleted"}
    if entity_type not in allowed_types:
        entity_type = ""
    if action not in allowed_actions:
        action = ""

    conn = db()
    cur = conn.cursor(dictionary=True)
    sql = """
        SELECT h.entity_type, h.entity_name, h.action, h.details,
               h.created_at, u.full_name
        FROM organization_history h
        JOIN users u ON u.id = h.user_id
    """
    filters = []
    params = []
    if q:
        filters.append("(h.entity_name LIKE %s OR h.details LIKE %s OR u.full_name LIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if entity_type:
        filters.append("h.entity_type = %s")
        params.append(entity_type)
    if action:
        filters.append("h.action = %s")
        params.append(action)
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY h.created_at DESC, h.id DESC"
    cur.execute(sql, params)
    history = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("organization_history.html", history=history, q=q, entity_type=entity_type, action=action)


# ============================================================
# ADD CATEGORY
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/categories/add",
    methods=["POST"]
)
@admin_required("categories")
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

        category_id = cur.lastrowid
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Category', %s, %s, 'Created', %s, %s)
        """, (category_id, name, description or "Category created.", session["user_id"]))

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


@app.route("/categories/<int:category_id>/edit", methods=["POST"])
@admin_required("categories")
def edit_category(category_id):
    try:
        name = clean_text(request.form.get("name"), "Category name", 100)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("categories"))

    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM categories WHERE id = %s", (category_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Category not found.")
        cur.execute("UPDATE categories SET name = %s, description = %s WHERE id = %s", (name, description, category_id))
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Category', %s, %s, 'Updated', %s, %s)
        """, (category_id, name, f"Updated from '{current['name']}'.", session["user_id"]))
        conn.commit()
        flash("Category updated.", "success")
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
    except IntegrityError:
        conn.rollback()
        flash("That category already exists.", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("categories"))


@app.route("/categories/<int:category_id>/delete", methods=["POST"])
@admin_required("categories")
def delete_category(category_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM categories WHERE id = %s", (category_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Category not found.")
        cur.execute("DELETE FROM categories WHERE id = %s", (category_id,))
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Category', %s, %s, 'Deleted', 'Category deleted; related equipment is now uncategorized.', %s)
        """, (category_id, current["name"], session["user_id"]))
        conn.commit()
        flash("Category deleted. Related equipment is now uncategorized.", "success")
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("categories"))


def organization_history_page(entity_type, entity_id, table_name, back_endpoint, label):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT h.action, h.entity_name, h.details, h.created_at, u.full_name
        FROM organization_history h
        JOIN users u ON u.id = h.user_id
        WHERE h.entity_type = %s AND h.entity_id = %s
        ORDER BY h.created_at DESC, h.id DESC
    """, (entity_type, entity_id))
    history = cur.fetchall()
    cur.execute(f"SELECT name FROM {table_name} WHERE id = %s", (entity_id,))
    current = cur.fetchone()
    cur.close()
    conn.close()
    if not history:
        return render_template("error.html", code=404, title=f"{label} history not found", message=f"No history exists for this {label.lower()} yet."), 404
    return render_template("resource_history.html", resource_type=label, resource_name=current["name"] if current else history[0]["entity_name"], history=history, back_endpoint=back_endpoint)


@app.route("/categories/<int:category_id>/history")
@login_required
def category_history(category_id):
    return organization_history_page("Category", category_id, "categories", "categories", "Category")


@app.route("/offices")
@login_required
def offices():
    q = request.args.get("q", "").strip()
    conn = db()
    cur = conn.cursor(dictionary=True)
    sql = """
        SELECT o.id, o.name, o.description, COUNT(e.id) AS equipment_count
        FROM offices o
        LEFT JOIN equipment e ON e.office_id = o.id
    """
    params = []
    if q:
        sql += " WHERE o.name LIKE %s OR o.description LIKE %s"
        params = [f"%{q}%", f"%{q}%"]
    sql += " GROUP BY o.id, o.name, o.description ORDER BY o.name"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("offices.html", offices=rows, q=q)


@app.route("/offices/add", methods=["POST"])
@admin_required("offices")
def add_office():
    try:
        name = clean_text(request.form.get("name"), "Office name", 150)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("offices"))

    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO offices (name, description) VALUES (%s, %s)",
            (name, description),
        )
        office_id = cur.lastrowid
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Office', %s, %s, 'Created', %s, %s)
        """, (office_id, name, description or "Office created.", session["user_id"]))
        conn.commit()
        flash("Office added.", "success")
    except IntegrityError:
        conn.rollback()
        flash("That office already exists.", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("offices"))


@app.route("/offices/<int:office_id>/edit", methods=["POST"])
@admin_required("offices")
def edit_office(office_id):
    try:
        name = clean_text(request.form.get("name"), "Office name", 150)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("offices"))

    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM offices WHERE id = %s", (office_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Office not found.")
        cur.execute(
            "UPDATE offices SET name = %s, description = %s WHERE id = %s",
            (name, description, office_id),
        )
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Office', %s, %s, 'Updated', %s, %s)
        """, (office_id, name, f"Updated from '{current['name']}'.", session["user_id"]))
        conn.commit()
        flash("Office updated.", "success")
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
    except IntegrityError:
        conn.rollback()
        flash("That office already exists.", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("offices"))


@app.route("/offices/<int:office_id>/delete", methods=["POST"])
@admin_required("offices")
def delete_office(office_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM offices WHERE id = %s", (office_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Office not found.")
        cur.execute("DELETE FROM offices WHERE id = %s", (office_id,))
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Office', %s, %s, 'Deleted', 'Office deleted; related equipment is now unassigned from an office.', %s)
        """, (office_id, current["name"], session["user_id"]))
        conn.commit()
        flash("Office deleted. Equipment assigned to it is now unassigned from an office.", "success")
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("offices"))


@app.route("/offices/<int:office_id>/history")
@login_required
def office_history(office_id):
    return organization_history_page("Office", office_id, "offices", "offices", "Office")


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
# INTERNAL MESSENGER
# ============================================================

def get_chat_group(cur, group_id, user_id):
    cur.execute("""
        SELECT g.id, g.name
        FROM chat_groups g
        JOIN chat_group_members gm ON gm.group_id = g.id
        WHERE g.id = %s AND gm.user_id = %s
    """, (group_id, user_id))
    return cur.fetchone()


def serialize_chat_messages(cur, group_id, after_id=0):
    cur.execute("""
        SELECT m.id, m.message, m.created_at, m.updated_at, m.is_deleted, m.sender_id,
               m.reply_to_id, u.full_name AS sender_name, u.username,
               sr.name AS sender_role_name,
               ru.full_name AS reply_sender_name, rm.message AS reply_message,
               rm.is_deleted AS reply_is_deleted
        FROM chat_messages m
        JOIN users u ON u.id = m.sender_id
        JOIN roles sr ON sr.id = u.role_id
        LEFT JOIN chat_messages rm ON rm.id = m.reply_to_id
        LEFT JOIN users ru ON ru.id = rm.sender_id
        WHERE m.group_id = %s AND m.id > %s
        ORDER BY m.id ASC
        LIMIT 100
    """, (group_id, after_id))
    messages = cur.fetchall()
    if not messages:
        return []

    message_ids = [message["id"] for message in messages]
    placeholders = ", ".join(["%s"] * len(message_ids))
    cur.execute(f"""
        SELECT message_id, id, original_name, mime_type, size_bytes
        FROM chat_attachments
        WHERE message_id IN ({placeholders})
        ORDER BY id
    """, message_ids)
    attachments = {}
    for attachment in cur.fetchall():
        attachments.setdefault(attachment["message_id"], []).append({
            "id": attachment["id"],
            "name": attachment["original_name"],
            "mime_type": attachment["mime_type"],
            "size": attachment["size_bytes"],
            "url": url_for("download_chat_attachment", attachment_id=attachment["id"]),
        })

    for message in messages:
        message["attachments"] = [] if message["is_deleted"] else attachments.get(message["id"], [])
        if message["created_at"]:
            message["created_at"] = message["created_at"].strftime("%Y-%m-%d %H:%M")
        if message["updated_at"]:
            message["updated_at"] = message["updated_at"].strftime("%Y-%m-%d %H:%M")
        if message["reply_is_deleted"]:
            message["reply_message"] = "Message deleted"
    return messages


@app.route("/messages")
@login_required
def messages():
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT g.id, g.name, COUNT(gm_all.user_id) AS member_count
        FROM chat_groups g
        JOIN chat_group_members gm ON gm.group_id = g.id AND gm.user_id = %s
        LEFT JOIN chat_group_members gm_all ON gm_all.group_id = g.id
        GROUP BY g.id, g.name
        ORDER BY g.name
    """, (g.current_user["id"],))
    groups = cur.fetchall()

    selected_group = None
    group_id = request.args.get("group", "").strip()
    try:
        group_id = int(group_id) if group_id else (groups[0]["id"] if groups else None)
    except ValueError:
        group_id = groups[0]["id"] if groups else None
    if group_id:
        selected_group = get_chat_group(cur, group_id, g.current_user["id"])
    if selected_group:
        selected_messages = serialize_chat_messages(cur, selected_group["id"], 0)
    else:
        selected_messages = []

    members = []
    group_member_ids = set()
    if g.current_user["role_name"].strip().lower() in MANAGEMENT_ROLES:
        cur.execute("""
            SELECT u.id, u.username, u.full_name, r.name AS role_name
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.is_active = 1
            ORDER BY u.full_name
        """)
        members = cur.fetchall()
        if selected_group:
            cur.execute("SELECT user_id FROM chat_group_members WHERE group_id = %s", (selected_group["id"],))
            group_member_ids = {row["user_id"] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return render_template(
        "messages.html",
        groups=groups,
        selected_group=selected_group,
        selected_messages=selected_messages,
        members=members,
        group_member_ids=group_member_ids,
        can_manage_groups=can_perform_action(g.current_user["role_name"], "messages", g.current_user.get("feature_permissions")),
    )


@app.route("/messages/groups/create", methods=["POST"])
@admin_required("messages")
def create_chat_group():
    try:
        name = clean_text(request.form.get("name"), "Group name", 120)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("messages"))

    raw_members = request.form.getlist("member_ids")
    member_ids = {g.current_user["id"]}
    for raw_id in raw_members:
        try:
            member_id = int(raw_id)
            if member_id > 0:
                member_ids.add(member_id)
        except (TypeError, ValueError):
            flash("One or more selected members are invalid.", "danger")
            return redirect(url_for("messages"))

    conn = db()
    cur = conn.cursor(dictionary=True)
    placeholders = ", ".join(["%s"] * len(member_ids))
    cur.execute(f"SELECT id FROM users WHERE is_active = 1 AND id IN ({placeholders})", tuple(member_ids))
    valid_ids = {row["id"] for row in cur.fetchall()}
    if valid_ids != member_ids:
        cur.close()
        conn.close()
        flash("Only active users can be added to a group.", "danger")
        return redirect(url_for("messages"))
    cur.execute("INSERT INTO chat_groups (name, created_by) VALUES (%s, %s)", (name, g.current_user["id"]))
    group_id = cur.lastrowid
    cur.executemany(
        "INSERT INTO chat_group_members (group_id, user_id) VALUES (%s, %s)",
        [(group_id, member_id) for member_id in sorted(member_ids)],
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Message group created.", "success")
    return redirect(url_for("messages", group=group_id))


@app.route("/messages/groups/<int:group_id>/members", methods=["POST"])
@admin_required("messages")
def add_chat_group_member(group_id):
    try:
        user_id = required_id(request.form.get("user_id"), "Member")
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("messages", group=group_id))

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM chat_groups WHERE id = %s", (group_id,))
    group = cur.fetchone()
    cur.execute("SELECT id, full_name FROM users WHERE id = %s AND is_active = 1", (user_id,))
    member = cur.fetchone()
    cur.execute("SELECT user_id FROM chat_group_members WHERE group_id = %s AND user_id = %s", (group_id, user_id))
    already_member = cur.fetchone()
    if not group or not member:
        cur.close()
        conn.close()
        flash("Only active users can be added to an existing group.", "danger")
        return redirect(url_for("messages", group=group_id))
    if already_member:
        cur.close()
        conn.close()
        flash("That user is already a member of this group.", "danger")
        return redirect(url_for("messages", group=group_id))

    cur.execute("INSERT INTO chat_group_members (group_id, user_id) VALUES (%s, %s)", (group_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{member['full_name']} was added to {group['name']}.", "success")
    return redirect(url_for("messages", group=group_id))


@app.route("/messages/groups/<int:group_id>/delete", methods=["POST"])
@admin_required("messages")
def delete_chat_group(group_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM chat_groups WHERE id = %s", (group_id,))
    group = cur.fetchone()
    cur.execute("""
        SELECT a.stored_name
        FROM chat_attachments a
        JOIN chat_messages m ON m.id = a.message_id
        WHERE m.group_id = %s
    """, (group_id,))
    stored_files = [row["stored_name"] for row in cur.fetchall()]
    if not group:
        cur.close()
        conn.close()
        flash("Message group not found.", "danger")
        return redirect(url_for("messages"))
    cur.execute("DELETE FROM chat_groups WHERE id = %s", (group_id,))
    conn.commit()
    cur.close()
    conn.close()
    for stored_name in stored_files:
        path = os.path.join(CHAT_UPLOAD_DIR, stored_name)
        if os.path.isfile(path):
            os.remove(path)
    flash(f"Message group '{group['name']}' was deleted.", "success")
    return redirect(url_for("messages"))


def get_editable_chat_message(cur, message_id, user_id):
    cur.execute("""
        SELECT m.id, m.group_id, m.sender_id, m.message, m.is_deleted, r.name AS sender_role_name
        FROM chat_messages m
        JOIN chat_group_members gm ON gm.group_id = m.group_id AND gm.user_id = %s
        JOIN users u ON u.id = m.sender_id
        JOIN roles r ON r.id = u.role_id
        WHERE m.id = %s
    """, (user_id, message_id))
    return cur.fetchone()


def can_manage_chat_message(actor, message):
    """Keep Super Admin messages protected from Admin moderation."""
    if message["sender_id"] == actor["id"]:
        return True
    if message["sender_role_name"].strip().lower() == "super admin":
        return actor["role_name"].strip().lower() == "super admin"
    return can_perform_action(actor["role_name"], "messages", actor.get("feature_permissions"))


@app.route("/messages/messages/<int:message_id>/edit", methods=["POST"])
@login_required
def edit_chat_message(message_id):
    try:
        message_text = clean_text(request.form.get("message"), "Message", 4000)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("messages"))
    conn = db()
    cur = conn.cursor(dictionary=True)
    message = get_editable_chat_message(cur, message_id, g.current_user["id"])
    if not message or message["is_deleted"] or not can_manage_chat_message(g.current_user, message):
        cur.close()
        conn.close()
        flash("You cannot edit this message.", "danger")
        return redirect(url_for("messages"))
    cur.execute("UPDATE chat_messages SET message = %s, is_deleted = 0 WHERE id = %s", (message_text, message_id))
    conn.commit()
    cur.close()
    conn.close()
    flash("Message updated.", "success")
    return redirect(url_for("messages", group=message["group_id"]))


@app.route("/messages/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_chat_message(message_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    message = get_editable_chat_message(cur, message_id, g.current_user["id"])
    if not message or message["is_deleted"] or not can_manage_chat_message(g.current_user, message):
        cur.close()
        conn.close()
        flash("You cannot delete this message.", "danger")
        return redirect(url_for("messages"))
    cur.execute("UPDATE chat_messages SET message = NULL, is_deleted = 1 WHERE id = %s", (message_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("Message deleted.", "success")
    return redirect(url_for("messages", group=message["group_id"]))


@app.route("/messages/groups/<int:group_id>/send", methods=["POST"])
@login_required
def send_chat_message(group_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    group = get_chat_group(cur, group_id, g.current_user["id"])
    if not group:
        cur.close()
        conn.close()
        flash("You are not a member of that message group.", "danger")
        return redirect(url_for("messages"))

    message_text = optional_text(request.form.get("message"), 4000)
    uploaded_files = [file for file in request.files.getlist("attachments") if file and file.filename]
    prepared_files = []
    try:
        reply_to_id = None
        raw_reply_id = request.form.get("reply_to_id", "").strip()
        if raw_reply_id:
            reply_to_id = required_id(raw_reply_id, "Reply")
            cur.execute("""
                SELECT id FROM chat_messages
                WHERE id = %s AND group_id = %s AND is_deleted = 0
            """, (reply_to_id, group_id))
            if not cur.fetchone():
                raise ValueError("The message you are replying to is no longer available.")
        for uploaded_file in uploaded_files:
            original_name = secure_filename(uploaded_file.filename)
            extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
            if not original_name or extension not in CHAT_FILE_EXTENSIONS:
                raise ValueError("That file type is not allowed.")
            content = uploaded_file.read()
            if not content or len(content) > 4 * 1024 * 1024:
                raise ValueError("Each attachment must be between 1 byte and 4 MB.")
            prepared_files.append((original_name, uploaded_file.mimetype or "application/octet-stream", content, extension))
        if not message_text and not prepared_files:
            raise ValueError("Write a message or attach a file before sending.")

        cur.execute(
            "INSERT INTO chat_messages (group_id, sender_id, reply_to_id, message) VALUES (%s, %s, %s, %s)",
            (group_id, g.current_user["id"], reply_to_id, message_text or None),
        )
        message_id = cur.lastrowid
        os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
        for original_name, mime_type, content, extension in prepared_files:
            stored_name = f"{secrets.token_urlsafe(24)}.{extension}"
            with open(os.path.join(CHAT_UPLOAD_DIR, stored_name), "wb") as output:
                output.write(content)
            cur.execute("""
                INSERT INTO chat_attachments
                    (message_id, original_name, stored_name, mime_type, size_bytes)
                VALUES (%s, %s, %s, %s, %s)
            """, (message_id, original_name, stored_name, mime_type, len(content)))
        conn.commit()
    except ValueError as error:
        conn.rollback()
        flash(str(error), "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("messages", group=group_id))


@app.route("/api/messages/groups/<int:group_id>")
@login_required
def api_chat_messages(group_id):
    try:
        after_id = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        after_id = 0
    conn = db()
    cur = conn.cursor(dictionary=True)
    group = get_chat_group(cur, group_id, g.current_user["id"])
    if not group:
        cur.close()
        conn.close()
        return jsonify({"error": "Message group not found."}), 404
    result = serialize_chat_messages(cur, group_id, after_id)
    cur.close()
    conn.close()
    return jsonify({"messages": result})


@app.route("/messages/attachments/<int:attachment_id>")
@login_required
def download_chat_attachment(attachment_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.stored_name, a.original_name, g.id AS group_id
        FROM chat_attachments a
        JOIN chat_messages m ON m.id = a.message_id
        JOIN chat_groups g ON g.id = m.group_id
        JOIN chat_group_members gm ON gm.group_id = g.id AND gm.user_id = %s
        WHERE a.id = %s
    """, (g.current_user["id"], attachment_id))
    attachment = cur.fetchone()
    cur.close()
    conn.close()
    if not attachment:
        return render_template("error.html", code=404, title="File not found", message="This attachment is unavailable or you are not allowed to access it."), 404
    path = os.path.join(CHAT_UPLOAD_DIR, attachment["stored_name"])
    if not os.path.isfile(path):
        return render_template("error.html", code=404, title="File not found", message="This attachment is no longer available."), 404
    return send_file(path, as_attachment=True, download_name=attachment["original_name"])


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
            u.feature_permissions,
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
        roles=roles,
        restrictable_features=RESTRICTABLE_FEATURES
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
        email = optional_email(request.form.get("email"))
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

    new_user_id = cur.lastrowid
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Account', %s, %s, 'Created', %s, %s)
    """, (new_user_id, username, f"{selected_role['name']} account created.", g.current_user["id"]))

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
            email = optional_email(request.form.get("email"))
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
            cur.execute("""
                INSERT INTO organization_history
                    (entity_type, entity_id, entity_name, action, details, user_id)
                VALUES ('Account', %s, %s, 'Updated', 'Profile information updated.', %s)
            """, (g.current_user["id"], g.current_user["username"], g.current_user["id"]))
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
        email = optional_email(request.form.get("email"))
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
        details = "Account information and profile picture updated."
        if new_password:
            details += " Password was changed; the password itself was not recorded."
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Account', %s, %s, 'Updated', %s, %s)
        """, (user_id, target["username"], details, g.current_user["id"]))
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
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Account', %s, %s, 'Updated', 'Password was reset; the password itself was not recorded.', %s)
    """, (user_id, target["username"], g.current_user["id"]))
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
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Account', %s, %s, 'Updated', %s, %s)
    """, (user_id, target["username"], f"Account {'activated' if new_status else 'deactivated'}.", g.current_user["id"]))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{target['username']} is now {'active' if new_status else 'inactive'}.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/permissions", methods=["POST"])
@super_admin_required
def update_user_permissions(user_id):
    """Allow only Super Admin to override feature access for managed accounts."""
    requested = set(request.form.getlist("features"))
    feature_keys = {key for key, _label in RESTRICTABLE_FEATURES}
    if not requested.issubset(feature_keys):
        flash("Invalid feature selection.", "danger")
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
        flash("Only Admin and Worker accounts can have feature access changed.", "danger")
        return redirect(url_for("users"))

    permissions = {key: key in requested for key, _label in RESTRICTABLE_FEATURES}
    cur.execute(
        "UPDATE users SET feature_permissions = %s WHERE id = %s",
        (json.dumps(permissions), user_id),
    )
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Account', %s, %s, 'Updated', 'Feature permissions updated.', %s)
    """, (user_id, target["username"], g.current_user["id"]))
    conn.commit()
    cur.close()
    conn.close()
    flash(f"Feature access updated for {target['username']}.", "success")
    return redirect(url_for("users"))


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=app.config["DEBUG"],
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5000"))
    )
