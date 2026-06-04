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


def update_aramex_shipment_statuses():
    """Hourly task — polls Aramex, updates shipments, writes a Tracking Log."""

    log = frappe.new_doc("Aramex Tracking Log")
    log.executed_at     = now_datetime()
    log.total_checked   = 0
    log.total_updated   = 0
    log.total_delivered = 0
    log.total_cancelled = 0
    log.status          = "Success"

    # ── 1. Fetch active shipments ─────────────────────────────────────────────
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
        log.status = "No Active Shipments"
        log.insert(ignore_permissions=True)
        frappe.db.commit()
        return

    log.total_checked = len(shipments)
    awb_to_shipment   = {s["awb_number"]: s for s in shipments if s["awb_number"]}
    awb_list          = list(awb_to_shipment.keys())

    # ── 2. Poll Aramex in batches ─────────────────────────────────────────────
    api_failed = False
    for batch_start in range(0, len(awb_list), BATCH_SIZE):
        batch = awb_list[batch_start: batch_start + BATCH_SIZE]
        try:
            result = call_aramex_tracking_api(batch, get_last_update_only=True)
        except Exception:
            api_failed = True
            err = frappe.get_traceback()
            frappe.log_error(title="Aramex Tracker — API call failed", message=err)
            log.append("results", {
                "shipment":     "",
                "awb_number":   str(batch),
                "remarks":      f"API ERROR: {err[:300]}"
            })
            continue

        if result.get("has_errors") or not result.get("tracking_results"):
            notifications = result.get("notifications") or []
            msg = "; ".join(n.get("message", "") for n in notifications) or "No tracking results returned"
            for awb in batch:
                sm = awb_to_shipment.get(awb)
                if sm:
                    log.append("results", {
                        "shipment":          sm["name"],
                        "awb_number":        awb,
                        "is_return":         sm["is_return"],
                        "previous_status":   sm["shipment_status"],
                        "new_status":        "",
                        "remarks":           f"API returned no data: {msg}"
                    })
            continue

        # ── 3. Process each AWB result ────────────────────────────────────────
        for awb, updates in result["tracking_results"].items():
            sm = awb_to_shipment.get(awb)
            if not sm or not updates:
                log.append("results", {
                    "awb_number": awb,
                    "remarks":    "AWB not in active list or empty updates"
                })
                continue

            row = _process_and_build_row(sm, updates, log)
            log.append("results", row)

            if row.get("new_status") and row["new_status"] != row["previous_status"]:
                log.total_updated += 1
            if row.get("new_status") == "Delivered":
                log.total_delivered += 1
            if row.get("new_status") == "Cancelled":
                log.total_cancelled += 1

    if api_failed:
        log.status = "Partial" if log.total_updated > 0 else "Failed"

    # ── 4. Save log ───────────────────────────────────────────────────────────
    log.insert(ignore_permissions=True)
    frappe.db.commit()


def _process_and_build_row(shipment_meta, updates, log):
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
        "shipment":          doc_name,
        "awb_number":        shipment_meta["awb_number"],
        "is_return":         is_return,
        "previous_status":   prev_status,
        "new_status":        new_status or "",
        "update_code":       update_code,
        "update_description": f"{description}" + (f" ({location})" if location else ""),
        "sales_orders_updated": "",
        "cancellation_reason":  "",
        "remarks":           ""
    }

    # Always stamp last_checked
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

    # Side effects on Sales Orders
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