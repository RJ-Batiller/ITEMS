import os
import hmac
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
    g
)

from werkzeug.security import generate_password_hash, check_password_hash

import mysql.connector
from mysql.connector.errors import IntegrityError
from dotenv import load_dotenv

import qrcode
from io import BytesIO


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
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "items_db")
    )

VALID_EQUIPMENT_STATUSES = {
    "Available",
    "Under Maintenance",
    "Assigned",
    "Archived",
    "Disposed"
}


def validate_status_change(old_status, new_status):
    if new_status not in VALID_EQUIPMENT_STATUSES:
        return False
    if old_status in {"Archived", "Disposed"}:
        return new_status == old_status
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
        SELECT u.id, u.full_name, u.is_active, r.name AS role_name
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
        if user["role_name"].strip().lower() == "worker":

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
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status NOT IN ('Archived', 'Disposed')
    """)
    total = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status = 'Available'
    """)
    available = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status = 'Assigned'
    """)
    assigned = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status = 'Under Maintenance'
    """)
    maintenance = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status = 'Archived'
    """)
    archived = cur.fetchone()["n"]

    cur.execute("""
        SELECT COUNT(*) AS n
        FROM equipment
        WHERE status = 'Disposed'
    """)
    disposed = cur.fetchone()["n"]

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        available=available,
        assigned=assigned,
        maintenance=maintenance,
        archived=archived,
        disposed=disposed
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

    params = []

    if q:

        sql += """
            WHERE
                e.asset_code LIKE %s
                OR e.name LIKE %s
                OR e.serial_number LIKE %s
                OR c.name LIKE %s
                OR o.name LIKE %s
                OR e.status LIKE %s
                OR a.person_name LIKE %s
        """

        search = f"%{q}%"

        params = [
            search,
            search,
            search,
            search,
            search,
            search,
            search
        ]

    sql += """
        ORDER BY e.id DESC
    """

    cur.execute(
        sql,
        params
    )

    rows = cur.fetchall()

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
        "equipment.html",
        equipment=rows,
        categories=categories,
        offices=offices,
        q=q
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
        is_worker=is_worker()
    )


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

    if equipment["status"] in {"Archived", "Disposed"}:
        cur.close()
        conn.close()
        flash("Archived or disposed equipment cannot be archived again.", "danger")
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

    if equipment["status"] in {"Archived", "Disposed"}:
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

    if equipment["status"] in {"Archived", "Disposed"}:
        cur.close()
        conn.close()
        flash("Archived or disposed equipment cannot be assigned.", "danger")
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

    params = []

    if q:
        sql += """
            WHERE CAST(t.created_at AS CHAR) LIKE %s
               OR e.asset_code LIKE %s
               OR e.name LIKE %s
               OR t.action LIKE %s
               OR u.full_name LIKE %s
        """
        search = f"%{q}%"
        params = [search] * 5

    sql += " ORDER BY t.created_at DESC"

    cur.execute(sql, params)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "transactions.html",
        transactions=rows,
        q=q
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
# SUPER ADMIN ONLY
# ============================================================

@app.route("/users")
@super_admin_required
def users():
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            u.id,
            u.username,
            u.full_name,
            u.email,
            u.is_active,
            r.name AS role_name
        FROM users u

        JOIN roles r
            ON u.role_id = r.id

        ORDER BY u.id DESC
    """)

    rows = cur.fetchall()

    cur.execute("""
        SELECT id, name
        FROM roles
        ORDER BY id
    """)

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
# SUPER ADMIN ONLY
# ============================================================

@app.route(
    "/users/add",
    methods=["POST"]
)
@super_admin_required
def add_user():
    # ========================================================
    # GET SELECTED ROLE
    # ========================================================

    try:
        username = clean_text(request.form.get("username"), "Username", 80)
        password = clean_text(request.form.get("password"), "Password", 255)
        full_name = clean_text(request.form.get("full_name"), "Full name", 150)
        email = optional_text(request.form.get("email"), 150)
        role_id = request.form.get("role_id")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long.")
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
    # BLOCK SUPER ADMIN CREATION
    # ========================================================

    if selected_role and selected_role["name"].strip().lower() == "super admin":

        cur.close()
        conn.close()

        flash(
            "Creating a Super Admin account is not allowed.",
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
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=app.config["DEBUG"],
        host="127.0.0.1",
        port=5000
    )