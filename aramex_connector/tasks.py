# Copyright (c) 2025, Printechs and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime, today
from aramex_connector.api import call_aramex_tracking_api

FORWARD_CODE_MAP = {
    "SH":  "Shipped",
    "OD":  "Shipped",
    "IT":  "Shipped",
    "DL":  "Delivered",
    "OK":  "Delivered",
    "CL":  "Cancelled",
    "CA":  "Cancelled",
    "RN":  "Cancelled",
    "RTO": "Cancelled",
    "SC":  "Scheduled",
    "PU":  "Scheduled",
}

RETURN_CODE_MAP = {
    "SC":  "Scheduled",
    "PU":  "Delivered",
    "SH":  "Shipped",
    "IT":  "Shipped",
    "DL":  "Delivered",
    "OK":  "Delivered",
    "CL":  "Cancelled",
    "CA":  "Cancelled",
    "RN":  "Cancelled",
}

TERMINAL_STATUSES = {"Delivered", "Cancelled"}
SKIP_STATUSES     = {"Draft"}
BATCH_SIZE        = 20


@frappe.whitelist()
def debug_tracker():
    """
    Diagnostic method — call from browser:
    /api/method/aramex_connector.tasks.debug_tracker
    Returns a full diagnostic log as plain text.
    """
    lines = []
    log = lines.append

    log("=== ARAMEX TRACKER DIAGNOSTIC ===")
    log(f"Time: {now_datetime()}")

    # 1. Doctype check
    log_exists = frappe.db.exists("DocType", "Aramex Tracking Log")
    log(f"Aramex Tracking Log doctype exists: {log_exists}")

    # 2. Shipments query
    try:
        all_submitted = frappe.get_all(
            "Aramex Shipment",
            filters={"docstatus": 1},
            fields=["name", "awb_number", "shipment_status"],
            limit=5
        )
        log(f"Total submitted shipments (sample of 5): {len(all_submitted)}")
        for s in all_submitted:
            log(f"  {s['name']} | AWB: {s['awb_number']} | Status: {s['shipment_status']}")
    except Exception as e:
        log(f"ERROR querying submitted shipments: {e}")

    try:
        shipments = frappe.get_all(
            "Aramex Shipment",
            filters={
                "docstatus": 1,
                "awb_number": ["!=", ""],
                "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
            },
            fields=["name", "awb_number", "shipment_status", "is_return"],
        )
        log(f"Active shipments to poll: {len(shipments)}")
        for s in shipments[:10]:
            log(f"  {s['name']} | AWB: {s['awb_number']} | Status: {s['shipment_status']} | Return: {bool(s['is_return'])}")
        if len(shipments) > 10:
            log(f"  ... and {len(shipments)-10} more")
    except Exception as e:
        log(f"ERROR querying active shipments: {e}")
        return "\n".join(lines)

    if not shipments:
        log("NO ACTIVE SHIPMENTS FOUND — checking why...")
        status_counts = frappe.db.sql("""
            SELECT shipment_status, docstatus, COUNT(*) as cnt
            FROM `tabAramex Shipment`
            GROUP BY shipment_status, docstatus
            ORDER BY cnt DESC
        """, as_dict=True)
        for row in status_counts:
            log(f"  status={row.shipment_status} | docstatus={row.docstatus} | count={row.cnt}")
        return "\n".join(lines)

    # 3. Test API call with first AWB only
    test_awb = shipments[0]["awb_number"]
    log(f"\nTesting API with first AWB: {test_awb}")
    try:
        result = call_aramex_tracking_api([test_awb], get_last_update_only=True)
        log(f"API has_errors: {result.get('has_errors')}")
        log(f"API notifications: {result.get('notifications')}")
        tracking = result.get("tracking_results", {})
        log(f"Tracking result AWBs returned: {list(tracking.keys())}")
        if tracking:
            for awb, updates in tracking.items():
                log(f"  AWB {awb}: {len(updates)} update(s)")
                if updates:
                    u = updates[0]
                    log(f"    code={u.get('update_code')} | desc={u.get('update_description')} | location={u.get('update_location')} | time={u.get('update_datetime')}")
        else:
            log("  No tracking data returned for this AWB")
            log(f"  Full API result: {result}")
    except Exception as e:
        log(f"ERROR calling Aramex API: {e}")
        import traceback
        log(traceback.format_exc())

    log("\n=== END DIAGNOSTIC ===")
    return "\n".join(lines)


def update_aramex_shipment_statuses():
    """Hourly scheduled task."""

    log_doctype_exists = frappe.db.exists("DocType", "Aramex Tracking Log")

    log = None
    if log_doctype_exists:
        log = frappe.new_doc("Aramex Tracking Log")
        log.executed_at     = now_datetime()
        log.total_checked   = 0
        log.total_updated   = 0
        log.total_delivered = 0
        log.total_cancelled = 0
        log.status          = "Success"

    shipments = frappe.get_all(
        "Aramex Shipment",
        filters={
            "docstatus": 1,
            "awb_number": ["!=", ""],
            "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
        },
        fields=["name", "awb_number", "shipment_status", "is_return"],
    )

    if not shipments:
        if log:
            log.status = "No Active Shipments"
            log.insert(ignore_permissions=True)
            frappe.db.commit()
        return

    if log:
        log.total_checked = len(shipments)

    awb_to_shipment = {s["awb_number"]: s for s in shipments if s["awb_number"]}
    awb_list = list(awb_to_shipment.keys())
    api_failed = False

    for batch_start in range(0, len(awb_list), BATCH_SIZE):
        batch = awb_list[batch_start: batch_start + BATCH_SIZE]
        try:
            result = call_aramex_tracking_api(batch, get_last_update_only=True)
        except Exception:
            api_failed = True
            err = frappe.get_traceback()
            frappe.log_error(title="Aramex Tracker — API call failed", message=err)
            if log:
                log.append("results", {"awb_number": str(batch), "remarks": f"API ERROR: {err[:300]}"})
            continue

        if result.get("has_errors") or not result.get("tracking_results"):
            msg = "; ".join(
                n.get("message", "") for n in (result.get("notifications") or [])
            ) or "No tracking results returned"
            frappe.log_error(title="Aramex Tracker — No results", message=msg)
            if log:
                for awb in batch:
                    sm = awb_to_shipment.get(awb)
                    if sm:
                        log.append("results", {
                            "shipment": sm["name"], "awb_number": awb,
                            "is_return": sm["is_return"],
                            "previous_status": sm["shipment_status"],
                            "remarks": f"No data from API: {msg}"
                        })
            continue

        for awb, updates in result["tracking_results"].items():
            sm = awb_to_shipment.get(awb)
            if not sm or not updates:
                continue

            row = _process_and_build_row(sm, updates)
            if log:
                log.append("results", row)
                if row.get("new_status") and row["new_status"] != row["previous_status"]:
                    log.total_updated += 1
                if row.get("new_status") == "Delivered":
                    log.total_delivered += 1
                if row.get("new_status") == "Cancelled":
                    log.total_cancelled += 1

    if api_failed and log:
        log.status = "Partial" if log.total_updated > 0 else "Failed"

    if log:
        try:
            log.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.log_error(title="Aramex Tracker — Log save failed", message=frappe.get_traceback())


def _process_and_build_row(shipment_meta, updates):
    doc_name  = shipment_meta["name"]
    is_return = bool(shipment_meta.get("is_return"))
    code_map  = RETURN_CODE_MAP if is_return else FORWARD_CODE_MAP

    latest      = updates[0]
    update_code = (latest.get("update_code") or "").upper()
    description = latest.get("update_description") or ""
    comments    = latest.get("comments") or ""
    location    = latest.get("update_location") or ""
    new_status  = code_map.get(update_code)
    prev_status = shipment_meta["shipment_status"]

    row = {
        "shipment":           doc_name,
        "awb_number":         shipment_meta["awb_number"],
        "is_return":          is_return,
        "previous_status":    prev_status,
        "new_status":         new_status or "",
        "update_code":        update_code,
        "update_description": description + (f" ({location})" if location else ""),
        "sales_orders_updated": "",
        "cancellation_reason":  "",
        "remarks":            ""
    }

    update_values = {"custom_last_checked": now_datetime()}

    if not new_status:
        row["remarks"] = f"Unknown Aramex code: '{update_code}'"
        frappe.db.set_value("Aramex Shipment", doc_name, update_values)
        return row

    if prev_status == new_status and new_status not in TERMINAL_STATUSES:
        row["remarks"] = "No status change"
        frappe.db.set_value("Aramex Shipment", doc_name, update_values)
        return row

    update_values["shipment_status"] = new_status

    if new_status == "Cancelled":
        reason_parts = [p for p in [update_code, description, comments] if p]
        cancel_reason = " | ".join(reason_parts) if reason_parts else "Cancelled by carrier"
        update_values["cancellation_reason"] = cancel_reason
        row["cancellation_reason"] = cancel_reason

    frappe.db.set_value("Aramex Shipment", doc_name, update_values)

    updated_sos = []
    if new_status == "Delivered":
        updated_sos = _handle_delivered(doc_name, is_return)
    elif new_status == "Cancelled":
        updated_sos = _handle_cancelled(doc_name, is_return)

    row["sales_orders_updated"] = ", ".join(updated_sos) if updated_sos else ""
    return row


def _get_linked_sales_orders(doc_name):
    sales_orders = set()
    rows = frappe.get_all(
        "Aramex Shipment Delivery Note",
        filters={"parent": doc_name},
        fields=["delivery_note"],
    )
    for row in rows:
        if not row.delivery_note:
            continue
        dn_items = frappe.get_all(
            "Delivery Note Item",
            filters={"parent": row.delivery_note, "against_sales_order": ["!=", ""]},
            fields=["against_sales_order"],
        )
        for item in dn_items:
            if item.against_sales_order:
                sales_orders.add(item.against_sales_order)
    return sales_orders


def _handle_delivered(doc_name, is_return):
    updated = []
    for so_name in _get_linked_sales_orders(doc_name):
        try:
            if is_return:
                frappe.db.set_value("Sales Order", so_name, {"custom_return_status": "Return Collected"})
            else:
                frappe.db.set_value("Sales Order", so_name,
                    {"delivery_date": today(), "custom_ecommerce_status": "Delivered"})
            updated.append(so_name)
        except Exception:
            frappe.log_error(
                title=f"Aramex Tracker — SO Delivered update failed [{so_name}]",
                message=frappe.get_traceback(),
            )
    return updated


def _handle_cancelled(doc_name, is_return):
    updated = []
    for so_name in _get_linked_sales_orders(doc_name):
        try:
            if is_return:
                frappe.db.set_value("Sales Order", so_name, {"custom_return_status": "Return Cancelled"})
            else:
                frappe.db.set_value("Sales Order", so_name, {"custom_ecommerce_status": "Cancelled"})
            updated.append(so_name)
        except Exception:
            frappe.log_error(
                title=f"Aramex Tracker — SO Cancelled update failed [{so_name}]",
                message=frappe.get_traceback(),
            )
    return updated