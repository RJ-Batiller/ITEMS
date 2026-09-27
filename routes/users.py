"""User and account management routes."""

from app import *


def read_profile_picture(uploaded_picture, user_id):
    """Read a profile picture into memory so it survives web-container restarts."""
    safe_name = secure_filename(uploaded_picture.filename)
    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if extension not in PROFILE_IMAGE_EXTENSIONS:
        raise ValueError("Profile picture must be JPG, PNG, GIF, or WEBP.")

    image_data = uploaded_picture.read()
    if not image_data:
        raise ValueError("The selected profile picture is empty.")
    if len(image_data) > PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Profile pictures must be 4 MB or smaller.")

    filename = f"{user_id}_{secrets.token_hex(8)}.{extension}"
    return filename, image_data, PROFILE_IMAGE_MIME_TYPES[extension]

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
            (u.profile_picture_data IS NOT NULL) AS has_profile_picture,
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


@app.route("/profile-picture/<int:user_id>")
@login_required
def profile_picture(user_id):
    """Serve profile pictures from MySQL without exposing the upload directory."""
    current_role = g.current_user["role_name"].strip().lower()
    if user_id != g.current_user["id"] and current_role not in MANAGEMENT_ROLES:
        return "", 404

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT profile_picture_data, profile_picture_mime, profile_picture
        FROM users
        WHERE id = %s AND is_active = 1
        """,
        (user_id,),
    )
    picture = cur.fetchone()
    cur.close()
    conn.close()

    if not picture:
        return "", 404
    if picture["profile_picture_data"]:
        response = send_file(
            BytesIO(picture["profile_picture_data"]),
            mimetype=picture["profile_picture_mime"] or "application/octet-stream",
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-cache, no-store, must-revalidate"
        return response

    # Compatibility for local installations that still have the old file.
    if picture["profile_picture"]:
        legacy_path = os.path.join(PROFILE_UPLOAD_DIR, picture["profile_picture"])
        if os.path.isfile(legacy_path):
            return send_file(legacy_path, max_age=0)
    return "", 404


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
        validate_password_strength(password)
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
                validate_password_strength(new_password, "New password")
                if new_password != confirm_password:
                    raise ValueError("New password and confirmation do not match.")

            profile_picture = None
            profile_picture_data = None
            profile_picture_mime = None
            uploaded_picture = request.files.get("profile_picture")
            if uploaded_picture and uploaded_picture.filename:
                if g.current_user["role_name"].strip().lower() not in MANAGEMENT_ROLES:
                    raise ValueError("Only Super Admin and Admin accounts may change profile pictures.")
                profile_picture, profile_picture_data, profile_picture_mime = read_profile_picture(
                    uploaded_picture, g.current_user["id"]
                )

            conn = db()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT password_hash, profile_picture FROM users WHERE id = %s", (g.current_user["id"],))
            account = cur.fetchone()
            if new_password and (not account or not check_password_hash(account["password_hash"], current_password)):
                raise ValueError("Current password is incorrect.")

            if new_password:
                cur.execute("""
                    UPDATE users
                    SET full_name = %s, email = %s, password_hash = %s,
                        profile_picture = COALESCE(%s, profile_picture),
                        profile_picture_data = COALESCE(%s, profile_picture_data),
                        profile_picture_mime = COALESCE(%s, profile_picture_mime)
                    WHERE id = %s
                """, (full_name, email, generate_password_hash(new_password), profile_picture,
                      profile_picture_data, profile_picture_mime, g.current_user["id"]))
            else:
                cur.execute("""
                    UPDATE users SET full_name = %s, email = %s,
                    profile_picture = COALESCE(%s, profile_picture),
                    profile_picture_data = COALESCE(%s, profile_picture_data),
                    profile_picture_mime = COALESCE(%s, profile_picture_mime)
                    WHERE id = %s
                """, (full_name, email, profile_picture, profile_picture_data,
                      profile_picture_mime, g.current_user["id"]))
            cur.execute("""
                INSERT INTO organization_history
                    (entity_type, entity_id, entity_name, action, details, user_id)
                VALUES ('Account', %s, %s, 'Updated', 'Profile information updated.', %s)
            """, (g.current_user["id"], g.current_user["username"], g.current_user["id"]))
            conn.commit()
            session["full_name"] = full_name
            if profile_picture:
                session["profile_picture"] = profile_picture
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
# Super Admin may reset or disable Admin/Staff accounts only.
# ============================================================

@app.route("/users/<int:user_id>/update", methods=["POST"])
@user_management_required
def update_managed_user(user_id):
    conn = None
    cur = None
    try:
        full_name = clean_text(request.form.get("full_name"), "Full name", 150)
        email = optional_email(request.form.get("email"))
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if new_password:
            validate_password_strength(new_password, "New password")
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
        profile_picture_data = None
        profile_picture_mime = None
        if uploaded_picture and uploaded_picture.filename:
            profile_picture, profile_picture_data, profile_picture_mime = read_profile_picture(
                uploaded_picture, user_id
            )

        cur.execute("""
            UPDATE users
            SET full_name = %s,
                email = %s,
                profile_picture = COALESCE(%s, profile_picture),
                profile_picture_data = COALESCE(%s, profile_picture_data),
                profile_picture_mime = COALESCE(%s, profile_picture_mime),
                password_hash = COALESCE(%s, password_hash)
            WHERE id = %s
        """, (full_name, email, profile_picture, profile_picture_data, profile_picture_mime,
              generate_password_hash(new_password) if new_password else None, user_id))
        details = "Account information and profile picture updated."
        if new_password:
            details += " Password was changed; the password itself was not recorded."
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Account', %s, %s, 'Updated', %s, %s)
        """, (user_id, target["username"], details, g.current_user["id"]))
        conn.commit()
        flash(f"{target['username']} account updated.", "success")
    except ValueError as error:
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
        validate_password_strength(new_password, "New password")
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
        flash("Only Admin and Staff passwords can be reset here.", "danger")
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
        flash("Only Admin and Staff accounts can be activated or deactivated here.", "danger")
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
        flash("Only Admin and Staff accounts can have feature access changed.", "danger")
        return redirect(url_for("users"))

    # Viewing the full catalog is deliberately read-only for Staff accounts.
    if target["role_name"].strip().lower() == "staff" and "view_all_equipment" in requested:
        requested.difference_update(EQUIPMENT_WRITE_ACTIONS)

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

