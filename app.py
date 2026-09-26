import os
import hmac
import csv
import json
import secrets
import re
import sys
from functools import wraps
from datetime import date, datetime, timedelta

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
from io import BytesIO

from config import Config, require_secret_key
from database import get_db_connection

# Route modules import shared application helpers from ``app``. When this
# file is started directly, expose the running ``__main__`` module under that
# name so Python does not create a second Flask application during imports.
sys.modules.setdefault("app", sys.modules[__name__])


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

app.secret_key = require_secret_key()
app.config.update(
    DEBUG=Config.DEBUG,
    TEMPLATES_AUTO_RELOAD=Config.DEBUG,
    SESSION_COOKIE_HTTPONLY=Config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=Config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=Config.MAX_CONTENT_LENGTH
)

PROFILE_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads", "profiles")
CHAT_UPLOAD_DIR = os.path.join(app.root_path, "private_uploads", "chat")
PROFILE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
CHAT_FILE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx", "xls", "xlsx", "txt", "zip"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


_remote_scanner_sessions = {}
REMOTE_SCANNER_TTL_SECONDS = 300
_login_failures = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_RATE_WINDOW_SECONDS = 15 * 60


@app.context_processor
def inject_security_helpers():
    def csrf_token():
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    return {"csrf_token": csrf_token}


@app.context_processor
def inject_sidebar_notifications():
    notifications = {"unread_messages": 0, "pending_requests": 0}
    if not session.get("user_id"):
        return notifications

    conn = None
    cur = None
    try:
        conn = db()
        cur = conn.cursor(dictionary=True)
        user_id = session["user_id"]
        role = (session.get("role") or "").strip().lower()

        seen_at = session.get("messages_seen_at")
        if seen_at:
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM chat_messages m
                JOIN chat_group_members gm ON gm.group_id = m.group_id AND gm.user_id = %s
                WHERE m.sender_id <> %s
                  AND m.is_deleted = 0
                  AND m.created_at > %s
            """, (user_id, user_id, seen_at))
        else:
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM chat_messages m
                JOIN chat_group_members gm ON gm.group_id = m.group_id AND gm.user_id = %s
                WHERE m.sender_id <> %s AND m.is_deleted = 0
            """, (user_id, user_id))
        notifications["unread_messages"] = cur.fetchone()["total"]

        requests_seen_at = session.get("requests_seen_at")
        request_created_filter = " AND created_at > %s" if requests_seen_at else ""
        request_params = (requests_seen_at,) if requests_seen_at else ()

        if role in MANAGEMENT_ROLES:
            cur.execute(
                "SELECT COUNT(*) AS total FROM equipment "
                "WHERE approval_status = 'Pending'" + request_created_filter,
                request_params,
            )
            equipment_count = cur.fetchone()["total"]
            cur.execute(
                "SELECT COUNT(*) AS total FROM equipment_action_requests "
                "WHERE status = 'Pending'" + request_created_filter,
                request_params,
            )
            action_count = cur.fetchone()["total"]
            cur.execute(
                "SELECT COUNT(*) AS total FROM organization_requests "
                "WHERE status = 'Pending'" + request_created_filter,
                request_params,
            )
            organization_count = cur.fetchone()["total"]
            notifications["pending_requests"] = equipment_count + action_count + organization_count
        elif role == STAFF_ROLE:
            staff_params = (user_id,) + request_params
            cur.execute(
                "SELECT COUNT(*) AS total FROM equipment "
                "WHERE created_by = %s AND approval_status = 'Pending'" + request_created_filter,
                staff_params,
            )
            equipment_count = cur.fetchone()["total"]
            cur.execute(
                "SELECT COUNT(*) AS total FROM equipment_action_requests "
                "WHERE requested_by = %s AND status = 'Pending'" + request_created_filter,
                staff_params,
            )
            action_count = cur.fetchone()["total"]
            cur.execute(
                "SELECT COUNT(*) AS total FROM organization_requests "
                "WHERE requested_by = %s AND status = 'Pending'" + request_created_filter,
                staff_params,
            )
            organization_count = cur.fetchone()["total"]
            notifications["pending_requests"] = equipment_count + action_count + organization_count
    except mysql.connector.Error:
        app.logger.exception("Could not load sidebar notifications")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return notifications


@app.before_request
def protect_state_changing_requests():
    if request.method == "POST":
        if request.path.startswith("/qr-scanner/remote/"):
            return None
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
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
    return get_db_connection()

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
    "Re-Assigned",
    "Unassigned",
    "Maintenance",
    "Repair",
    "Archived",
    "Restored",
    "Disposed",
    "Requested",
    "Approved",
    "Rejected",
    "Requested/Create",
    "Approved/Create",
    "Rejected/Create",
    "Requested/Edit",
    "Approved/Edit",
    "Rejected/Edit",
    "Requested/Assign",
    "Approved/Assign",
    "Rejected/Assign",
    "Requested/Maintenance Start",
    "Approved/Maintenance Start",
    "Rejected/Maintenance Start",
    "Requested/Maintenance Complete",
    "Approved/Maintenance Complete",
    "Rejected/Maintenance Complete",
    "Requested/Archive",
    "Approved/Archive",
    "Rejected/Archive",
    "Requested/Dispose",
    "Approved/Dispose",
    "Rejected/Dispose",
)

MANAGEMENT_ROLES = {"super admin", "admin"}
STAFF_ROLE = "staff"
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
    ("view_all_equipment", "View all equipment"),
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


def validate_password_strength(password, field_name="Password"):
    """Apply the same password policy to every account-management path."""
    if len(password or "") < 8:
        raise ValueError(f"{field_name} must be at least 8 characters long.")
    if not re.search(r"[A-Z]", password):
        raise ValueError(f"{field_name} must contain at least one uppercase letter.")
    if not re.search(r"[0-9]", password):
        raise ValueError(f"{field_name} must contain at least one number.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError(f"{field_name} must contain at least one special character.")
    return password


def _login_key(ip_address, username):
    return (ip_address or "unknown", (username or "").lower())


def login_is_rate_limited(ip_address, username):
    now = datetime.now().timestamp()
    key = _login_key(ip_address, username)
    attempts = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_RATE_WINDOW_SECONDS]
    _login_failures[key] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(ip_address, username):
    now = datetime.now().timestamp()
    key = _login_key(ip_address, username)
    attempts = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_RATE_WINDOW_SECONDS]
    attempts.append(now)
    _login_failures[key] = attempts


def clear_login_failures(ip_address, username):
    _login_failures.pop(_login_key(ip_address, username), None)


def required_id(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} is required.") from error
    if parsed <= 0:
        raise ValueError(f"{field_name} is required.")
    return parsed


from utils import csv_safe, csv_datetime


def role_allows_action(role_name, action):
    role = (role_name or "").strip().lower()
    if action == "manage_users":
        return role in MANAGEMENT_ROLES
    if action in {"categories", "offices", "messages"}:
        return role in MANAGEMENT_ROLES
    if action == "requests":
        return role in MANAGEMENT_ROLES
    if action == "view_all_equipment":
        return role in MANAGEMENT_ROLES
    if action == "add" and role == STAFF_ROLE:
        return True
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
    return user and user["role_name"].strip().lower() == STAFF_ROLE


def is_staff_role(role_name):
    """Return whether a role is the renamed Staff role."""
    return (role_name or "").strip().lower() == STAFF_ROLE


def is_staff():
    user = get_current_user()
    return bool(user and is_staff_role(user["role_name"]))


app.template_global("is_staff_role")(is_staff_role)


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
        return ("Admin", "Staff")
    if role == "admin":
        return ("Staff",)
    return ()


def manageable_roles(role_name):
    """Return account roles this manager may edit, excluding their own role."""
    role = (role_name or "").strip().lower()
    if role == "super admin":
        return ("Admin", "Staff")
    if role == "admin":
        return ("Staff",)
    return ()


# ============================================================
# ADMIN / MANAGEMENT REQUIRED
# Worker accounts cannot modify equipment/categories
# ============================================================

def admin_required(action="edit", allow_staff=False):
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
from routes.auth import register_auth_routes
import routes.organization
import routes.messages
import routes.users
import routes.equipment
import routes.dashboard

register_auth_routes(app)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    ssl_context = "adhoc" if Config.APP_HTTPS else None

    app.run(
        debug=app.config["DEBUG"],
        host=Config.APP_HOST,
        port=Config.APP_PORT,
        ssl_context=ssl_context
    )

