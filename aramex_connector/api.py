import frappe
import requests
import json
from frappe.utils import get_datetime
from datetime import datetime
import time
import xml.etree.ElementTree as ET
import html
from zeep import Client
from zeep.transports import Transport 


def test():
    print("OK")
    dt = datetime.now()

    unix_ms = int(time.mktime(dt.timetuple()) * 1000)
    #formatted_date = f'\/Date({unix_ms}+0300)\/'
    formatted_date = f"/Date({unix_ms})/"
    print(formatted_date)
    
    # 1. Unescape the HTML entities
    escaped_response = '''&lt;ShipmentCreationResponse xmlns="http://ws.aramex.net/ShippingAPI/v1/"&gt;&lt;Transaction xmlns:i="http://www.w3.org/2001/XMLSchema-instance"&gt;&lt;Reference1&gt;SAL-ORD-2025-00164&lt;/Reference1&gt;&lt;Reference2&gt;ARMX25-00002&lt;/Reference2&gt;&lt;Reference3/&gt;&lt;Reference4/&gt;&lt;Reference5/&gt;&lt;/Transaction&gt;&lt;Notifications xmlns:i="http://www.w3.org/2001/XMLSchema-instance"/&gt;&lt;HasErrors&gt;false&lt;/HasErrors&gt;&lt;Shipments xmlns:i="http://www.w3.org/2001/XMLSchema-instance"&gt;&lt;ProcessedShipment&gt;&lt;ID&gt;50464931952&lt;/ID&gt;&lt;Reference1/&gt;&lt;Reference2/&gt;&lt;Reference3/&gt;&lt;ForeignHAWB/&gt;&lt;HasErrors&gt;false&lt;/HasErrors&gt;&lt;Notifications/&gt;&lt;ShipmentLabel&gt;&lt;LabelURL&gt;https://ws.aramex.net/ShippingAPI.V2/rpt_cache/d9013fea58d34779bbf9c03dad1f2726.pdf&lt;/LabelURL&gt;&lt;LabelFileContents/&gt;&lt;/ShipmentLabel&gt;&lt;ShipmentDetails&gt;&lt;Origin&gt;RUH&lt;/Origin&gt;&lt;Destination&gt;RUH&lt;/Destination&gt;&lt;ChargeableWeight&gt;&lt;Unit&gt;KG&lt;/Unit&gt;&lt;Value&gt;1&lt;/Value&gt;&lt;/ChargeableWeight&gt;&lt;DescriptionOfGoods&gt;Shoes and Accessories&lt;/DescriptionOfGoods&gt;&lt;GoodsOriginCountry&gt;SA&lt;/GoodsOriginCountry&gt;&lt;NumberOfPieces&gt;1&lt;/NumberOfPieces&gt;&lt;ProductGroup&gt;DOM&lt;/ProductGroup&gt;&lt;ProductType&gt;OND&lt;/ProductType&gt;&lt;PaymentType&gt;P&lt;/PaymentType&gt;&lt;PaymentOptions/&gt;&lt;CustomsValueAmount i:nil="true"/&gt;&lt;CashOnDeliveryAmount&gt;&lt;CurrencyCode&gt;SAR&lt;/CurrencyCode&gt;&lt;Value&gt;195&lt;/Value&gt;&lt;/CashOnDeliveryAmount&gt;&lt;InsuranceAmount&gt;&lt;CurrencyCode&gt;SAR&lt;/CurrencyCode&gt;&lt;Value&gt;0&lt;/Value&gt;&lt;/InsuranceAmount&gt;&lt;CashAdditionalAmount i:nil="true"/&gt;&lt;CollectAmount i:nil="true"/&gt;&lt;Services&gt;CODS,&lt;/Services&gt;&lt;OriginCity&gt;Riyadh&lt;/OriginCity&gt;&lt;DestinationCity&gt;Riyadh&lt;/DestinationCity&gt;&lt;/ShipmentDetails&gt;&lt;ShipmentAttachments/&gt;&lt;SortCode&gt;R&lt;/SortCode&gt;&lt;/ProcessedShipment&gt;&lt;/Shipments&gt;&lt;/ShipmentCreationResponse&gt;'''  # your full response.text
    xml_string = html.unescape(escaped_response)

    # 2. Define namespace
    ns = {'ns': 'http://ws.aramex.net/ShippingAPI/v1/'}

    # 3. Parse XML
    root = ET.fromstring(xml_string)

    # 4. Extract values
    shipment_id = root.find('.//ns:ProcessedShipment/ns:ID', ns).text
    label_url = root.find('.//ns:ProcessedShipment/ns:ShipmentLabel/ns:LabelURL', ns).text

    print("Shipment ID:", shipment_id)
    print("Label URL:", label_url)

@frappe.whitelist()
def create_aramex_shipment1(doc, method):
    return "OKOKOK"
    
@frappe.whitelist()
def create_aramex_shipment_ws(doc, method):
    
    
    #shipment_name="ARMX25-00002"
    
    if isinstance(doc, str):
        doc = frappe.get_doc("Aramex Shipment", doc)
       
    # Get the first item's Sales Order reference (assuming invoice is linked to a single Sales Order)
    reference_no = doc.delivery_notes[0].sales_order if doc.delivery_notes and hasattr(doc.delivery_notes[0], "sales_order") else "Unknown"
    print(doc.name)
    
    settings = frappe.get_single("Aramex Setting")
        
    # Get address/contact details
    shipper_address = frappe.get_doc("Address", doc.shipper_address)
    print(shipper_address.address_line1)
    
    shipper_contact = frappe.get_doc("Contact", doc.shipper_contact)
    print(shipper_contact.first_name)
    
    consignee_address = frappe.get_doc("Address", doc.consignee_address)
    print(consignee_address.address_line1)
    
    consignee_contact = frappe.get_doc("Contact", doc.consignee_contact)
    print(consignee_contact.first_name)

    shipper = get_party_details(shipper_address.name, shipper_contact.name, reference_no, is_shipper=True,  settings=settings)
    consignee = get_party_details(consignee_address.name, consignee_contact.name, reference_no, is_shipper=False, settings=settings)
    
    
    #dt = datetime.strptime("2025-07-21T12:16:10", "%Y-%m-%dT%H:%M:%S")
    dt = datetime.now()

    unix_ms = int(time.mktime(dt.timetuple()) * 1000)
    #formatted_date = f'\/Date({unix_ms}+0300)\/'
    formatted_date = f"/Date({unix_ms})/"
    print(formatted_date)
    payload = {
        "ClientInfo": {
            "UserName": settings.user_name,
            "Password": settings.get_password("password"),
            "Version": settings.api_version,
            "AccountNumber": settings.account_number,
            "AccountPin": settings.account_pin, #get_password("account_pin"),
            "AccountEntity": settings.account_entity,
            "AccountCountryCode": settings.account_country_code,
            "Source": 24
        },
        "Transaction": {"Reference1": reference_no,
                        "Reference2": doc.name,
                        "Reference3": "",
                        "Reference4": "",
                        "Reference5": ""},
        "Shipments": [{
            "Reference1": "",
            "Reference2": "",
            "Reference3": "",
            "Shipper": shipper,
            "Consignee": consignee,
            "ThirdParty": None,
            "ShippingDateTime": formatted_date,
            "DueDate":formatted_date,
            "Comments": "",
            "PickupLocation": "",
            "OperationsInstructions": "",
            "AccountingInstrcutions": "",
            "Attachments": [],
            "ForeignHAWB": "",
            "TransportType ": 0,
            "PickupGUID": "",
            "Number": None,
            "ScheduledDelivery": None,
            "Details": {
                "ActualWeight": {"Value": doc.actual_weight, "Unit": doc.unit},                
                "Dimensions": None,
                "ChargeableWeight": None,
                "ProductGroup": settings.default_product_group,
                "ProductType": settings.default_product_type,
                "PaymentType": "P",# if doc.payment_state=="Prepaid" else "P",
                "NumberOfPieces": doc.number_of_pieces,
                "DescriptionOfGoods": doc.description_of_goods,
                "GoodsOriginCountry": shipper["PartyAddress"]["CountryCode"],
                "CashOnDeliveryAmount": None if doc.payment_state=="Prepaid" else { "Value": doc.amount_to_collect, "CurrencyCode": "SAR" },
                "PaymentOptions": "",
                "CustomsValueAmount": None,
                "InsuranceAmount":{
 
                    "CurrencyCode": "SAR",
                    "Value": 0
                },
                "CashAdditionalAmount": None,
                "CashAdditionalAmountDescription": "",
                "CashAdditionalAmount": None,
                "CashAdditionalAmountDescription": "",
                "CollectAmount": None,
                "Services":  "" if doc.payment_state=="Prepaid" else "CODS",
                "Items": []
            }
        }],
        "LabelInfo": {
            "ReportID": 9201,
            "ReportType": "URL"
        }
    }
    return call_aramex_api(payload, doc)

def get_party_details(address, contact, reference_no, is_shipper=False, settings=None):
    
    addr = frappe.get_doc("Address", address)
    cntct = frappe.get_doc("Contact", contact)
    
    country=frappe.get_doc("Country", addr.country)
    return {
        "Reference1": reference_no,
        "Reference2": "",
        "AccountNumber": settings.account_number if is_shipper else "",
        "PartyAddress": {
            "Line1": addr.address_line1,
            "Line2": addr.address_line2 or "",
            "Line3":"",
            "City": addr.city,
            "StateOrProvinceCode":"", #addr.state,
            "PostCode": "", #addr.pincode,
            "CountryCode": country.code,
            "Longitude": 0,
            "Latitude": 0,
            "BuildingNumber": "",
            "BuildingName": "",
            "Floor": "",
            "Apartment": "",
            "POBox": None,
            "Description": ""
            
        },
        "Contact": {
            "Department": "",
            "PersonName": cntct.first_name,
            "Title": "",
            "CompanyName": cntct.company_name or cntct.first_name,
            "PhoneNumber1": cntct.phone,
            "PhoneNumber1Ext": "",
            "PhoneNumber2":"",
            "PhoneNumber2Ext": "",
            "FaxNumber": "",
            "CellPhone": cntct.phone,
            "EmailAddress": cntct.email_id,
            "Type":""
        }
    }
    
def call_aramex_api(payload, doc):
    settings = frappe.get_cached_doc("Aramex Setting")
    so_no = doc.delivery_notes[0].sales_order if doc.delivery_notes and hasattr(doc.delivery_notes[0], "sales_order") else "Unknown"
    url = settings.test_url if settings.mode=="Test" else settings.production_url
    full_url = url + "/CreateShipments"
    
    print(f"Doc: {doc.name}")
   
    # Mask sensitive data for logging
    logged_payload = dict(payload)
    #logged_payload["ClientInfo"]["Password"] = "***"
    #logged_payload["ClientInfo"]["AccountPin"] = "***"
    
    #frappe.logger().info(f"Aramex Request URL: {full_url}")
    #frappe.logger().info(f"Aramex Request Payload: {json.dumps(logged_payload, indent=2)}")
    print(f"Aramex Request URL: {full_url}")
    print(f"Aramex Request Payload: {json.dumps(logged_payload, indent=2)}")
    
    try:
        response = requests.post(
            full_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
       # response_json = response.json() #if response_status == 200 #else {"error": response.text}
       
        
        #frappe.logger().info(f"HTTP Status: {response.status_code}")
        #frappe.logger().info(f"Response Content: {response.text[:500]}")  # First 500 chars
        print(f"HTTP Status: {response.status_code}")
        print(f"Response Content: {response.text}")  # First 500 chars
        if response.status_code==200:
            # Handle non-JSON responses
            
            if 'application/json' not in response.headers.get('Content-Type', ''):            
                # 1. Unescape the HTML entities
                xml_string = html.unescape(response.text)
                formatted_xml=None          
                try:
                    parsed_xml = xml.dom.minidom.parseString(xml_string)
                    formatted_xml = parsed_xml.toprettyxml(indent="  ")
                except Exception:
                    # fallback to raw if parsing fails
                    formatted_xml = xml_string

                # 2. Define namespace
                ns = {'ns': 'http://ws.aramex.net/ShippingAPI/v1/'}

                # 3. Parse XML
                root = ET.fromstring(xml_string)

                # 4. Extract values
                has_errors = root.find('.//ns:HasErrors', ns).text 
                               
                if has_errors == "true":
                    # doc.db_set("api_call_status", "Error")                    
                    # doc.db_set("api_payload", json.dumps(payload, indent=4))
                    # doc.db_set("api_response", formatted_xml)
                    #frappe.throw(f"Triggered! HTTP Status:{doc.name}{has_errors}, {response.status_code}, ERROR_FE {formatted_xml}")
                    frappe.db.set_value(doc.doctype, doc.name, {
                            "api_call_status": "Error",
                            "api_payload": json.dumps(payload, indent=4),
                            "api_response": formatted_xml
                        })
                    frappe.db.commit()


                    #frappe.throw(f"Triggered! HTTP Status:{has_errors}, {response.status_code}, ERROR_FE {formatted_xml}")
                    frappe.logger().error(f"Aramex returned non-JSON response: {response.text}")
                else:
                    shipment_id = root.find('.//ns:ProcessedShipment/ns:ID', ns).text
                    label_url = root.find('.//ns:ProcessedShipment/ns:ShipmentLabel/ns:LabelURL', ns).text
                    doc.api_call_status= "Success"
                    doc.api_payload= json.dumps(payload, indent=4)
                    doc.api_response= formatted_xml
                    doc.awb_number= shipment_id
                    doc.label_url= label_url
                    doc.shipment_status= "Shipped"
                    
                    # frappe.db.set_value(doc.doctype, doc.name, {
                    #         "api_call_status": "Success",
                    #         "api_payload": json.dumps(payload, indent=4),
                    #         "api_response": formatted_xml,
                    #         "awb_number": shipment_id,
                    #         "label_url": label_url,
                    #         "shipment_status": "Shipped"
                    #     })
                    # frappe.db.commit()
                    
                    # doc.db_set("api_call_status", "Success")
                    # doc.db_set("api_payload", json.dumps(payload, indent=4))
                    # doc.db_set("api_response", formatted_xml)
                    # doc.db_set("awb_number", shipment_id)
                    # doc.db_set("label_url", label_url)
                    # doc.db_set("shipment_status", "Shipped")
                    frappe.logger().info(f"Aramex Shipment Success - AWB: {shipment_id}")

                #return
                #frappe.throw(f"Aramex returned error with non-JSON response: {response.text}")
            else:
                data = response.json()
                call_status="Pending"
                if data.get("HasErrors", True):
                    call_status="Error"
                    handle_errors(data, doc)
                else:
                    call_status="Success"
                    update_shipment_details(data, doc)
                doc.api_call_status=call_status
                #doc.api_response=json.dumps(response_json, indent=4)
                doc.api_payload=json.dumps(payload, indent=4)
                doc.save() 
                frappe.db.commit()
        else:
            doc.api_call_status="Error"
            doc.api_response= str(e)
            doc.save()
            frappe.db.commit()
            frappe.log_error(f"Aramex API Error: {str(e)}")
    except Exception as e:
        doc.api_call_status="Error"
        doc.api_response= str(e)
        doc.save()
        frappe.db.commit()
        frappe.log_error(f"Aramex API Error: {str(e)}")
        frappe.throw("Failed to connect to Aramex")
        
def update_shipment_details(response, doc):
    shipment = response["Shipments"][0]
    
    doc.update({
        "awb_number": shipment["ID"],
        "label_url": shipment["ShipmentLabel"]["LabelURL"],
        "shipment_status": "Shipped"
    })
    doc.save()
    frappe.db.commit()
    
def handle_errors(response, doc):
    errors = [f"{e['Code']}: {e['Message']}" for e in response["Notifications"]]
    doc.db_set("shipment_status", "Failed")
    frappe.log_error(f"Aramex Error: {json.dumps(errors)}", doc.name)
    frappe.throw("<br>".join(errors))
    
 #*****************************************TRACKING********************************************************   

@frappe.whitelist()
def track_aramex_shipment(shipment_ids, get_last_update_only=False):
    """
    Track Aramex shipments and format results for UI display
    """
    try:
        if isinstance(shipment_ids, str):
            shipment_ids = [shipment_ids]
            
        # Call the actual Aramex API (implementation from previous example)
        result = call_aramex_tracking_api(shipment_ids, get_last_update_only)
        
        # Format dates for better display
        if result.get("tracking_results"):
            for tracking_number, updates in result["tracking_results"].items():
                for update in updates:
                    if update.get("update_datetime"):
                        update["update_datetime"] = frappe.utils.format_datetime(update["update_datetime"])
        
        return result
        
    except Exception as e:
        frappe.log_error(f"Aramex Tracking Error: {str(e)}")
        return {
            "has_errors": True,
            "notifications": [{
                "code": "ERROR",
                "message": f"Failed to track shipment: {str(e)}"
            }],
            "tracking_results": {}
        }

def call_aramex_tracking_api(shipment_ids, get_last_update_only=False):
    """
    Make the actual SOAP API call to Aramex tracking service with robust error handling
    """
    settings = frappe.get_single("Aramex Setting")
    
    if not settings:
        return {
            "has_errors": True,
            "notifications": [{
                "code": "DISABLED", 
                "message": "Aramex settings not found"
            }],
            "tracking_results": {}
        }

    try:
        # Initialize SOAP client with timeout
        wsdl_url = "http://ws.aramex.net/shippingapi/tracking/service_1_0.svc?wsdl"
        session = requests.Session()
        transport = Transport(session=session, timeout=30)
        client = Client(wsdl_url, transport=transport)
        
        # Prepare request payload
        request = {
            "ClientInfo": {
                "UserName": settings.user_name,
                "Password": settings.get_password("password"),
                "Version": settings.api_version or "1.0",
                "AccountNumber": settings.account_number,
                "AccountPin": settings.get_password("account_pin"),
                "AccountEntity": settings.account_entity,
                "AccountCountryCode": settings.account_country_code,
                "Source": 24
            },
            "Transaction": {
                "Reference1": "ERPNext Tracking",
                "Reference2": frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S"),
            },
            "Shipments": shipment_ids,
            "GetLastTrackingUpdateOnly": get_last_update_only
        }
        
        # Make the API call with error handling
        response = client.service.TrackShipments(**request)
        
        # Initialize default result structure
        result = {
            "has_errors": True,  # Default to error until we confirm success
            "notifications": [],
            "tracking_results": {}
        }
        
        # Check if we got a valid response
        if not response:
            result["notifications"].append({
                "code": "NO_RESPONSE",
                "message": "Received empty response from Aramex server"
            })
            return result
        
        # Process notifications safely
        notifications = getattr(response, "Notifications", None)
        if notifications:
            notification_list = getattr(notifications, "Notification", [])
            if notification_list:
                if not isinstance(notification_list, list):
                    notification_list = [notification_list]
                
                result["notifications"] = [{
                    "code": getattr(n, "Code", "UNKNOWN"),
                    "message": getattr(n, "Message", "No message")
                } for n in notification_list]
        
        # Only consider successful if we have no errors AND tracking results
        if hasattr(response, "HasErrors") and not response.HasErrors:
            result["has_errors"] = False
            
            # Process tracking results if available
            tracking_results = getattr(response, "TrackingResults", None)
            if tracking_results:
                items = getattr(tracking_results, "KeyValueOfstringArrayOfTrackingResultmFAkxlpY", [])
                if items:
                    if not isinstance(items, list):
                        items = [items]
                    
                    for item in items:
                        tracking_number = getattr(item, "Key", "UNKNOWN")
                        updates = getattr(item.Value, "TrackingResult", [])
                        
                        if updates:
                            if not isinstance(updates, list):
                                updates = [updates]
                            
                            result["tracking_results"][tracking_number] = [{
                                "waybill_number": getattr(update, "WaybillNumber", tracking_number),
                                "update_code": getattr(update, "UpdateCode", ""),
                                "update_description": getattr(update, "UpdateDescription", ""),
                                "update_datetime": str(getattr(update, "UpdateDateTime", "")),
                                "update_location": getattr(update, "UpdateLocation", ""),
                                "comments": getattr(update, "Comments", ""),
                                "problem_code": getattr(update, "ProblemCode", "")
                            } for update in updates]
        
        return result
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Network error connecting to Aramex: {str(e)}"
    except Exception as e:
        error_msg = f"Aramex API error: {str(e)}"
    
    frappe.log_error(
        title="Aramex Tracking API Failure",
        message=f"Error tracking shipments {shipment_ids}: {error_msg}\n{frappe.get_traceback()}"
    )
    
    return {
        "has_errors": True,
        "notifications": [{
            "code": "API_ERROR",
            "message": error_msg
        }],
        "tracking_results": {}
    }