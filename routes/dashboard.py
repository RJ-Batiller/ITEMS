"""Dashboard route group."""

from app import *

# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    conn = db()
    cur = conn.cursor(dictionary=True)
    staff_scope = is_staff_role(g.current_user["role_name"])
    staff_id = g.current_user["id"]

    cur.execute("""
        SELECT status, COUNT(*) AS status_count
        FROM equipment
        WHERE created_by = %s
        GROUP BY status
    """ if staff_scope else """
        SELECT status, COUNT(*) AS status_count
        FROM equipment
        GROUP BY status
    """, (staff_id,) if staff_scope else ())
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
    all_equipment = sum(status_counts.values())

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
        WHERE (t.user_id = %s OR e.created_by = %s)
        ORDER BY t.created_at DESC
        LIMIT 8
    """ if staff_scope else """
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
    """, (staff_id, staff_id) if staff_scope else ())
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
        WHERE m.status = 'Open' AND m.started_by = %s
        ORDER BY m.started_at DESC
        LIMIT 5
    """ if staff_scope else """
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
    """, (staff_id,) if staff_scope else ())
    open_maintenance = cur.fetchall()

    cur.execute(f"""
        SELECT c.name, COUNT(e.id) AS equipment_count
        FROM categories c
        LEFT JOIN equipment e
            ON e.category_id = c.id
            AND e.status NOT IN ('Archived', 'Disposed')
            {"AND e.created_by = %s" if staff_scope else ""}
        GROUP BY c.id, c.name
        ORDER BY equipment_count DESC, c.name
        LIMIT 8
    """, (staff_id,) if staff_scope else ())
    category_breakdown = cur.fetchall()

    cur.execute(f"""
        SELECT o.name, COUNT(e.id) AS equipment_count
        FROM offices o
        LEFT JOIN equipment e
            ON e.office_id = o.id
            AND e.status NOT IN ('Archived', 'Disposed')
            {"AND e.created_by = %s" if staff_scope else ""}
        GROUP BY o.id, o.name
        ORDER BY equipment_count DESC, o.name
        LIMIT 8
    """, (staff_id,) if staff_scope else ())
    office_breakdown = cur.fetchall()

    cur.execute("""
        SELECT action, COUNT(*) AS action_count
        FROM transactions
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
          AND user_id = %s
        GROUP BY action
        ORDER BY action_count DESC, action
        LIMIT 6
    """ if staff_scope else """
        SELECT action, COUNT(*) AS action_count
        FROM transactions
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY action
        ORDER BY action_count DESC, action
        LIMIT 6
    """, (staff_id,) if staff_scope else ())
    activity_breakdown = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS completed_count
        FROM maintenance_records
        WHERE status = 'Completed'
          AND completed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
          AND completed_by = %s
    """ if staff_scope else """
        SELECT COUNT(*) AS completed_count
        FROM maintenance_records
        WHERE status = 'Completed'
          AND completed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
    """, (staff_id,) if staff_scope else ())
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
        all_equipment=all_equipment,
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
