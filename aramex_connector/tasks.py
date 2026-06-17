# Copyright (c) 2025, Printechs and contributors
# For license information, please see license.txt
#
# IMPORTANT FIX (2026-06-16):
# GetLastTrackingUpdateOnly=True from Aramex was found to return inconsistent
# results — e.g. code "SH005" paired with description "Delivered", and the
# actual most-recent delivered event being skipped entirely. We now ALWAYS
# fetch the FULL tracking history per AWB and scan ALL entries (not just the
# supposed "last" one) to determine status, prioritizing keyword detection in
# update_description (which matches what the Aramex portal displays) over the
# update_code (which has proven unreliable for this account/region).

import frappe
import json
from frappe.utils import now_datetime, today, add_days
from aramex_connector.api import call_aramex_tracking_api

TERMINAL_STATUSES = {"Delivered", "Cancelled"}
SKIP_STATUSES     = {"Draft"}
BATCH_SIZE        = 20
PER_RUN_LIMIT     = 100
TRACKING_DAYS_LIMIT = 90

# ── Description-based keyword detection (PRIMARY — most reliable) ────────────
# Checked against update_description, case-insensitive, in priority order.
# Returns are checked for "Picked Up From Shipper" differently (terminal for
# returns only), forward shipments check for "Delivered".

DELIVERED_KEYWORDS   = ["delivered", "delivery completed"]
CANCELLED_KEYWORDS   = ["cancelled", "canceled", "return to shipper", "rts",
                         "undelivered - return", "shipment cancelled"]
PICKED_UP_KEYWORDS   = ["picked up from shipper", "pickup completed"]
OUT_FOR_DELIVERY_KW  = ["out for delivery"]
IN_TRANSIT_KEYWORDS  = ["in transit", "departed operations", "received at",
                         "sms sent to consignee", "under processing",
                         "delivery scheduled", "delivery address corrected",
                         "shipment update"]
SCHEDULED_KEYWORDS   = ["record created", "delivery scheduled"]

# ── Code-prefix fallback (used only if no description keyword matches) ───────
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
    ("PU",  "Delivered"),
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


def _get_cutoff():
    return add_days(today(), -TRACKING_DAYS_LIMIT)


def _status_from_description(description, is_return):
    """Return a status string if description matches a known keyword, else None."""
    desc = (description or "").lower()

    if is_return:
        if any(k in desc for k in PICKED_UP_KEYWORDS):
            return "Delivered"  # terminal for returns
    else:
        if any(k in desc for k in DELIVERED_KEYWORDS):
            return "Delivered"

    if any(k in desc for k in CANCELLED_KEYWORDS):
        return "Cancelled"
    if any(k in desc for k in OUT_FOR_DELIVERY_KW):
        return "Shipped"
    if any(k in desc for k in IN_TRANSIT_KEYWORDS):
        return "Shipped"
    if any(k in desc for k in SCHEDULED_KEYWORDS):
        return "Scheduled"
    return None


def _status_from_code(update_code, is_return):
    code = (update_code or "").upper().strip()
    prefix_map = RETURN_PREFIX_MAP if is_return else CODE_PREFIX_MAP
    for prefix, status in prefix_map:
        if code.startswith(prefix):
            return status
    return None


# Priority ranking: higher number wins when multiple events are scanned.
# This lets us find a "Delivered" event even if it's buried in history and
# the API's own "last update" pointer is stale/wrong.
STATUS_RANK = {"Scheduled": 1, "Shipped": 2, "Cancelled": 3, "Delivered": 4}


def determine_final_status(all_updates, is_return, steps):
    """
    Scan ALL tracking updates (not just the supposed 'latest' one) and return
    the highest-ranked status found, using description keywords first and
    code prefix as fallback. Also returns which update produced that status.
    """
    best_status = None
    best_update = None

    for idx, u in enumerate(all_updates):
        code = u.get("update_code") or ""
        desc = u.get("update_description") or ""

        status_from_desc = _status_from_description(desc, is_return)
        status_from_code  = _status_from_code(code, is_return)
        resolved = status_from_desc or status_from_code

        steps.append(
            f"  [{idx}] code={code!r} desc={desc!r} "
            f"-> from_desc={status_from_desc!r} from_code={status_from_code!r} "
            f"-> resolved={resolved!r}"
        )

        if resolved and (best_status is None or STATUS_RANK.get(resolved, 0) > STATUS_RANK.get(best_status, 0)):
            best_status = resolved
            best_update = u

    return best_status, best_update


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
    out(f"Polling shipments created after: {_get_cutoff()}")

    shipments = frappe.get_all(
        "Aramex Shipment",
        filters={
            "docstatus": 1,
            "awb_number": ["!=", ""],
            "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
            "creation": [">=", _get_cutoff()],
        },
        fields=["name", "awb_number", "shipment_status", "is_return", "custom_last_checked"],
        order_by="custom_last_checked asc",
        limit=PER_RUN_LIMIT,
    )
    out(f"Active shipments to poll: {len(shipments)}")

    if not shipments:
        return "\n".join(lines)

    test_awbs = [s["awb_number"] for s in shipments[:3]]
    out(f"\nTesting FULL history API with AWBs: {test_awbs}")

    try:
        # IMPORTANT: get_last_update_only=False to fetch full history
        result = call_aramex_tracking_api(test_awbs, get_last_update_only=False)
        out(f"API has_errors: {result.get('has_errors')}")
        tracking = result.get("tracking_results", {})
        out(f"AWBs returned: {list(tracking.keys())}")

        for awb, updates in tracking.items():
            sm = next((s for s in shipments if s["awb_number"] == awb), None)
            is_return = bool(sm.get("is_return")) if sm else False
            out(f"\n  AWB {awb} ({len(updates)} updates):")
            steps = []
            final_status, final_update = determine_final_status(updates, is_return, steps)
            for s in steps:
                out(s)
            out(f"  >>> FINAL STATUS: {final_status}")
    except Exception:
        out(f"API ERROR: {frappe.get_traceback()}")

    out("\n=== END DIAGNOSTIC ===")
    return "\n".join(lines)


def update_aramex_shipment_statuses():
    """
    Hourly scheduled task.
    Fetches FULL tracking history per AWB and scans all events to find the
    true current status, rather than trusting Aramex's "last update" pointer.
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

    shipments = frappe.get_all(
        "Aramex Shipment",
        filters={
            "docstatus": 1,
            "awb_number": ["!=", ""],
            "shipment_status": ["not in", list(TERMINAL_STATUSES) + list(SKIP_STATUSES)],
            "creation": [">=", _get_cutoff()],
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
            # Fetch FULL history, not just "last update"
            result = call_aramex_tracking_api(batch, get_last_update_only=False)
        except Exception:
            api_failed = True
            err = frappe.get_traceback()
            frappe.log_error(title="Aramex Tracker — API call failed", message=err)
            if log:
                log.append("results", {
                    "awb_number": str(batch),
                    "remarks": f"API ERROR: {err[:300]}",
                    "execution_steps": err[:3000]
                })
            continue

        if result.get("has_errors") or not result.get("tracking_results"):
            msg = "; ".join(
                n.get("message", "") for n in (result.get("notifications") or [])
            ) or "No tracking results returned"
            for awb in batch:
                sm = awb_to_shipment.get(awb)
                if sm:
                    frappe.db.set_value("Aramex Shipment", sm["name"],
                        {"custom_last_checked": now_datetime()})
                    if log:
                        log.append("results", {
                            "shipment":        sm["name"],
                            "awb_number":      awb,
                            "is_return":       sm["is_return"],
                            "previous_status": sm["shipment_status"],
                            "remarks":         f"No data from API: {msg}"
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


def _process_and_build_row(shipment_meta, all_updates):
    doc_name  = shipment_meta["name"]
    is_return = bool(shipment_meta.get("is_return"))
    prev_status = shipment_meta["shipment_status"]

    steps = [f"Shipment {doc_name} | AWB {shipment_meta['awb_number']} | is_return={is_return} | prev_status={prev_status}"]
    steps.append(f"Total updates returned by Aramex: {len(all_updates)}")

    new_status, winning_update = determine_final_status(all_updates, is_return, steps)

    steps.append(f"FINAL RESOLVED STATUS: {new_status}")

    update_code = (winning_update.get("update_code") or "") if winning_update else ""
    description = (winning_update.get("update_description") or "") if winning_update else ""
    comments    = (winning_update.get("comments") or "") if winning_update else ""
    location    = (winning_update.get("update_location") or "") if winning_update else ""

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
        "remarks":              "",
        "execution_steps":      "\n".join(steps),
        "raw_api_response":     json.dumps(all_updates, indent=2, default=str)[:100000],
    }

    update_values = {"custom_last_checked": now_datetime()}

    if not new_status:
        row["remarks"] = "Could not resolve status from any update in history"
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