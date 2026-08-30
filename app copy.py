import os
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

def db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "items_db")
    )

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        conn = db()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT u.*, r.name AS role_name
            FROM users u JOIN roles r ON u.role_id=r.id
            WHERE u.username=%s AND u.is_active=1
        """, (username,))
        user = cur.fetchone()
        cur.close(); conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role_name"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM equipment WHERE status <> 'Disposed'")
    total = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM equipment WHERE status='Available'")
    available = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM equipment WHERE status='Assigned'")
    assigned = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM equipment WHERE status='Disposed'")
    disposed = cur.fetchone()["n"]
    cur.close(); conn.close()
    return render_template("dashboard.html", total=total, available=available,
                           assigned=assigned, disposed=disposed)

@app.route("/equipment")
@login_required
def equipment():
    q = request.args.get("q", "").strip()
    conn = db()
    cur = conn.cursor(dictionary=True)
    sql = """
        SELECT e.*, c.name AS category_name, o.name AS office_name,
               a.person_name AS accountable_person
        FROM equipment e
        LEFT JOIN categories c ON e.category_id=c.id
        LEFT JOIN offices o ON e.office_id=o.id
        LEFT JOIN accountability a ON e.id=a.equipment_id AND a.is_current=1
    """
    params = []
    if q:
        sql += """ WHERE e.asset_code LIKE %s OR e.name LIKE %s
                   OR e.serial_number LIKE %s OR c.name LIKE %s """
        params = [f"%{q}%"] * 4
    sql += " ORDER BY e.id DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()

    cur.execute("SELECT id,name FROM categories ORDER BY name")
    categories = cur.fetchall()
    cur.execute("SELECT id,name FROM offices ORDER BY name")
    offices = cur.fetchall()
    cur.close(); conn.close()
    return render_template("equipment.html", equipment=rows, categories=categories,
                           offices=offices, q=q)

@app.route("/equipment/add", methods=["POST"])
@login_required
def add_equipment():
    data = request.form
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO equipment
        (asset_code,name,description,category_id,office_id,serial_number,
         specifications,acquisition_date,status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        data["asset_code"], data["name"], data.get("description",""),
        data["category_id"] or None, data["office_id"] or None,
        data.get("serial_number",""), data.get("specifications",""),
        data.get("acquisition_date") or None, data.get("status","Available")
    ))
    equipment_id = cur.lastrowid
    cur.execute("""
        INSERT INTO transactions (equipment_id,user_id,action,details)
        VALUES (%s,%s,'Created','Equipment record created')
    """, (equipment_id, session["user_id"]))
    conn.commit()
    cur.close(); conn.close()
    flash("Equipment added successfully.", "success")
    return redirect(url_for("equipment"))

@app.route("/equipment/delete/<int:item_id>", methods=["POST"])
@login_required
def archive_equipment(item_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE equipment SET status='Archived' WHERE id=%s", (item_id,))
    cur.execute("""
        INSERT INTO transactions (equipment_id,user_id,action,details)
        VALUES (%s,%s,'Archived','Equipment moved to archive')
    """, (item_id, session["user_id"]))
    conn.commit()
    cur.close(); conn.close()
    flash("Equipment archived.", "success")
    return redirect(url_for("equipment"))

@app.route("/transactions")
@login_required
def transactions():
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT t.*, e.asset_code, e.name AS equipment_name, u.full_name
        FROM transactions t
        JOIN equipment e ON t.equipment_id=e.id
        JOIN users u ON t.user_id=u.id
        ORDER BY t.created_at DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("transactions.html", transactions=rows)

@app.route("/categories")
@login_required
def categories():
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM categories ORDER BY name")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("categories.html", categories=rows)

@app.route("/categories/add", methods=["POST"])
@login_required
def add_category():
    name = request.form["name"].strip()
    if name:
        conn = db(); cur = conn.cursor()
        cur.execute("INSERT INTO categories(name,description) VALUES(%s,%s)",
                    (name, request.form.get("description","")))
        conn.commit(); cur.close(); conn.close()
        flash("Category added.", "success")
    return redirect(url_for("categories"))

@app.route("/api/equipment/<asset_code>")
@login_required
def api_equipment(asset_code):
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT e.*, c.name category_name, o.name office_name
        FROM equipment e
        LEFT JOIN categories c ON e.category_id=c.id
        LEFT JOIN offices o ON e.office_id=o.id
        WHERE e.asset_code=%s
    """, (asset_code,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return jsonify(row or {"error":"Equipment not found"})

@app.route("/users")
@login_required
def users():
    if session.get("role") != "Super Admin":
        flash("Super Admin access required.", "danger")
        return redirect(url_for("dashboard"))
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.id,u.username,u.full_name,u.email,u.is_active,r.name role_name
        FROM users u JOIN roles r ON u.role_id=r.id ORDER BY u.id DESC
    """)
    rows = cur.fetchall()
    cur.execute("SELECT id,name FROM roles ORDER BY id")
    roles = cur.fetchall()
    cur.close(); conn.close()
    return render_template("users.html", users=rows, roles=roles)

@app.route("/users/add", methods=["POST"])
@login_required
def add_user():
    if session.get("role") != "Super Admin":
        return redirect(url_for("dashboard"))
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO users(username,password_hash,full_name,email,role_id)
        VALUES(%s,%s,%s,%s,%s)
    """, (
        request.form["username"], generate_password_hash(request.form["password"]),
        request.form["full_name"], request.form.get("email",""),
        request.form["role_id"]
    ))
    conn.commit(); cur.close(); conn.close()
    flash("User created.", "success")
    return redirect(url_for("users"))

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
