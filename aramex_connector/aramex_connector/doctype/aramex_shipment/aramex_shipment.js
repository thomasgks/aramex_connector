// Copyright (c) 2025, Printechs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Aramex Shipment", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Aramex Shipment', {
    refresh(frm) {
      if (frm.doc.docstatus === 1) {  // only if submitted
        //disable_form(frm);
        // Add "Request Pickup" button
        frm.add_custom_button(__('Request Create Shipment'), () => {
          frappe.call({
            method: 'aramex_connector.api.create_aramex_shipment_ws',  // your custom method aramex_connector.api.create_aramex_shipment
            args: {
              doc: frm.doc.name,
              method: 'on_submit'
            },
            callback: function(r) {
              if (r.message) {
                console.log(r);                
                frappe.msgprint(__('Create Shipment requested successfully.'));
              }
            },
                error: function(err) {
                    frappe.msgprint(__('Failed to create shipment'));
                },
                always: function() {
                    //enable_form(frm);  // Step 3
                }
          });
        }, __('Actions'));
  
        // Add "Print Label" button only if label_url exists
        if (frm.doc.label_url) {
          frm.add_custom_button(__('Download Label PDF'), function() {
           const timestamp = new Date().getTime();
        const downloadUrl = `${frm.doc.label_url}?t=${timestamp}`; // Bypass cache
        window.open(downloadUrl, '_blank');
        });

        }
        if(frm.doc.awb_number) {
          frm.add_custom_button(__('Track Package'), function() {
              track_aramex_shipment(frm);
          });
      }

      }// docstatus==1
    }//refresh
  });
  
frappe.ui.form.on('Aramex Shipment', {
    shipper_address(frm) {
      if (frm.doc.shipper_address) {
        frappe.call({
          method: 'frappe.contacts.doctype.address.address.get_address_display',
          args: {
            address_dict: frm.doc.shipper_address
          },
          callback: function(r) {
            if (r.message) {
              frm.set_value('shipper_address_display', r.message);
            }
          }
        });
      } else {
        frm.set_value('shipper_address_display', '');
      }
    },

    shipper_contact(frm) {
        if (frm.doc.shipper_contact) {
          frappe.call({
            method: 'erpnext.stock.doctype.delivery_trip.delivery_trip.get_contact_display',
            args: {
              contact: frm.doc.shipper_contact
            },
            callback: function(r) {
              if (r.message) {
                frm.set_value('shipper_contact_display', r.message);
              }
            }
          });
        } else {
          frm.set_value('shipper_contact_display', '');
        }
    }
    
  });
  
  frappe.ui.form.on('Aramex Shipment', {
    consignee_address(frm) {
      if (frm.doc.consignee_address) {
        frappe.call({
          method: 'frappe.contacts.doctype.address.address.get_address_display',
          args: {
            address_dict: frm.doc.consignee_address
          },
          callback: function(r) {
            if (r.message) {
              frm.set_value('consignee_address_display', r.message);
            }
          }
        });
      } else {
        frm.set_value('consignee_address_display', '');
      }
    },

    shipper_contact(frm) {
        if (frm.doc.consignee_contact) {
          frappe.call({
            method: 'erpnext.stock.doctype.delivery_trip.delivery_trip.get_contact_display',
            args: {
              contact: frm.doc.consignee_contact
            },
            callback: function(r) {
              if (r.message) {
                frm.set_value('consignee_contact_display', r.message);
              }
            }
          });
        } else {
          frm.set_value('consignee_contact_display', '');
        }
    }
  });

function track_aramex_shipment(frm) {
  frappe.call({
    method: "aramex_connector.api.track_aramex_shipment",
    args: {
      "shipment_ids": frm.doc.awb_number,
      "get_last_update_only": false
    },
    callback: function (r) {
      if (!r.exc) {
        show_tracking_results(frm, r.message);
      }
    }
  });
}
    function show_tracking_results(frm, tracking_data) {
      // Prepare HTML content
      let html = `
          <div class="aramex-tracking-results">
              <h5>Tracking History for ${frm.doc.awb_number}</h5>
      `;
      
      if(tracking_data.notifications && tracking_data.notifications.length) {
          html += `<div class="notifications">`;
          tracking_data.notifications.forEach(notif => {
              html += `<div class="alert alert-${notif.code === '0' ? 'info' : 'warning'}">
                  ${notif.message}
              </div>`;
          });
          html += `</div>`;
      }
      
      if(tracking_data.tracking_results && tracking_data.tracking_results[frm.doc.awb_number]) {
          const updates = tracking_data.tracking_results[frm.doc.awb_number];
          
          html += `
              <div class="tracking-updates">
                  <table class="table table-bordered">
                      <thead>
                          <tr>
                              <th>Date/Time</th>
                              <th>Location</th>
                              <th>Status</th>
                              <th>Details</th>
                          </tr>
                      </thead>
                      <tbody>
          `;
          
          updates.forEach(update => {
              html += `
                  <tr>
                      <td>${update.update_datetime}</td>
                      <td>${update.update_location || '-'}</td>
                      <td>${update.update_description || '-'}</td>
                      <td>${update.comments || '-'}</td>
                  </tr>
              `;
          });
          
          html += `
                      </tbody>
                  </table>
              </div>
          `;
      } else {
          html += `<div class="alert alert-info">No tracking information available</div>`;
      }
      
      html += `</div>`;
      
      // Show dialog
      const dialog = new frappe.ui.Dialog({
          title: `Aramex Tracking - ${frm.doc.awb_number}`,
          fields: [
              {
                  fieldname: "tracking_html",
                  fieldtype: "HTML",
                  options: html
              }
          ],
          size: 'large'
      });
      
      dialog.show();
      
      // Add CSS styling
      dialog.$wrapper.find('.modal-dialog').css('width', '80%');
      dialog.$wrapper.find('.aramex-tracking-results').css('padding', '15px');
  }


  
