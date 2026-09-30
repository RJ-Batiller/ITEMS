"""Transactions, categories, offices, and QR routes."""

from app import *
from app import _remote_scanner_sessions
from services.report_service import build_xlsx

ORGANIZATION_ACTION_GROUPS = {
    "Create": ("Created", "Requested/Create", "Approved/Create", "Rejected/Create"),
    "Update": ("Updated", "Requested/Edit", "Approved/Edit", "Rejected/Edit"),
    "Delete": ("Deleted", "Requested/Delete", "Approved/Delete", "Rejected/Delete"),
}

EQUIPMENT_STATUS_GROUPS = {
    "Requested": "Requested/%",
    "Approved": "Approved/%",
    "Rejected": "Rejected/%",
}

EQUIPMENT_ACTION_GROUPS = {
    "Create": ("Created", "Requested/Create", "Approved/Create", "Rejected/Create"),
    "Update": ("Updated", "Requested/Edit", "Approved/Edit", "Rejected/Edit"),
    "Assign": ("Assigned", "Re-Assigned", "Requested/Assign", "Requested/Re-Assign", "Approved/Assign", "Approved/Re-Assign", "Rejected/Assign", "Rejected/Re-Assign"),
    "Unassign": ("Unassigned",),
    "Maintenance Start": ("Maintenance", "Requested/Maintenance Start", "Approved/Maintenance Start", "Rejected/Maintenance Start"),
    "Maintenance Complete": ("Repair", "Requested/Maintenance Complete", "Approved/Maintenance Complete", "Rejected/Maintenance Complete"),
    "Archive": ("Archived", "Requested/Archive", "Approved/Archive", "Rejected/Archive"),
    "Restore": ("Restored",),
    "Dispose": ("Disposed", "Requested/Dispose", "Approved/Dispose", "Rejected/Dispose"),
}


def _staff_can_view_equipment(equipment):
    """Match QR access to the equipment catalog visibility rules."""
    if not equipment or not is_staff_role(g.current_user["role_name"]):
        return bool(equipment)
    can_view_all = can_perform_action(
        g.current_user["role_name"],
        "view_all_equipment",
        g.current_user.get("feature_permissions"),
    )
    return (
        can_view_all
        or equipment["created_by"] == g.current_user["id"]
        or equipment.get("accountable_user_id") == g.current_user["id"]
    )


def _queue_organization_request(entity_type, request_data):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id
        FROM organization_requests
        WHERE entity_type = %s
          AND requested_by = %s
          AND status = 'Pending'
          AND request_data = %s
        LIMIT 1
    """, (entity_type, g.current_user["id"], json.dumps(request_data)))
    if cur.fetchone():
        cur.close()
        conn.close()
        return False
    cur.execute("""
        INSERT INTO organization_requests (entity_type, requested_by, request_data)
        VALUES (%s, %s, %s)
    """, (entity_type, g.current_user["id"], json.dumps(request_data)))
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES (%s, NULL, %s, %s, %s, %s)
    """, (
        entity_type,
        request_data["name"],
        f"Requested/{request_data.get('operation', 'Create')}",
        f"Staff requested {entity_type.lower()} approval.",
        g.current_user["id"],
    ))
    conn.commit()
    cur.close()
    conn.close()
    return True

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
    try:
        requested_page = int(request.args.get("page", "1"))
    except ValueError:
        requested_page = 1

    if len(action) > 80:
        action = ""
    if action not in EQUIPMENT_ACTION_GROUPS and action not in EQUIPMENT_STATUS_GROUPS:
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

    if is_staff_role(g.current_user["role_name"]):
        can_view_all = can_perform_action(
            g.current_user["role_name"],
            "view_all_equipment",
            g.current_user.get("feature_permissions"),
        )
        if not can_view_all:
            filters.append("""
                (
                    t.user_id = %s
                    OR e.created_by = %s
                    OR EXISTS (
                        SELECT 1
                        FROM accountability historical_accountability
                        WHERE historical_accountability.equipment_id = e.id
                          AND historical_accountability.accountable_user_id = %s
                    )
                )
            """)
            params.extend([g.current_user["id"], g.current_user["id"], g.current_user["id"]])

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

    if action in EQUIPMENT_STATUS_GROUPS:
        filters.append("t.action LIKE %s")
        params.append(EQUIPMENT_STATUS_GROUPS[action])
    elif action:
        values = EQUIPMENT_ACTION_GROUPS[action]
        filters.append("t.action IN (" + ", ".join(["%s"] * len(values)) + ")")
        params.extend(values)
    if date_from:
        filters.append("t.created_at >= %s")
        params.append(date_from)

    if date_to:
        filters.append("t.created_at < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(date_to)

    if filters:
        sql += " WHERE " + " AND ".join(filters)

    sql += " ORDER BY t.created_at DESC"
    count_sql = f"SELECT COUNT(*) AS total FROM ({sql.rsplit(' ORDER BY', 1)[0]}) filtered_transactions"
    cur.execute(count_sql, params)
    pagination = build_pagination(cur.fetchone()["total"], requested_page)
    if request.args.get("export") in {"csv", "xlsx"}:
        cur.execute(sql, params)
    else:
        cur.execute(sql + " LIMIT %s OFFSET %s", [*params, pagination["per_page"], pagination["offset"]])
    rows = cur.fetchall()

    transaction_actions = tuple(EQUIPMENT_ACTION_GROUPS.keys()) + tuple(EQUIPMENT_STATUS_GROUPS.keys())

    cur.close()
    conn.close()

    if request.args.get("export") in {"csv", "xlsx"}:
        content = build_xlsx([
            "Date", "Property Number", "Equipment", "Action", "Details", "Performed By",
        ], [
            [
                csv_datetime(row["created_at"]),
                csv_safe(row["asset_code"]),
                csv_safe(row["equipment_name"]),
                csv_safe(row["action"]),
                csv_safe(row["details"]),
                csv_safe(row["full_name"]),
            ] for row in rows
        ], "Transactions")

        response = make_response(content)
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = "attachment; filename=items-transaction-report.xlsx"
        return response

    return render_template(
        "transactions.html",
        transactions=rows,
        q=q,
        action=action,
        date_from=date_from or "",
        date_to=date_to or "",
        transaction_actions=transaction_actions,
        pagination=pagination,
        pagination_params={"q": q, "action": action, "date_from": date_from or "", "date_to": date_to or ""},
    )


@app.route("/transactions/<int:transaction_id>")
@login_required
def transaction_detail(transaction_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    staff_user = is_staff_role(g.current_user["role_name"])
    can_view_all = can_perform_action(
        g.current_user["role_name"],
        "view_all_equipment",
        g.current_user.get("feature_permissions"),
    )
    scope = """
        (
            t.user_id = %s
            OR e.created_by = %s
            OR EXISTS (
                SELECT 1
                FROM accountability historical_accountability
                WHERE historical_accountability.equipment_id = e.id
                  AND historical_accountability.accountable_user_id = %s
            )
        )
    """ if staff_user and not can_view_all else "1 = 1"
    params = [transaction_id]
    if staff_user and not can_view_all:
        params.extend([g.current_user["id"], g.current_user["id"], g.current_user["id"]])

    cur.execute(f"""
        SELECT
            t.*,
            e.id AS equipment_id,
            e.asset_code,
            e.name AS equipment_name,
            e.serial_number,
            u.full_name
        FROM transactions t
        JOIN equipment e ON e.id = t.equipment_id
        JOIN users u ON u.id = t.user_id
        WHERE t.id = %s AND {scope}
    """, params)
    transaction = cur.fetchone()
    cur.close()
    conn.close()

    if not transaction:
        return render_template(
            "error.html",
            code=404,
            title="Transaction not found",
            message="This transaction does not exist or is not available to your account.",
        ), 404

    return render_template("transaction_view.html", transaction=transaction)


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

    staff_user = is_staff_role(g.current_user["role_name"])
    can_view_all = can_perform_action(
        g.current_user["role_name"],
        "view_all_equipment",
        g.current_user.get("feature_permissions"),
    )
    transaction_scope = """
        (
            t.user_id = %s
            OR e.created_by = %s
            OR EXISTS (
                SELECT 1
                FROM accountability historical_accountability
                WHERE historical_accountability.equipment_id = e.id
                  AND historical_accountability.accountable_user_id = %s
            )
        )
    """ if staff_user and not can_view_all else "1 = 1"
    transaction_scope_params = [g.current_user["id"], g.current_user["id"], g.current_user["id"]] if staff_user and not can_view_all else []
    cur.execute(f"""
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
          AND {transaction_scope}

        ORDER BY t.created_at DESC
    """, [item_id, *transaction_scope_params])

    rows = cur.fetchall()

    equipment_scope = """
        (
            e.created_by = %s
            OR EXISTS (
                SELECT 1
                FROM accountability historical_accountability
                WHERE historical_accountability.equipment_id = e.id
                  AND historical_accountability.accountable_user_id = %s
            )
        )
    """ if staff_user and not can_view_all else "1 = 1"
    equipment_scope_params = [g.current_user["id"], g.current_user["id"]] if staff_user and not can_view_all else []
    cur.execute(f"""
        SELECT id, asset_code, name
        FROM equipment
        WHERE id = %s AND {equipment_scope}
    """, [item_id, *equipment_scope_params])

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
    try:
        requested_page = int(request.args.get("page", "1"))
    except ValueError:
        requested_page = 1
    allowed_types = {"Account", "Category", "Office"}
    if entity_type not in allowed_types:
        entity_type = ""
    if action not in ORGANIZATION_ACTION_GROUPS:
        action = ""

    conn = db()
    cur = conn.cursor(dictionary=True)
    sql = """
        SELECT h.entity_type, h.entity_name, h.action, h.details,
               h.created_at, u.full_name
        FROM organization_history h
        JOIN users u ON u.id = h.user_id
    """
    filters = ["h.entity_type IN ('Account', 'Category', 'Office')"]
    params = []
    if q:
        filters.append("(h.entity_name LIKE %s OR h.details LIKE %s OR u.full_name LIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    if entity_type:
        filters.append("h.entity_type = %s")
        params.append(entity_type)
    if action:
        values = ORGANIZATION_ACTION_GROUPS[action]
        filters.append("h.action IN (" + ", ".join(["%s"] * len(values)) + ")")
        params.extend(values)
    if is_staff_role(g.current_user["role_name"]):
        filters.append("h.user_id = %s")
        params.append(g.current_user["id"])
    if filters:
        sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY h.created_at DESC, h.id DESC"
    count_sql = f"SELECT COUNT(*) AS total FROM ({sql.rsplit(' ORDER BY', 1)[0]}) filtered_history"
    cur.execute(count_sql, params)
    pagination = build_pagination(cur.fetchone()["total"], requested_page)
    if request.args.get("export") in {"csv", "xlsx"}:
        cur.execute(sql, params)
    else:
        cur.execute(sql + " LIMIT %s OFFSET %s", [*params, pagination["per_page"], pagination["offset"]])
    history = cur.fetchall()
    action_options = tuple(ORGANIZATION_ACTION_GROUPS.keys())
    cur.close()
    conn.close()
    if request.args.get("export") in {"csv", "xlsx"}:
        content = build_xlsx([
            "Date", "Type", "Name", "Action", "Details", "Changed By",
        ], [
            [
                row["created_at"].strftime("%Y-%m-%d %H:%M") if row["created_at"] else "",
                row["entity_type"],
                row["entity_name"],
                row["action"],
                row["details"] or "",
                row["full_name"],
            ] for row in history
        ], "Organization History")
        response = make_response(content)
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        response.headers["Content-Disposition"] = "attachment; filename=items-organization-history.xlsx"
        return response
    return render_template(
        "organization_history.html",
        history=history,
        q=q,
        entity_type=entity_type,
        action=action,
        action_options=action_options,
        pagination=pagination,
        pagination_params={"q": q, "entity_type": entity_type, "action": action},
    )


# ============================================================
# ADD CATEGORY
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/categories/add",
    methods=["POST"]
)
@admin_required("categories", allow_staff=True)
def add_category():

    try:
        name = clean_text(request.form.get("name"), "Category name", 100)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("categories"))

    if is_staff_role(g.current_user["role_name"]):
        queued = _queue_organization_request("Category", {"name": name, "description": description})
        flash(
            "Category request submitted for administrative approval." if queued
            else "This category request is already pending.",
            "success" if queued else "warning",
        )
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


@app.route("/organization-requests/<int:request_id>/approve", methods=["POST"])
@admin_required("requests")
def approve_organization_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT r.*, u.full_name AS requested_by_name
            FROM organization_requests r
            JOIN users u ON u.id = r.requested_by
            WHERE r.id = %s AND r.status = 'Pending'
        """, (request_id,))
        request_row = cur.fetchone()
        if not request_row:
            raise ValueError("That organization request has already been reviewed or no longer exists.")
        data = json.loads(request_row["request_data"] or "{}")
        operation = data.get("operation", "Create")
        name = clean_text(data.get("name"), f"{request_row['entity_type']} name", 150 if request_row["entity_type"] == "Office" else 100)
        description = optional_text(data.get("description"), 255)
        table_name = "categories" if request_row["entity_type"] == "Category" else "offices"
        if operation == "Create":
            cur.execute(
                f"INSERT INTO {table_name} (name, description) VALUES (%s, %s)",
                (name, description),
            )
            resource_id = cur.lastrowid
            history_action = "Created"
        elif operation == "Update":
            resource_id = required_id(data.get("entity_id"), f"{request_row['entity_type']} ID")
            cur.execute(f"SELECT name FROM {table_name} WHERE id = %s", (resource_id,))
            current = cur.fetchone()
            if not current:
                raise ValueError(f"{request_row['entity_type']} not found.")
            cur.execute(
                f"UPDATE {table_name} SET name = %s, description = %s WHERE id = %s",
                (name, description, resource_id),
            )
            history_action = "Updated"
        elif operation == "Delete":
            resource_id = required_id(data.get("entity_id"), f"{request_row['entity_type']} ID")
            cur.execute(f"SELECT name FROM {table_name} WHERE id = %s", (resource_id,))
            current = cur.fetchone()
            if not current:
                raise ValueError(f"{request_row['entity_type']} not found.")
            cur.execute(f"DELETE FROM {table_name} WHERE id = %s", (resource_id,))
            history_action = "Deleted"
        else:
            raise ValueError("Unknown organization request operation.")

        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            request_row["entity_type"], resource_id, name,
            f"Approved/{history_action}",
            f"{request_row['entity_type']} request approved for {request_row['requested_by_name']}.",
            g.current_user["id"],
        ))
        cur.execute("""
            UPDATE organization_requests
            SET status = 'Approved', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
            WHERE id = %s AND status = 'Pending'
        """, (g.current_user["id"], "Request approved", request_id))
        conn.commit()
        flash(f"{request_row['entity_type']} request approved.", "success")
    except IntegrityError:
        conn.rollback()
        flash("That name already exists.", "danger")
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        conn.rollback()
        flash(str(error), "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("equipment_requests"))


@app.route("/organization-requests/<int:request_id>/view")
@admin_required("requests")
def view_organization_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT r.*, u.full_name AS requested_by_name, u.username AS requested_by_username
        FROM organization_requests r
        JOIN users u ON u.id = r.requested_by
        WHERE r.id = %s
    """, (request_id,))
    request_row = cur.fetchone()
    cur.close()
    conn.close()

    if not request_row:
        flash("Organization request not found.", "warning")
        return redirect(url_for("equipment_requests"))

    try:
        request_row["request_data"] = json.loads(request_row["request_data"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        request_row["request_data"] = {}

    return render_template("organization_request_view.html", request_row=request_row)


@app.route("/organization-requests/<int:request_id>/reject", methods=["POST"])
@admin_required("requests")
def reject_organization_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT r.entity_type, r.request_data, u.full_name AS requested_by_name
        FROM organization_requests r
        JOIN users u ON u.id = r.requested_by
        WHERE r.id = %s AND r.status = 'Pending'
    """, (request_id,))
    request_row = cur.fetchone()
    if not request_row:
        cur.close()
        conn.close()
        flash("That organization request has already been reviewed or no longer exists.", "warning")
        return redirect(url_for("equipment_requests"))
    try:
        request_data = json.loads(request_row["request_data"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        request_data = {}
    cur.execute("""
        UPDATE organization_requests
        SET status = 'Rejected', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
        WHERE id = %s AND status = 'Pending'
    """, (g.current_user["id"], "Request rejected", request_id))
    if cur.rowcount:
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES (%s, NULL, %s, %s, %s, %s)
        """, (
            request_row["entity_type"], request_data.get("name", "-"),
            f"Rejected/{request_data.get('operation', 'Create')}",
            f"{request_row['entity_type']} request rejected for {request_row['requested_by_name']}.",
            g.current_user["id"],
        ))
        conn.commit()
        flash("Organization request rejected.", "success")
    else:
        conn.rollback()
        flash("That organization request has already been reviewed.", "warning")
    cur.close()
    conn.close()
    return redirect(url_for("equipment_requests"))


@app.route("/categories/<int:category_id>/edit", methods=["POST"])
@admin_required("categories", allow_staff=True)
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
        if is_staff_role(g.current_user["role_name"]):
            queued = _queue_organization_request("Category", {
                "operation": "Update",
                "entity_id": category_id,
                "name": name,
                "description": description,
            })
            conn.rollback()
            flash(
                "Category update request submitted for administrative approval." if queued
                else "A matching category update request is already pending.",
                "success" if queued else "warning",
            )
            return redirect(url_for("categories"))
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
@admin_required("categories", allow_staff=True)
def delete_category(category_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM categories WHERE id = %s", (category_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Category not found.")
        if is_staff_role(g.current_user["role_name"]):
            queued = _queue_organization_request("Category", {
                "operation": "Delete",
                "entity_id": category_id,
                "name": current["name"],
                "description": None,
            })
            conn.rollback()
            flash(
                "Category deletion request submitted for administrative approval." if queued
                else "A category deletion request is already pending.",
                "success" if queued else "warning",
            )
            return redirect(url_for("categories"))
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
@admin_required("offices", allow_staff=True)
def add_office():
    try:
        name = clean_text(request.form.get("name"), "Office name", 150)
        description = optional_text(request.form.get("description"), 255)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("offices"))

    if is_staff_role(g.current_user["role_name"]):
        queued = _queue_organization_request("Office", {"name": name, "description": description})
        flash(
            "Office request submitted for administrative approval." if queued
            else "This office request is already pending.",
            "success" if queued else "warning",
        )
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
@admin_required("offices", allow_staff=True)
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
        if is_staff_role(g.current_user["role_name"]):
            queued = _queue_organization_request("Office", {
                "operation": "Update",
                "entity_id": office_id,
                "name": name,
                "description": description,
            })
            conn.rollback()
            flash(
                "Office update request submitted for administrative approval." if queued
                else "A matching office update request is already pending.",
                "success" if queued else "warning",
            )
            return redirect(url_for("offices"))
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
@admin_required("offices", allow_staff=True)
def delete_office(office_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT name FROM offices WHERE id = %s", (office_id,))
        current = cur.fetchone()
        if not current:
            raise ValueError("Office not found.")
        if is_staff_role(g.current_user["role_name"]):
            queued = _queue_organization_request("Office", {
                "operation": "Delete",
                "entity_id": office_id,
                "name": current["name"],
                "description": None,
            })
            conn.rollback()
            flash(
                "Office deletion request submitted for administrative approval." if queued
                else "An office deletion request is already pending.",
                "success" if queued else "warning",
            )
            return redirect(url_for("offices"))
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
            e.id,
            e.asset_code,
            e.name,
            e.created_by,
            a.accountable_user_id
        FROM equipment e
        LEFT JOIN accountability a
            ON a.equipment_id = e.id AND a.is_current = 1
        WHERE e.id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not _staff_can_view_equipment(equipment):
        equipment = None

    cur.close()
    conn.close()

    if not equipment:

        return jsonify({
            "error": "Equipment not found"
        }), 404

    return render_template("equipment_qr.html", equipment=equipment)


@app.route("/equipment/<int:item_id>/qr/image")
@login_required
def equipment_qr_image(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT e.id, e.asset_code, e.created_by, a.accountable_user_id
        FROM equipment e
        LEFT JOIN accountability a
            ON a.equipment_id = e.id AND a.is_current = 1
        WHERE e.id = %s
    """, (item_id,))
    equipment = cur.fetchone()
    if not _staff_can_view_equipment(equipment):
        equipment = None
    cur.close()
    conn.close()

    if not equipment:
        return jsonify({"error": "Equipment not found"}), 404

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(equipment["asset_code"])
    qr.make(fit=True)

    buffer = BytesIO()
    qr.make_image().save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="image/png",
        download_name=f"{equipment['asset_code']}.png"
    )


@app.route("/equipment/<int:item_id>/qr/print")
@login_required
def equipment_qr_print(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT
            e.id,
            e.asset_code,
            e.name,
            e.serial_number,
            e.status,
            e.created_by,
            a.person_name AS accountable_person,
            a.accountable_user_id,
            a.assigned_at AS accountable_assigned_at,
            ao.name AS accountable_office_name
        FROM equipment e
        LEFT JOIN accountability a
            ON e.id = a.equipment_id
            AND a.is_current = 1
        LEFT JOIN offices ao
            ON a.office_id = ao.id
        WHERE e.id = %s
    """, (item_id,))

    equipment = cur.fetchone()
    if not _staff_can_view_equipment(equipment):
        equipment = None
    cur.close()
    conn.close()

    if not equipment:
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    return render_template("equipment_qr_print.html", equipment=equipment)


# ============================================================
# REMOTE PHONE SCANNER
# ============================================================

def _remote_scanner_session(token):
    remote = _remote_scanner_sessions.get(token)
    if not remote or remote["expires_at"] <= datetime.now().timestamp():
        _remote_scanner_sessions.pop(token, None)
        return None
    return remote


def _remote_scanner_url(token):
    path = url_for("remote_scanner", token=token)
    public_base_url = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/")

    # Do not send phone users to the example domain when the deployment has
    # not been given a real custom URL yet. ProxyFix makes the fallback use
    # Railway's HTTPS host and scheme.
    placeholder_urls = {
        "https://your-domain.com",
        "http://your-domain.com",
    }
    if public_base_url and public_base_url not in placeholder_urls:
        return f"{public_base_url}{path}"
    return url_for("remote_scanner", token=token, _external=True)


@app.route("/qr-scanner/remote/start")
@login_required
def start_remote_scanner():
    now = datetime.now().timestamp()
    for token, remote in list(_remote_scanner_sessions.items()):
        if remote["expires_at"] <= now:
            _remote_scanner_sessions.pop(token, None)

    token = secrets.token_urlsafe(24)
    _remote_scanner_sessions[token] = {
        "user_id": session["user_id"],
        "expires_at": now + REMOTE_SCANNER_TTL_SECONDS,
        "asset_code": None,
    }
    remote_url = _remote_scanner_url(token)
    return jsonify({"token": token, "url": remote_url, "expires_in": REMOTE_SCANNER_TTL_SECONDS})


@app.route("/qr-scanner/remote/<token>")
def remote_scanner(token):
    if not _remote_scanner_session(token):
        return render_template(
            "error.html",
            code=410,
            title="Scanner session expired",
            message="Start a new phone scanner session from the computer.",
        ), 410
    return render_template("remote_scanner.html", token=token)


@app.route("/qr-scanner/remote/<token>/qr")
def remote_scanner_qr(token):
    if not _remote_scanner_session(token):
        return jsonify({"error": "Scanner session expired"}), 410

    remote_url = _remote_scanner_url(token)
    qr = qrcode.QRCode(version=1, box_size=8, border=4)
    qr.add_data(remote_url)
    qr.make(fit=True)
    buffer = BytesIO()
    qr.make_image().save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@app.route("/qr-scanner/remote/<token>/scan", methods=["POST"])
def receive_remote_scan(token):
    remote = _remote_scanner_session(token)
    if not remote:
        return jsonify({"error": "Scanner session expired"}), 410

    payload = request.get_json(silent=True) or {}
    asset_code = str(payload.get("asset_code", "")).strip()
    if not asset_code or len(asset_code) > 150:
        return jsonify({"error": "Invalid equipment QR value"}), 400

    remote["asset_code"] = asset_code
    return jsonify({"ok": True})


@app.route("/qr-scanner/remote/<token>/result")
@login_required
def remote_scanner_result(token):
    remote = _remote_scanner_session(token)
    if not remote or remote["user_id"] != session["user_id"]:
        return jsonify({"error": "Scanner session expired"}), 410

    asset_code = remote["asset_code"]
    remote["asset_code"] = None
    return jsonify({"asset_code": asset_code})


# ============================================================
# QR SCANNER PAGE
# Worker can USE
# ============================================================

@app.route("/qr-scanner")
@login_required
def qr_scanner():

    return render_template(
        "qr_scanner.html",
        can_edit_equipment=can_perform_action(
            g.current_user["role_name"],
            "edit",
            g.current_user.get("feature_permissions")
        )
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
            u.full_name AS created_by_name,
            a.person_name AS accountable_person,
            a.person_position AS accountable_position,
            a.office_id AS accountable_office_id,
            ao.name AS accountable_office_name,
            a.assigned_at AS accountable_assigned_at
        FROM equipment e

        LEFT JOIN categories c
            ON e.category_id = c.id

        LEFT JOIN offices o
            ON e.office_id = o.id

        LEFT JOIN users u
            ON e.created_by = u.id

        LEFT JOIN accountability a
            ON e.id = a.equipment_id
            AND a.is_current = 1

        LEFT JOIN offices ao
            ON a.office_id = ao.id

        WHERE e.asset_code = %s
    """, (asset_code,))

    row = cur.fetchone()

    if row:
        row["can_edit"] = (
            can_perform_action(
                g.current_user["role_name"],
                "edit",
                g.current_user.get("feature_permissions"),
            )
            or (
                is_staff_role(g.current_user["role_name"])
                and row.get("created_by") == g.current_user["id"]
                and row.get("approval_status") in {"Pending", "Approved"}
            )
        )
        if row.get("acquisition_date"):
            row["acquisition_date"] = row["acquisition_date"].isoformat()
        if row.get("accountable_assigned_at"):
            row["accountable_assigned_at"] = row["accountable_assigned_at"].strftime("%Y-%m-%d %H:%M:%S")

    cur.close()
    conn.close()

    return jsonify(
        row or {
            "error": "Equipment not found"
        }
    )


# ============================================================



