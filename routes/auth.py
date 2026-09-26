"""Authentication and session routes."""

import secrets

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app import clear_login_failures, db, login_is_rate_limited, record_login_failure


def register_auth_routes(app):
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    def login():
        if request.method == "GET" and session.get("user_id"):
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            if login_is_rate_limited(request.remote_addr, username):
                flash("Too many failed login attempts. Please try again later.", "danger")
                return render_template("login.html"), 429

            conn = db()
            cur = conn.cursor(dictionary=True)
            cur.execute("""
                SELECT u.*, r.name AS role_name
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.username = %s AND u.is_active = 1
            """, (username,))
            user = cur.fetchone()
            valid_login = bool(user and check_password_hash(user["password_hash"], password))
            is_first_login = bool(valid_login and (user.get("login_count") or 0) == 0)
            if valid_login:
                cur.execute("UPDATE users SET login_count = login_count + 1 WHERE id = %s", (user["id"],))
                conn.commit()
            cur.close()
            conn.close()

            if valid_login:
                session.clear()
                session["user_id"] = user["id"]
                session["full_name"] = user["full_name"]
                session["role"] = user["role_name"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                session["login_greeting"] = (
                    f"Welcome, {user['full_name']}"
                    if is_first_login
                    else f"Welcome back, {user['full_name']}"
                )
                session.permanent = True
                clear_login_failures(request.remote_addr, username)
                return redirect(url_for("dashboard"))

            record_login_failure(request.remote_addr, username)
            flash("Invalid username or password.", "danger")

        return render_template("login.html")

    def logout():
        session.clear()
        return redirect(url_for("login"))

    app.add_url_rule("/", endpoint="index", view_func=index)
    app.add_url_rule("/login", endpoint="login", view_func=login, methods=["GET", "POST"])
    app.add_url_rule("/logout", endpoint="logout", view_func=logout)
