"""Equipment catalog, maintenance, accountability, and QR routes."""

from app import *
from services.report_service import build_csv


def _staff_owns_equipment(equipment):
    return (
        is_staff_role(g.current_user["role_name"])
        and equipment
        and equipment.get("created_by") == g.current_user["id"]
    )


def _transaction_request_action(action_type):
    """Use the same action wording in Staff transaction history and requests."""
    return "Updated" if action_type == "Edit" else action_type


def _queue_staff_action(conn, cur, equipment, action_type, request_data):
    cur.execute("""
        SELECT id
        FROM equipment_action_requests
        WHERE equipment_id = %s
          AND requested_by = %s
          AND action_type = %s
          AND status = 'Pending'
        LIMIT 1
    """, (equipment["id"], g.current_user["id"], action_type))
    if cur.fetchone():
        return False

    cur.execute("""
        INSERT INTO equipment_action_requests
            (equipment_id, requested_by, action_type, request_data)
        VALUES (%s, %s, %s, %s)
    """, (
        equipment["id"],
        g.current_user["id"],
        action_type,
        json.dumps(request_data),
    ))
    cur.execute("""
        INSERT INTO transactions (equipment_id, user_id, action, details)
        VALUES (%s, %s, %s, %s)
    """, (
        equipment["id"],
        g.current_user["id"],
        f"Requested/{_transaction_request_action(action_type)}",
        f"Staff requested {action_type.lower()} approval",
    ))
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Equipment', %s, %s, %s, %s, %s)
    """, (
        equipment["id"], equipment["asset_code"], f"Requested/{_transaction_request_action(action_type)}",
        f"Staff requested {action_type.lower()} approval.",
        g.current_user["id"],
    ))
    conn.commit()
    return True

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
    staff_scope = is_staff_role(g.current_user["role_name"])

    sql = """
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            u.full_name AS created_by_name,
            a.person_name AS accountable_person,
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

    """

    filters = []
    params = []
    if staff_scope:
        filters.append("e.created_by = %s")
        params.append(g.current_user["id"])
    else:
        filters.append("e.approval_status = 'Approved'")

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
        content = build_csv([
            "Property Number", "Equipment", "Category", "Type", "Office",
            "Serial Number", "Status", "Created By", "Accountable Person", "Acquisition Date",
        ], [
            [
                csv_safe(row["asset_code"]),
                csv_safe(row["name"]),
                csv_safe(row["category_name"]),
                csv_safe(row["item_type"]),
                csv_safe(row["office_name"]),
                csv_safe(row["serial_number"]),
                csv_safe(row["status"]),
                csv_safe(row["created_by_name"]),
                csv_safe(row["accountable_person"]),
                csv_safe(row["acquisition_date"]),
            ] for row in rows
        ])
        response = make_response(content)
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=items-equipment-report.csv"
        return response

    equipment_scope = (
        "e.created_by = %s"
        if staff_scope
        else "e.approval_status = 'Approved'"
    )
    scope_params = [g.current_user["id"]] if staff_scope else []

    cur.execute(f"""
        SELECT
            c.id,
            c.name,
            COUNT(e.id) AS equipment_count
        FROM categories c
        LEFT JOIN equipment e
            ON e.category_id = c.id
            AND {equipment_scope}
        GROUP BY c.id, c.name
        ORDER BY c.name
    """, scope_params)

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

    cur.execute(f"""
        SELECT status, COUNT(*) AS status_count
        FROM equipment e
        WHERE {equipment_scope}
        GROUP BY status
    """, scope_params)
    status_counts = {
        row["status"]: row["status_count"]
        for row in cur.fetchall()
    }

    cur.execute(f"""
        SELECT item_type, COUNT(*) AS item_type_count
        FROM equipment e
        WHERE {equipment_scope}
        GROUP BY item_type
    """, scope_params)
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
    staff_scope = is_staff_role(g.current_user["role_name"])

    cur.execute("""
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            u.full_name AS created_by_name,
            a.person_name AS accountable_person,
            a.person_position AS accountable_position,
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

        WHERE e.id = %s
    """, (item_id,))

    equipment = cur.fetchone()
    if equipment and staff_scope and equipment.get("created_by") != g.current_user["id"]:
        equipment = None

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
        is_worker=staff_scope,
        is_staff=staff_scope,
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
@admin_required("maintenance", allow_staff=True)
def equipment_maintenance(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status, approval_status, created_by
        FROM equipment
        WHERE id = %s
    """, (item_id,))
    equipment = cur.fetchone()

    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    staff_action = is_staff_role(g.current_user["role_name"])
    if staff_action and (not _staff_owns_equipment(equipment) or equipment.get("approval_status") != "Approved"):
        cur.close()
        conn.close()
        flash("Staff can only request actions for their approved equipment.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    if equipment["status"] == "Disposed":
        cur.close()
        conn.close()
        flash("Disposed equipment can only be viewed or scanned.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

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

    if staff_action:
        requested_action = "Maintenance Complete" if maintenance_action == "complete" else "Maintenance Start"
        if maintenance_action not in {"start", "complete"} or not remarks:
            cur.close()
            conn.close()
            flash("Please provide valid maintenance details.", "danger")
            return redirect(url_for("equipment_maintenance", item_id=item_id))
        if maintenance_action == "start" and (open_record or (not staff_action and not can_start_maintenance(equipment["status"]))):
            cur.close()
            conn.close()
            flash("This equipment cannot start maintenance in its current state.", "danger")
            return redirect(url_for("equipment_maintenance", item_id=item_id))
        if maintenance_action == "complete" and not can_complete_maintenance(open_record):
            cur.close()
            conn.close()
            flash("There is no open maintenance record to complete.", "danger")
            return redirect(url_for("equipment_maintenance", item_id=item_id))
        queued = _queue_staff_action(conn, cur, equipment, requested_action, {
            "maintenance_action": maintenance_action,
            "remarks": remarks,
        })
        cur.close()
        conn.close()
        flash(
            "Maintenance request submitted for administrative approval." if queued
            else "A matching maintenance request is already pending.",
            "success" if queued else "warning",
        )
        return redirect(url_for("view_equipment", item_id=item_id))

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
            flash("Archived or disposed equipment cannot enter maintenance.", "danger")
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
# STAFF EQUIPMENT REQUESTS
# ============================================================

@app.route("/equipment/requests")
@admin_required("requests")
def equipment_requests():
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT e.*, c.name AS category_name, o.name AS office_name,
               u.full_name AS requested_by_name, u.username AS requested_by_username
        FROM equipment e
        LEFT JOIN categories c ON c.id = e.category_id
        LEFT JOIN offices o ON o.id = e.office_id
        JOIN users u ON u.id = e.created_by
        WHERE e.approval_status = 'Pending'
        ORDER BY e.created_at ASC, e.id ASC
    """)
    requests = cur.fetchall()
    cur.execute("""
        SELECT
            r.*,
            e.asset_code,
            e.name AS equipment_name,
            c.name AS category_name,
            o.name AS office_name,
            u.full_name AS requested_by_name
        FROM equipment_action_requests r
        JOIN equipment e ON e.id = r.equipment_id
        LEFT JOIN categories c ON c.id = e.category_id
        LEFT JOIN offices o ON o.id = e.office_id
        JOIN users u ON u.id = r.requested_by
        WHERE r.status = 'Pending'
        ORDER BY r.created_at ASC, r.id ASC
    """)
    action_requests = cur.fetchall()
    cur.execute("""
        SELECT
            r.id AS request_id,
            r.entity_type,
            r.request_data,
            r.created_at,
            u.full_name AS requested_by_name
        FROM organization_requests r
        JOIN users u ON u.id = r.requested_by
        WHERE r.status = 'Pending'
        ORDER BY r.created_at ASC, r.id ASC
    """)
    organization_requests = cur.fetchall()
    for request_row in organization_requests:
        try:
            request_data = json.loads(request_row["request_data"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            request_data = {}
        request_row["request_name"] = request_data.get("name", "-")
        request_row["request_description"] = request_data.get("description", "-")
    cur.close()
    conn.close()
    return render_template(
        "equipment_requests.html",
        requests=requests,
        action_requests=action_requests,
        organization_requests=organization_requests,
    )


@app.route("/equipment/my-requests")
@login_required
def my_equipment_requests():
    if not is_staff_role(g.current_user["role_name"]):
        return redirect(url_for("equipment_requests"))

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            e.id,
            e.asset_code,
            e.name,
            e.approval_status,
            e.review_details,
            e.created_at,
            c.name AS category_name,
            o.name AS office_name
        FROM equipment e
        LEFT JOIN categories c ON c.id = e.category_id
        LEFT JOIN offices o ON o.id = e.office_id
        WHERE e.created_by = %s
          AND e.approval_status = 'Pending'
        ORDER BY e.created_at DESC, e.id DESC
    """, (g.current_user["id"],))
    equipment_requests = cur.fetchall()

    cur.execute("""
        SELECT
            r.id AS request_id,
            r.equipment_id,
            r.action_type,
            r.request_data,
            r.status,
            r.review_details,
            r.created_at,
            e.asset_code,
            e.name AS equipment_name
        FROM equipment_action_requests r
        JOIN equipment e ON e.id = r.equipment_id
        WHERE r.requested_by = %s
          AND r.status = 'Pending'
        ORDER BY r.created_at DESC, r.id DESC
    """, (g.current_user["id"],))
    action_requests = cur.fetchall()

    cur.execute("""
        SELECT
            r.id AS request_id,
            r.entity_type,
            r.request_data,
            r.created_at
        FROM organization_requests r
        WHERE r.requested_by = %s
          AND r.status = 'Pending'
        ORDER BY r.created_at DESC, r.id DESC
    """, (g.current_user["id"],))
    organization_requests = cur.fetchall()
    cur.close()
    conn.close()

    for request_row in action_requests:
        try:
            request_row["request_data"] = json.loads(request_row["request_data"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            request_row["request_data"] = {}

    for request_row in organization_requests:
        try:
            request_data = json.loads(request_row["request_data"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            request_data = {}
        request_row["request_name"] = request_data.get("name", "-")
        request_row["request_description"] = request_data.get("description", "-")

    return render_template(
        "my_equipment_requests.html",
        equipment_requests=equipment_requests,
        action_requests=action_requests,
        organization_requests=organization_requests,
    )


@app.route("/equipment/requests/<int:item_id>/view")
@admin_required("requests")
def view_equipment_request(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            e.*,
            c.name AS category_name,
            o.name AS office_name,
            u.full_name AS requested_by_name,
            u.username AS requested_by_username
        FROM equipment e
        LEFT JOIN categories c ON c.id = e.category_id
        LEFT JOIN offices o ON o.id = e.office_id
        JOIN users u ON u.id = e.created_by
        WHERE e.id = %s AND e.approval_status = 'Pending'
    """, (item_id,))
    request_row = cur.fetchone()
    cur.close()
    conn.close()
    if not request_row:
        flash("Equipment request not found or already reviewed.", "warning")
        return redirect(url_for("equipment_requests"))
    return render_template(
        "equipment_request_view.html",
        request_kind="equipment",
        request_row=request_row,
    )


@app.route("/equipment/action-requests/<int:request_id>/view")
@admin_required("requests")
def view_equipment_action_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            r.id AS request_id,
            r.equipment_id,
            r.action_type,
            r.request_data,
            r.status AS request_status,
            r.created_at AS request_created_at,
            e.asset_code,
            e.name AS equipment_name,
            e.status AS equipment_status,
            c.name AS category_name,
            o.name AS office_name,
            u.full_name AS requested_by_name,
            u.username AS requested_by_username
        FROM equipment_action_requests r
        JOIN equipment e ON e.id = r.equipment_id
        LEFT JOIN categories c ON c.id = e.category_id
        LEFT JOIN offices o ON o.id = e.office_id
        JOIN users u ON u.id = r.requested_by
        WHERE r.id = %s AND r.status = 'Pending'
    """, (request_id,))
    request_row = cur.fetchone()
    cur.close()
    conn.close()
    if not request_row:
        flash("Equipment action request not found or already reviewed.", "warning")
        return redirect(url_for("equipment_requests"))
    try:
        request_row["request_data"] = json.loads(request_row["request_data"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        request_row["request_data"] = {}
    return render_template(
        "equipment_request_view.html",
        request_kind="action",
        request_row=request_row,
    )


@app.route("/equipment/requests/<int:item_id>/approve", methods=["POST"])
@admin_required("requests")
def approve_equipment_request(item_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id, asset_code, approval_status
        FROM equipment
        WHERE id = %s
    """, (item_id,))
    equipment = cur.fetchone()
    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment request not found.", "danger")
        return redirect(url_for("equipment_requests"))
    if equipment["approval_status"] != "Pending":
        cur.close()
        conn.close()
        flash("That equipment request has already been reviewed.", "warning")
        return redirect(url_for("equipment_requests"))

    cur.execute("""
        UPDATE equipment
        SET approval_status = 'Approved', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
        WHERE id = %s AND approval_status = 'Pending'
    """, (session["user_id"], "Request approved", item_id))
    cur.execute("""
        INSERT INTO transactions (equipment_id, user_id, action, details)
        VALUES (%s, %s, 'Approved/Create', %s)
    """, (item_id, session["user_id"], f"Staff equipment request approved for {equipment['asset_code']}"))
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Equipment', %s, %s, 'Approved/Create', %s, %s)
    """, (item_id, equipment["asset_code"], "Staff equipment request approved.", session["user_id"]))
    conn.commit()
    cur.close()
    conn.close()
    flash("Equipment request approved and added to the inventory.", "success")
    return redirect(url_for("equipment_requests"))


@app.route("/equipment/requests/<int:item_id>/reject", methods=["POST"])
@admin_required("requests")
def reject_equipment_request(item_id):
    review_details = optional_text(request.form.get("review_details"), 500)
    review_details = review_details or "Request rejected"
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT id, asset_code, approval_status
        FROM equipment
        WHERE id = %s
    """, (item_id,))
    equipment = cur.fetchone()
    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment request not found.", "danger")
        return redirect(url_for("equipment_requests"))
    if equipment["approval_status"] != "Pending":
        cur.close()
        conn.close()
        flash("That equipment request has already been reviewed.", "warning")
        return redirect(url_for("equipment_requests"))

    cur.execute("""
        UPDATE equipment
        SET approval_status = 'Rejected', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
        WHERE id = %s AND approval_status = 'Pending'
    """, (session["user_id"], review_details, item_id))
    cur.execute("""
        INSERT INTO transactions (equipment_id, user_id, action, details)
        VALUES (%s, %s, 'Rejected/Create', %s)
    """, (item_id, session["user_id"], review_details))
    cur.execute("""
        INSERT INTO organization_history
            (entity_type, entity_id, entity_name, action, details, user_id)
        VALUES ('Equipment', %s, %s, 'Rejected/Create', %s, %s)
    """, (item_id, equipment["asset_code"], review_details, session["user_id"]))
    conn.commit()
    cur.close()
    conn.close()
    flash("Equipment request rejected.", "success")
    return redirect(url_for("equipment_requests"))


@app.route("/equipment/action-requests/<int:request_id>/approve", methods=["POST"])
@admin_required("requests")
def approve_equipment_action_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT r.*, e.*
            FROM equipment_action_requests r
            JOIN equipment e ON e.id = r.equipment_id
            WHERE r.id = %s AND r.status = 'Pending'
        """, (request_id,))
        request_row = cur.fetchone()
        if not request_row:
            raise ValueError("That action request has already been reviewed or no longer exists.")

        data = json.loads(request_row["request_data"] or "{}")
        action_type = request_row["action_type"]
        item_id = request_row["equipment_id"]

        if action_type == "Edit":
            new_status = data.get("status") or request_row["status"]
            if not validate_status_change(request_row["status"], new_status):
                raise ValueError("That requested equipment status change is not allowed.")
            acquisition_date = parse_optional_date(data.get("acquisition_date"), "acquisition date")
            category_id = required_id(data.get("category_id"), "Category")
            office_id = required_id(data.get("office_id"), "Office")
            item_type = data.get("item_type", "").strip()
            if item_type not in EQUIPMENT_ITEM_TYPES:
                raise ValueError("The requested item type is invalid.")
            name = clean_text(data.get("name"), "Equipment name", 150)
            cur.execute("""
                UPDATE equipment
                SET name = %s, description = %s, category_id = %s, office_id = %s,
                    serial_number = %s, specifications = %s, acquisition_date = %s,
                    item_type = %s, status = %s
                WHERE id = %s AND approval_status = 'Approved'
            """, (
                name, optional_text(data.get("description"), 5000), category_id, office_id,
                optional_text(data.get("serial_number"), 150),
                optional_text(data.get("specifications"), 5000), acquisition_date,
                item_type, new_status, item_id,
            ))
            cur.execute("""
                SELECT id
                FROM accountability
                WHERE equipment_id = %s AND is_current = 1
                LIMIT 1
            """, (item_id,))
            current_accountable = cur.fetchone()
            if current_accountable and data.get("accountable_person"):
                assigned_at = datetime.fromisoformat(data["accountable_assigned_at"])
                cur.execute("""
                    UPDATE accountability
                    SET person_name = %s, person_position = %s, office_id = %s, assigned_at = %s
                    WHERE id = %s AND is_current = 1
                """, (
                    clean_text(data.get("accountable_person"), "Accountable person", 150),
                    clean_text(data.get("accountable_position"), "Accountable position", 150),
                    required_id(data.get("accountable_office_id"), "Accountable office"),
                    assigned_at.strftime("%Y-%m-%d %H:%M:%S"), current_accountable["id"],
                ))
            details = "Staff edit request approved"
        elif action_type == "Assign":
            assigned_at = datetime.fromisoformat(data["assigned_at"])
            cur.execute("""
                UPDATE accountability SET is_current = 0, released_at = COALESCE(released_at, NOW())
                WHERE equipment_id = %s AND is_current = 1
            """, (item_id,))
            cur.execute("""
                INSERT INTO accountability
                    (equipment_id, person_name, person_position, office_id, assigned_at, is_current)
                VALUES (%s, %s, %s, %s, %s, 1)
            """, (
                item_id, clean_text(data.get("person_name"), "Accountable person", 150),
                clean_text(data.get("person_position"), "Accountable position", 150),
                required_id(data.get("office_id"), "Office"),
                assigned_at.strftime("%Y-%m-%d %H:%M:%S"),
            ))
            cur.execute("UPDATE equipment SET status = 'Assigned' WHERE id = %s", (item_id,))
            details = "Staff assignment request approved"
        elif action_type in {"Maintenance Start", "Maintenance Complete"}:
            remarks = clean_text(data.get("remarks"), "Maintenance remarks", 5000)
            if action_type == "Maintenance Start":
                cur.execute("""
                    INSERT INTO maintenance_records (equipment_id, started_by, remarks)
                    VALUES (%s, %s, %s)
                """, (item_id, g.current_user["id"], remarks))
                cur.execute("UPDATE equipment SET status = 'Under Maintenance' WHERE id = %s", (item_id,))
            else:
                cur.execute("""
                    SELECT id FROM maintenance_records
                    WHERE equipment_id = %s AND status = 'Open'
                    ORDER BY started_at DESC LIMIT 1
                """, (item_id,))
                open_record = cur.fetchone()
                if not open_record:
                    raise ValueError("There is no open maintenance record to complete.")
                cur.execute("""
                    UPDATE maintenance_records
                    SET status = 'Completed', completed_by = %s, completed_at = NOW(), completion_remarks = %s
                    WHERE id = %s
                """, (g.current_user["id"], remarks, open_record["id"]))
                cur.execute("UPDATE equipment SET status = 'Available' WHERE id = %s", (item_id,))
            details = f"Staff {action_type.lower()} request approved"
        elif action_type == "Archive":
            cur.execute("UPDATE equipment SET status = 'Archived' WHERE id = %s AND status NOT IN ('Archived', 'Disposed', 'Under Maintenance')", (item_id,))
            cur.execute("""
                UPDATE accountability SET is_current = 0, released_at = COALESCE(released_at, NOW())
                WHERE equipment_id = %s AND is_current = 1
            """, (item_id,))
            cur.execute("""
                INSERT INTO archives (equipment_id, archived_by, reason)
                VALUES (%s, %s, %s)
            """, (item_id, g.current_user["id"], data.get("reason") or "Equipment moved to archive"))
            details = "Staff archive request approved"
        elif action_type == "Dispose":
            disposal_date = date.fromisoformat(data["disposal_date"])
            reason = clean_text(data.get("reason"), "Disposal reason", 5000)
            cur.execute("""
                UPDATE accountability SET is_current = 0, released_at = COALESCE(released_at, NOW())
                WHERE equipment_id = %s AND is_current = 1
            """, (item_id,))
            cur.execute("UPDATE equipment SET status = 'Disposed' WHERE id = %s", (item_id,))
            cur.execute("""
                INSERT INTO disposals (equipment_id, disposed_by, reason, disposal_date, reference_no)
                VALUES (%s, %s, %s, %s, %s)
            """, (item_id, g.current_user["id"], reason, disposal_date, data.get("reference_no") or None))
            details = "Staff disposal request approved"
        else:
            raise ValueError("Unknown equipment action request.")

        cur.execute("""
            UPDATE equipment_action_requests
            SET status = 'Approved', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
            WHERE id = %s AND status = 'Pending'
        """, (g.current_user["id"], details, request_id))
        cur.execute("""
            INSERT INTO transactions (equipment_id, user_id, action, details)
            VALUES (%s, %s, %s, %s)
        """, (item_id, g.current_user["id"], f"Approved/{_transaction_request_action(action_type)}", details))
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Equipment', %s, %s, %s, %s, %s)
        """, (item_id, request_row["asset_code"], f"Approved/{action_type}", details, g.current_user["id"]))
        conn.commit()
        flash("Equipment action request approved.", "success")
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        conn.rollback()
        flash(str(error), "danger")
    except Exception:
        conn.rollback()
        flash("The equipment action request could not be approved.", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for("equipment_requests"))


@app.route("/equipment/action-requests/<int:request_id>/reject", methods=["POST"])
@admin_required("requests")
def reject_equipment_action_request(request_id):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT r.equipment_id, e.asset_code
        FROM equipment_action_requests r
        JOIN equipment e ON e.id = r.equipment_id
        WHERE r.id = %s AND r.status = 'Pending'
    """, (request_id,))
    request_row = cur.fetchone()
    cur.execute("""
        UPDATE equipment_action_requests
        SET status = 'Rejected', reviewed_by = %s, reviewed_at = NOW(), review_details = %s
        WHERE id = %s AND status = 'Pending'
    """, (g.current_user["id"], "Action request rejected", request_id))
    if cur.rowcount:
        cur.execute("""
            INSERT INTO transactions (equipment_id, user_id, action, details)
            VALUES (%s, %s, %s, %s)
        """, (
            request_row["equipment_id"] if request_row else None,
            g.current_user["id"],
            f"Rejected/{_transaction_request_action(request_row['action_type'])}" if request_row else "Rejected/Action",
            "Staff equipment action request rejected",
        ))
        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Equipment', %s, %s, %s, %s, %s)
        """, (
            request_row["equipment_id"] if request_row else None,
            request_row["asset_code"] if request_row else "Equipment",
            f"Rejected/{_transaction_request_action(request_row['action_type'])}" if request_row else "Rejected/Action",
            "Staff equipment action request rejected.",
            g.current_user["id"],
        ))
        conn.commit()
        flash("Equipment action request rejected.", "success")
    else:
        conn.rollback()
        flash("That action request has already been reviewed.", "warning")
    cur.close()
    conn.close()
    return redirect(url_for("equipment_requests"))


# ADD EQUIPMENT
# Staff submissions require approval; Admin and Super Admin submissions are immediate.
# ============================================================

@app.route(
    "/equipment/add",
    methods=["GET", "POST"]
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
        approval_status = "Pending" if is_staff_role(g.current_user["role_name"]) else "Approved"
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
                status,
                created_by,
                approval_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            status,
            session["user_id"],
            approval_status
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
            VALUES (%s, %s, %s, %s)
        """, (
            equipment_id,
            session["user_id"],
            "Requested/Create" if approval_status == "Pending" else "Created",
        "Equipment submitted for Admin approval" if approval_status == "Pending" else "Equipment record created",
        ))

        cur.execute("""
            INSERT INTO organization_history
                (entity_type, entity_id, entity_name, action, details, user_id)
            VALUES ('Equipment', %s, %s, %s, %s, %s)
        """, (
            equipment_id, asset_code,
            "Requested" if approval_status == "Pending" else "Created",
            "Equipment submitted for Admin approval" if approval_status == "Pending" else "Equipment record created",
            session["user_id"],
        ))

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
        "Equipment submitted for Admin approval." if approval_status == "Pending" else "Equipment added successfully.",
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
@admin_required("edit", allow_staff=True)
def edit_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT e.*, u.full_name AS created_by_name
        FROM equipment e
        LEFT JOIN users u ON e.created_by = u.id
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

    if equipment["status"] == "Disposed":
        cur.close()
        conn.close()
        flash("Disposed equipment cannot be edited.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(equipment) or equipment.get("approval_status") not in {"Pending", "Approved"}:
            cur.close()
            conn.close()
            flash("Staff can only edit equipment they submitted.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))

    cur.execute("""
        SELECT a.person_name, a.person_position, a.office_id, a.assigned_at, o.name AS office_name
        FROM accountability a
        LEFT JOIN offices o ON o.id = a.office_id
        WHERE a.equipment_id = %s AND a.is_current = 1
        ORDER BY a.id DESC
        LIMIT 1
    """, (item_id,))
    accountable = cur.fetchone()

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
        offices=offices,
        accountable=accountable
    )


# ============================================================
# EDIT EQUIPMENT - SAVE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/edit/<int:item_id>",
    methods=["POST"]
)
@admin_required("edit", allow_staff=True)
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

    if old["status"] == "Disposed":
        cur.close()
        conn.close()
        flash("Disposed equipment cannot be edited.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(old) or old.get("approval_status") not in {"Pending", "Approved"}:
            cur.close()
            conn.close()
            flash("Staff can only edit equipment they submitted.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))
        if old.get("approval_status") == "Approved":
            request_data = request.form.to_dict()
            request_data.pop("csrf_token", None)
            if not _queue_staff_action(conn, cur, old, "Edit", request_data):
                cur.close()
                conn.close()
                flash("An edit request for this equipment is already pending.", "warning")
                return redirect(url_for("view_equipment", item_id=item_id))
            cur.close()
            conn.close()
            flash("Edit request submitted for administrative approval.", "success")
            return redirect(url_for("view_equipment", item_id=item_id))

    cur.execute("""
        SELECT id, person_name, person_position, office_id, assigned_at
        FROM accountability
        WHERE equipment_id = %s
          AND is_current = 1
        ORDER BY id DESC
        LIMIT 1
    """, (item_id,))
    current_accountable = cur.fetchone()

    try:
        # Keep the printed QR identity stable when other equipment details change.
        asset_code = old["asset_code"]
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

        accountability_update = None
        if current_accountable:
            accountability_person = clean_text(
                data.get("accountable_person"),
                "Accountable person",
                150
            )
            accountability_position = clean_text(
                data.get("accountable_position"),
                "Accountable position",
                150
            )
            accountability_office_id = required_id(
                data.get("accountable_office_id"),
                "Accountable office"
            )
            accountability_assigned_at = data.get("accountable_assigned_at", "").strip()
            if not accountability_assigned_at:
                raise ValueError("Assignment date and time is required.")
            try:
                accountability_assigned_at = datetime.fromisoformat(accountability_assigned_at)
            except ValueError:
                raise ValueError("Please provide a valid assignment date and time.")
            accountability_update = (
                accountability_person,
                accountability_position,
                accountability_office_id,
                accountability_assigned_at.strftime("%Y-%m-%d %H:%M:%S")
            )
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

    transaction_details = "Equipment information updated"
    if accountability_update:
        cur.execute("""
            UPDATE accountability
            SET person_name = %s,
                person_position = %s,
                office_id = %s,
                assigned_at = %s
            WHERE id = %s
              AND is_current = 1
        """, (*accountability_update, current_accountable["id"]))
        transaction_details = "Equipment information and accountability updated"

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
        transaction_details
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
    methods=["GET", "POST"]
)
@admin_required("archive", allow_staff=True)
def archive_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status, approval_status, created_by
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

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(equipment) or equipment.get("approval_status") != "Approved":
            cur.close()
            conn.close()
            flash("Staff can only request actions for their approved equipment.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))

    if request.method == "GET":
        cur.close()
        conn.close()
        return render_template(
            "equipment_archive.html",
            equipment=equipment,
            current_date=date.today().isoformat()
        )

    reason = request.form.get("reason", "").strip()
    if not reason:
        cur.close()
        conn.close()
        flash("An archive reason is required.", "danger")
        return redirect(url_for("archive_equipment", item_id=item_id))

    if equipment["status"] in {"Archived", "Disposed", "Under Maintenance"}:
        cur.close()
        conn.close()
        flash("Archived, disposed, or maintained equipment cannot be archived.", "danger")
        return redirect(url_for("equipment"))

    if is_staff_role(g.current_user["role_name"]):
        queued = _queue_staff_action(conn, cur, equipment, "Archive", {
            "reason": reason,
        })
        cur.close()
        conn.close()
        flash(
            "Archive request submitted for administrative approval." if queued
            else "An archive request is already pending.",
            "success" if queued else "warning",
        )
        return redirect(url_for("view_equipment", item_id=item_id))

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
        reason
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
@admin_required("dispose", allow_staff=True)
def dispose_equipment(item_id):

    conn = db()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT id, asset_code, name, status, approval_status, created_by
        FROM equipment
        WHERE id = %s
    """, (item_id,))

    equipment = cur.fetchone()

    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(equipment) or equipment.get("approval_status") != "Approved":
            cur.close()
            conn.close()
            flash("Staff can only request actions for their approved equipment.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))

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

    if is_staff_role(g.current_user["role_name"]):
        queued = _queue_staff_action(conn, cur, equipment, "Dispose", {
            "reason": reason,
            "disposal_date": disposal_date,
            "reference_no": reference_no,
        })
        cur.close()
        conn.close()
        flash(
            "Disposal request submitted for administrative approval." if queued
            else "A disposal request is already pending.",
            "success" if queued else "warning",
        )
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
@admin_required("assign", allow_staff=True)
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

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(equipment) or equipment.get("approval_status") != "Approved":
            cur.close()
            conn.close()
            flash("Staff can only request actions for their approved equipment.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))

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
        current_datetime=(
            accountable["assigned_at"].strftime("%Y-%m-%dT%H:%M")
            if accountable and accountable.get("assigned_at")
            else datetime.now().strftime("%Y-%m-%dT%H:%M")
        )
    )


# ============================================================
# ASSIGN EQUIPMENT - SAVE
# Worker CANNOT ACCESS
# ============================================================

@app.route(
    "/equipment/assign/<int:item_id>",
    methods=["POST"]
)
@admin_required("assign", allow_staff=True)
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
        assigned_at_value = datetime.fromisoformat(assigned_at)
    except ValueError:
        flash("Please provide a valid assignment date and time.", "danger")
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

    if equipment["status"] in {"Archived", "Disposed", "Under Maintenance"}:
        cur.close()
        conn.close()
        flash("Archived, disposed, or maintained equipment cannot be assigned.", "danger")
        return redirect(url_for("view_equipment", item_id=item_id))

    if is_staff_role(g.current_user["role_name"]):
        if not _staff_owns_equipment(equipment) or equipment.get("approval_status") != "Approved":
            cur.close()
            conn.close()
            flash("Staff can only request actions for their approved equipment.", "danger")
            return redirect(url_for("view_equipment", item_id=item_id))
        queued = _queue_staff_action(conn, cur, equipment, "Assign", {
            "person_name": person_name,
            "person_position": person_position,
            "office_id": office_id,
            "assigned_at": assigned_at,
        })
        cur.close()
        conn.close()
        flash(
            "Assignment request submitted for administrative approval." if queued
            else "An assignment request is already pending.",
            "success" if queued else "warning",
        )
        return redirect(url_for("view_equipment", item_id=item_id))

    cur.execute("""
        SELECT id
        FROM accountability
        WHERE equipment_id = %s AND is_current = 1
        LIMIT 1
    """, (item_id,))
    had_accountability = cur.fetchone() is not None

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
            assigned_at_value.strftime("%Y-%m-%d %H:%M:%S")
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
            %s,
            %s
        )
    """, (
        item_id,
        session["user_id"],
        "Re-Assigned" if had_accountability else "Assigned",
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

    cur.execute("""
        SELECT e.id, e.status, a.id AS accountability_id
        FROM equipment e
        LEFT JOIN accountability a
            ON a.equipment_id = e.id
            AND a.is_current = 1
        WHERE e.id = %s
    """, (item_id,))
    equipment = cur.fetchone()
    if not equipment:
        cur.close()
        conn.close()
        flash("Equipment not found.", "danger")
        return redirect(url_for("equipment"))
    if not equipment["accountability_id"]:
        cur.close()
        conn.close()
        flash("This equipment has no current accountability assignment.", "danger")
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
