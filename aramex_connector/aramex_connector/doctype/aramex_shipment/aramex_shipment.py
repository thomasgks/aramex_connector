# Copyright (c) 2025, Printechs and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document
import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from erpnext.stock.doctype.delivery_trip.delivery_trip import get_contact_display
from aramex_connector.api import create_aramex_shipment_with_pickup

@frappe.whitelist()
def test():
    print("OK")

@frappe.whitelist()
def create_aramex_shipment(delivery_note):
    
    # Check if an Aramex Shipment already exists for this Delivery Note
    linked_shipments = frappe.get_all(
        "Aramex Shipment Delivery Note",
        filters={"delivery_note": delivery_note},
        fields=["parent"])

    for link in linked_shipments:
        docstatus = frappe.db.get_value("Aramex Shipment", link.parent, "docstatus")
        if docstatus != 2:
            frappe.throw(_("Aramex Shipment already exists: {0}").format(link.parent))
    
    dn = frappe.get_doc("Delivery Note", delivery_note)

    shipment = frappe.new_doc("Aramex Shipment")
    shipment.is_return=dn.is_return
    if dn.is_return==0:
        shipment.pickup_from_type = "Company"
        shipment.pickup_company=dn.company
        shipment.shipper_address = dn.company_address or ""
        shipment.shipper_contact =get_company_contact(dn.company) #"Mr.X B" #dn.contact_person or ""
  
        shipment.delivery_to_type = "Customer"
        shipment.delivery_customer=dn.customer		
        shipment.consignee_address = dn.customer_address or ""
        shipment.consignee_contact = dn.contact_person or ""
    else:
        shipment.pickup_from_type = "Customer"
        shipment.pickup_customer=dn.customer
        shipment.shipper_address = dn.customer_address or ""
        shipment.shipper_contact =dn.contact_person or ""
  
        shipment.delivery_to_type = "Company"
        shipment.delivery_company=dn.company		
        shipment.consignee_address = dn.company_address or ""
        shipment.consignee_contact = get_company_contact(dn.company) #"Mr.X B" or ""
     
     # Display fields
    if shipment.shipper_address:
        shipment.shipper_address_display = get_address_display(shipment.shipper_address)
    if shipment.shipper_contact and frappe.db.exists("Contact", shipment.shipper_contact):
        shipment.shipper_contact_display = get_contact_display(shipment.shipper_contact)
    if shipment.consignee_address:
        shipment.consignee_address_display = get_address_display(shipment.consignee_address)
    if shipment.consignee_contact and frappe.db.exists("Contact", shipment.consignee_contact):
        shipment.consignee_contact_display = get_contact_display(shipment.consignee_contact)
        
    shipment.number_of_pieces = 1 #len(dn.items)
    shipment.description_of_goods = "Shoes and Accessories"
    shipment.actual_weight =1 #sum([d.weight for d in dn.items if d.weight])
    shipment.unit = "KG"
    shipment.payment_state = "COD"  # or logic from Sales Order
    shipment.shipmet_date = dn.posting_date
    

    # Link delivery note(s) custom_ecommerce_payment_mode
    # Get Sales Order from first item in Delivery Note
    sales_order = dn.items[0].against_sales_order if dn.items else None
    payment_mode = ""

    # Safely fetch Sales Order's custom field
    if sales_order and frappe.db.exists("Sales Order", sales_order):
        so_doc = frappe.get_doc("Sales Order", sales_order)
        payment_mode = so_doc.get("custom_ecommerce_payment_mode") or ""
    
    shipment.append("delivery_notes", {
        "delivery_note": dn.name,
        "sales_order": dn.items[0].against_sales_order if dn.items else "",
        "payment_mode": payment_mode,
        "number_of_pieces": len(dn.items),
        "value": dn.base_grand_total
    })
    
    shipment.total_value=dn.base_grand_total
    if payment_mode == "COD" and dn.base_grand_total>0:
        shipment.payment_state = "COD"
        shipment.amount_to_collect=dn.base_grand_total
    else:
        shipment.payment_state = "Prepaid"
        shipment.amount_to_collect=0
        
    shipment.insert()
    #return shipment.as_dict()
    return shipment

def get_company_contact(company_name):
    contact_links = frappe.get_all("Dynamic Link", filters={
        "link_doctype": "Company",
        "link_name": company_name,
        "parenttype": "Contact"
    }, fields=["parent"])

    if not contact_links:
        return None

    # Fetch the first linked Contact
    contact_name = contact_links[0].parent
    #contact = frappe.get_doc("Contact", contact_name)
    return contact_name

class AramexShipment(Document):
    def before_submit(self):
        create_aramex_shipment_with_pickup(self, "before_submit")
        
    def on_submit(self):
        self.shipment_status = 'shipped'
        self.update_sales_order_on_shipment_submit()
    
    def on_cancel(self):
        self.clear_sales_order_on_shipment_cancel()
        
    def update_sales_order_on_shipment_submit(self):
        """Update Sales Order and its items when Aramex Shipment is submitted"""
        
        if not self.awb_number:
            frappe.throw(_("AWB Number is required to update Sales Order"))
        
        # Process each delivery note in the shipment
        for delivery_note_row in self.get("delivery_notes", []):
            if not delivery_note_row.delivery_note:
                continue
                
            try:
                # Get the delivery note
                delivery_note = frappe.get_doc("Delivery Note", delivery_note_row.delivery_note)
                
                # Get unique sales orders from delivery note items
                sales_orders = set()
                dn_items_map = {}
                
                for item in delivery_note.items:
                    if item.against_sales_order:
                        sales_orders.add(item.against_sales_order)
                        if item.against_sales_order not in dn_items_map:
                            dn_items_map[item.against_sales_order] = []
                        dn_items_map[item.against_sales_order].append(item.item_code)
                
                if not sales_orders:
                    continue
                    
                # Process each sales order found in the delivery note
                for so_name in sales_orders:
                    # Update Sales Order header
                    if self.is_return:
                        frappe.db.set_value(
                        "Sales Order",
                        so_name,
                        {
                            "custom_awb_number": self.awb_number,                            
                            "custom_label_url": self.label_url,
                            "custom_return_status":"Scheduled"
                        }
                    )
                        # Update only the Sales Order items that are in this delivery note
                        frappe.db.sql("""
                            UPDATE `tabSales Order Item` soi
                            SET soi.custom_return_status='Scheduled', soi.custom_awb_number = %s, soi.custom_label_url = %s
                            WHERE soi.parent = %s
                            AND soi.item_code IN %s
                        """, (self.awb_number, self.label_url, so_name, dn_items_map[so_name]))
                    else:
                        frappe.db.set_value(
                        "Sales Order",
                        so_name,
                        {
                            "custom_awb_number": self.awb_number,
                            "custom_ecommerce_status": "Shipped"
                        }
                    )
                        # Update only the Sales Order items that are in this delivery note
                        frappe.db.sql("""
                            UPDATE `tabSales Order Item` soi
                            SET soi.custom_awb_number = %s, soi.custom_label_url = %s
                            WHERE soi.parent = %s
                            AND soi.item_code IN %s
                        """, (self.awb_number, self.label_url, so_name, dn_items_map[so_name]))
                    
                    
                    
                    frappe.msgprint(_("Updated Sales Order {0} with AWB {1} for delivered items").format(
                        frappe.bold(so_name),
                        frappe.bold(self.awb_number)
                    ))
                
            except Exception as e:
                frappe.log_error(f"Failed to update Sales Order items for DN {delivery_note_row.delivery_note}", str(e))
                frappe.throw(_("Error updating Sales Order items: {0}").format(str(e)))

    def clear_sales_order_on_shipment_cancel(self):
        """Clear AWB fields when Aramex Shipment is cancelled"""
        
        # Process each delivery note in the shipment
        for delivery_note_row in self.get("delivery_notes", []):
            if not delivery_note_row.delivery_note:
                continue
                
            try:
                # Get the delivery note
                delivery_note = frappe.get_doc("Delivery Note", delivery_note_row.delivery_note)
                
                # Get unique sales orders from delivery note items
                sales_orders = set()
                dn_items_map = {}
                
                for item in delivery_note.items:
                    if item.against_sales_order:
                        sales_orders.add(item.against_sales_order)
                        if item.against_sales_order not in dn_items_map:
                            dn_items_map[item.against_sales_order] = []
                        dn_items_map[item.against_sales_order].append(item.item_code)
                
                if not sales_orders:
                    continue
                    
                # Process each sales order found in the delivery note
                for so_name in sales_orders:
                    # Update Sales Order header
                    if self.is_return:
                        frappe.db.set_value(
                            "Sales Order",
                            so_name,
                            {
                                "custom_awb_number": None,
                                "custom_label_url": None,
                                "custom_return_status":"Return Requested"
                            }
                        )
                        
                        # Clear AWB only for items that were in this delivery note
                        frappe.db.sql("""
                            UPDATE `tabSales Order Item` soi
                            SET soi.custom_return_status='Return Requested',soi.custom_awb_number = NULL, soi.custom_label_url=NULL
                            WHERE soi.parent = %s
                            AND soi.item_code IN %s
                        """, (so_name, dn_items_map[so_name]))
                    else:
                        frappe.db.set_value(
                            "Sales Order",
                            so_name,
                            {
                                "custom_awb_number": None,
                                "custom_label_url": None,
                                "custom_ecommerce_status": "Preparing for Shipment"
                                
                            }
                        )
                        
                        # Clear AWB only for items that were in this delivery note
                        frappe.db.sql("""
                            UPDATE `tabSales Order Item` soi
                            SET soi.custom_return_status='Return Requested',soi.custom_awb_number = NULL, soi.custom_label_url=NULL
                            WHERE soi.parent = %s
                            AND soi.item_code IN %s
                        """, (so_name, dn_items_map[so_name]))
                    frappe.msgprint(_("Cleared AWB from Sales Order {0} for cancelled items").format(
                            frappe.bold(so_name)
                        ))
                    
            except Exception as e:
                frappe.log_error(f"Failed to clear Sales Order items for DN {delivery_note_row.delivery_note}", str(e))
                frappe.throw(_("Error clearing Sales Order items: {0}").format(str(e)))

    def get_linked_sales_orders(self):
        """Get unique Sales Orders linked through Delivery Notes"""
        sales_orders = set()
        
        for row in self.get("delivery_notes", []):
            if not row.delivery_note:
                continue
                
            # Get sales orders from delivery note items
            dn_sales_orders = frappe.db.get_values(
                "Delivery Note Item",
                {"parent": row.delivery_note, "against_sales_order": ("!=", "")},
                "against_sales_order",
                as_dict=True
            )
            
            for so in dn_sales_orders:
                if so.get("against_sales_order"):
                    sales_orders.add(so["against_sales_order"])
        
        return sales_orders