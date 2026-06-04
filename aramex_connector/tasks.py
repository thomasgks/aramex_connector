# Copyright (c) 2025, Printechs and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime, today
from aramex_connector.api import call_aramex_tracking_api

# Aramex returns full codes like SH369, DL001 — match by prefix
CODE_PREFIX_MAP = [
    ("DL",  "Delivered"),
    ("OK",  "Delivered"),
    ("RTO", "Cancelled"),
    ("CL",  "Cancelled"),
    ("CA",  "Cancelled"),
    ("RN",  "Cancelled"),
    ("SH",  "Shipped"),
    ("SS",  "Shipped"),
    ("OD",  "Shipped"),
    ("IT",  "Shipped"),
    ("OF",  "Shipped"),
    ("SC",  "Scheduled"),
    ("PU",  "Scheduled"),
]

RETURN_PREFIX_MAP = [
    ("DL",  "Delivered"),
    ("OK",  "Delivered"),
    ("PU",  "Delivered"),   # Picked Up from customer = terminal for returns
    ("RTO", "Cancelled"),
    ("CL",  "Cancelled"),
    ("CA",  "Cancelled"),
    ("RN",  "Cancelled"),
    ("SH",  "Shipped"),
    ("SS",  "Shipped"),
    ("IT",  "Shipped"),
    ("SC",  "Scheduled"),
    ("OF",  "Shipped"),
]

TERMINAL_STATUSES = {"Delivered", "Cancelled"}
SKIP_STATUSES     = {"Draft"}
BATCH_SIZE        = 20

# Max shipments to process per hourly run
# 100 shipments = 5 API batches, well within the 5 min timeout
# All 1703 will be cycled through in ~17 hourly runs (~17 hours)
PER_RUN_LIMIT     = 100


def _map_code(update_code, is_return):
    """Match Aramex full code (e.g. SH369) by prefix."""
    code = update_code.upper().strip()
    prefix_map = RETURN_PREFIX_MAP if is_return else CODE_PREFIX_MAP
    for prefix, status in prefix_map:
        if code.startswith(prefix):
            return status
    return None


@frappe.whitelist()
def debug_tracker():
    """
    Diagnostic — open in browser:
    /api/method/aramex_connector.tasks.debug_tracker
    """
    lines = []
    out = lines.append

    out("=== ARAMEX TRACKER DIAGNOSTIC ===")
    out(f"Time: {now_datetime()}")

    log_exists = frappe.db.exists("DocType", "Aramex Tracking Log")
    out(f"Aramex Tracking Log doctype exists: {log_exists}")

    shipments = frappe.get_all(
        "Aramex Shipment",
        filters={
            "docstatus": 1,
            "awb_number": ["!=", ""],
            "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
        },
        fields=["name", "awb_number", "shipment_status", "is_return", "custom_last_checked"],
        order_by="custom_last_checked asc",
        limit=PER_RUN_LIMIT,
    )
    out(f"Active shipments to poll (this run): {len(shipments)} of {PER_RUN_LIMIT} max")

    if not shipments:
        out("Nothing to poll.")
        return "\n".join(lines)

    test_awbs = [s["awb_number"] for s in shipments[:3]]
    out(f"\nTesting API with AWBs: {test_awbs}")

    try:
        result = call_aramex_tracking_api(test_awbs, get_last_update_only=True)
        out(f"API has_errors: {result.get('has_errors')}")
        tracking = result.get("tracking_results", {})
        out(f"AWBs returned: {list(tracking.keys())}")

        for awb, updates in tracking.items():
            sm = next((s for s in shipments if s["awb_number"] == awb), None)
            is_return = bool(sm.get("is_return")) if sm else False
            out(f"\n  AWB {awb}:")
            if updates:
                u = updates[0]
                code = (u.get("update_code") or "").upper()
                mapped = _map_code(code, is_return)
                out(f"    raw_code   = {code}")
                out(f"    description= {u.get('update_description')}")
                out(f"    location   = {u.get('update_location')}")
                out(f"    mapped_to  = {mapped or 'UNKNOWN — not in map'}")
    except Exception:
        out(f"API ERROR: {frappe.get_traceback()}")

    out("\n=== END DIAGNOSTIC ===")
    return "\n".join(lines)


def update_aramex_shipment_statuses():
    """
    Hourly scheduled task.
    Processes up to PER_RUN_LIMIT shipments per run, ordered by
    oldest-checked-first so all shipments are cycled through over time.
    """
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

    # Fetch oldest-checked-first so every shipment gets polled over time
    shipments = frappe.get_all(
        "Aramex Shipment",
        filters={
            "docstatus": 1,
            "awb_number": ["!=", ""],
            "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
        },
        fields=["name", "awb_number", "shipment_status", "is_return", "custom_last_checked"],
        order_by="custom_last_checked asc",
        limit=PER_RUN_LIMIT,
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
            # Still stamp last_checked so these AWBs move to back of queue
            for awb in batch:
                sm = awb_to_shipment.get(awb)
                if sm:
                    frappe.db.set_value("Aramex Shipment", sm["name"],
                        {"custom_last_checked": now_datetime()})
                    if log:
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

        # Stamp last_checked for AWBs not returned by API (no change)
        returned_awbs = set(result["tracking_results"].keys())
        for awb in batch:
            if awb not in returned_awbs:
                sm = awb_to_shipment.get(awb)
                if sm:
                    frappe.db.set_value("Aramex Shipment", sm["name"],
                        {"custom_last_checked": now_datetime()})

    if api_failed and log:
        log.status = "Partial" if log.total_updated > 0 else "Failed"

    if log:
        try:
            log.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.log_error(
                title="Aramex Tracker — Log save failed",
                message=frappe.get_traceback()
            )
            try:
                log.set("results", [])
                log.insert(ignore_permissions=True)
                frappe.db.commit()
            except Exception:
                frappe.log_error(
                    title="Aramex Tracker — Log save failed (no results)",
                    message=frappe.get_traceback()
                )


def _process_and_build_row(shipment_meta, updates):
    doc_name  = shipment_meta["name"]
    is_return = bool(shipment_meta.get("is_return"))

    latest      = updates[0]
    update_code = (latest.get("update_code") or "").upper()
    description = latest.get("update_description") or ""
    comments    = latest.get("comments") or ""
    location    = latest.get("update_location") or ""
    new_status  = _map_code(update_code, is_return)
    prev_status = shipment_meta["shipment_status"]

    row = {
        "shipment":             doc_name,
        "awb_number":           shipment_meta["awb_number"],
        "is_return":            is_return,
        "previous_status":      prev_status,
        "new_status":           new_status or "",
        "update_code":          update_code,
        "update_description":   description + (f" ({location})" if location else ""),
        "sales_orders_updated": "",
        "cancellation_reason":  "",
        "remarks":              ""
    }

    update_values = {"custom_last_checked": now_datetime()}

    if not new_status:
        row["remarks"] = f"Unknown code: '{update_code}'"
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
                frappe.db.set_value("Sales Order", so_name,
                    {"custom_return_status": "Return Collected"})
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
                frappe.db.set_value("Sales Order", so_name,
                    {"custom_return_status": "Return Cancelled"})
            else:
                frappe.db.set_value("Sales Order", so_name,
                    {"custom_ecommerce_status": "Cancelled"})
            updated.append(so_name)
        except Exception:
            frappe.log_error(
                title=f"Aramex Tracker — SO Cancelled update failed [{so_name}]",
                message=frappe.get_traceback(),
            )
    return updated