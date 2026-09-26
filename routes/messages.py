"""Live messaging routes."""

from app import *

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
    session["messages_seen_at"] = philippines_now().strftime("%Y-%m-%d %H:%M:%S")
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

