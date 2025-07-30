frappe.ui.form.on('Delivery Note', {
    refresh: function(frm) {
      if (frm.doc.docstatus === 1 || frm.doc.docstatus === 0) {
        frm.add_custom_button(__('Create Aramex Shipment'), function() {
          frappe.call({
            method: 'aramex_connector.aramex_connector.doctype.aramex_shipment.aramex_shipment.create_aramex_shipment',
            args: { delivery_note: frm.doc.name },
            callback: function(r) {
              if (r.message) {
                frappe.set_route('Form', 'Aramex Shipment', r.message.name);
              }
            }
          });
        }, __('Create'));
      }
    }
  });
  