app_name = "aramex_connector"
app_title = "Aramex Connector"
app_publisher = "Printechs"
app_description = "Aramex Connector"
app_email = "thomas@printechs.com"
app_license = "mit"

doctype_js = {"Delivery Note": "public/js/delivery_note.js"}
app_include_css = ["public/css/aramex_shipment.css"]

scheduler_events = {
    "hourly": [
        "aramex_connector.tasks.queue_aramex_status_update_jobs"
    ],
}

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["name", "in", (
            "Sales Order Item-custom_awb_number",
            "Sales Order-custom_awb_number",
            "Sales Order-custom_label_url",
            "Sales Order Item-custom_label_url",
            "Sales Order-custom_delivery_time",
        )]]
    }
]