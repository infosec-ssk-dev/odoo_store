import logging
from datetime import datetime
from zeep import Client, Settings
from zeep.exceptions import Fault
from zeep.transports import Transport
from requests import Session
from odoo import _, fields
from odoo.exceptions import UserError
from lxml import etree

import tempfile
import os
import requests

_logger = logging.getLogger(__name__)


class ElfatooraAPI:
    """Client for Elfatoora Web Services (SOAP)."""

    def __init__(self, config, invoice=None):
        self.config = config
        self.invoice = invoice

        if not config.api_url:
            raise UserError(_("API WSDL URL is not configured. Please set it in Elfatoora Settings."))
        if not config.api_username or not config.api_password:
            raise UserError(_("API Username and/or Password are not configured. Please set them in Elfatoora Settings."))
        if not config.company_vat:
            raise UserError(_("Company VAT/Matricule Fiscale is missing. Please set it in Elfatoora Configuration."))

        self.api_url = config.api_url
        self.api_username = config.api_username
        self.api_password = config.api_password
        self.matricule = config.company_vat

        self.client = self._get_soap_client()

    def _get_soap_client(self):
        if not self.api_url:
            return None

        try:
            response = requests.get(self.api_url, verify=False, timeout=30)
            response.raise_for_status()
            wsdl_content = response.text

            if "http://test.elfatoora.tn:80" in wsdl_content:
                wsdl_content = wsdl_content.replace("http://test.elfatoora.tn:80", "https://test.elfatoora.tn")
            if "http://test.elfatoora.tn" in wsdl_content:
                wsdl_content = wsdl_content.replace("http://test.elfatoora.tn", "https://test.elfatoora.tn")

            fd, temp_wsdl_path = tempfile.mkstemp(suffix=".wsdl")
            with os.fdopen(fd, "w") as f:
                f.write(wsdl_content)

            session = Session()
            session.verify = False

            transport = Transport(session=session, timeout=30, operation_timeout=60)
            settings = Settings(strict=False, xml_huge_tree=True)
            return Client(wsdl=temp_wsdl_path, transport=transport, settings=settings)
        except Exception as e:
            _logger.error("Failed to initialize SOAP client: %s", e, exc_info=True)
            return None

    def submit_invoice(self, signed_xml_content):
        if not self.client:
            raise UserError(_("SOAP Client not initialized. Check URL configuration."))

        try:
            if not self.config.soap_operation_submit:
                raise UserError(_("SOAP operation name for submitting invoices is not configured. Please set it in Elfatoora Configuration."))

            operation_name = self.config.soap_operation_submit
            if not hasattr(self.client.service, operation_name):
                raise UserError(_("SOAP operation '%s' not found in WSDL. Please check the operation name in configuration.") % operation_name)

            operation = getattr(self.client.service, operation_name)
            document_efact = signed_xml_content.encode("utf-8") if isinstance(signed_xml_content, str) else signed_xml_content

            try:
                response = operation(self.api_username, self.api_password, self.matricule, document_efact)
            except Fault as fault:
                _logger.error("SOAP Fault: %s", fault.message)
                user_msg = fault.message
                if fault.detail is not None:
                    _logger.error("SOAP Fault Detail: %s", etree.tostring(fault.detail, pretty_print=False).decode())
                    try:
                        fault_code = None
                        fault_message = None
                        for elem in fault.detail.iter():
                            if "faultCode" in elem.tag:
                                fault_code = elem.text
                            elif "faultMessage" in elem.tag:
                                fault_message = elem.text
                        if fault_code or fault_message:
                            user_msg = f"TTN Error {fault_code or ''}: {fault_message or ''}".strip()
                            if user_msg.endswith(":"):
                                user_msg = user_msg[:-1]
                    except Exception as parse_err:
                        _logger.warning("Failed to parse SOAP Fault details: %s", parse_err)
                raise UserError(user_msg)
            except Exception as soap_error:
                _logger.error("SOAP call failed: %s", soap_error, exc_info=True)
                error_msg = str(soap_error)
                low = error_msg.lower()
                if "timeout" in low or "timed out" in low:
                    raise UserError(_("Request to Elfatoora timed out. Please check your network connection and try again."))
                if "connection" in low or "connect" in low:
                    raise UserError(_("Failed to connect to Elfatoora service. Please check the API URL and your network connection."))
                raise UserError(_("Elfatoora SOAP call failed: %s") % error_msg)

            if isinstance(response, str):
                import re

                tracking_number = None
                match = re.search(r"ID\s+(\d+)", response)
                if match:
                    tracking_number = match.group(1)
                return {"status": "ack", "tracking_number": tracking_number, "ttn_ref": None, "message": response}
            if isinstance(response, dict):
                return {
                    "status": response.get("status", "ack"),
                    "tracking_number": response.get("tracking_number"),
                    "ttn_ref": response.get("ttn_ref"),
                }
            return {
                "status": getattr(response, "status", "ack"),
                "tracking_number": getattr(response, "tracking_number", None),
                "ttn_ref": getattr(response, "ttn_ref", None),
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error("Submission failed: %s", e, exc_info=True)
            raise UserError(_("Elfatoora Submission Failed: %s") % str(e))

    def check_status(self, tracking_number=None, ttn_reference=None):
        if not self.client:
            raise UserError(_("SOAP Client not initialized."))

        try:
            if not self.config.soap_operation_check_status:
                raise UserError(_("SOAP operation name for checking status is not configured. Please set it in Elfatoora Configuration."))
            operation_name = self.config.soap_operation_check_status
            if not hasattr(self.client.service, operation_name):
                raise UserError(_("SOAP operation '%s' not found in WSDL. Please check the operation name in configuration.") % operation_name)

            operation = getattr(self.client.service, operation_name)

            try:
                EfactCriteria = None
                for ns_prefix in ["ns0", "tns", "ns1"]:
                    try:
                        EfactCriteria = self.client.get_type(f"{ns_prefix}:EfactCriteria")
                        if EfactCriteria:
                            break
                    except Exception:
                        continue

                criteria = EfactCriteria() if EfactCriteria else None
                criteria_dict = {}

                if ttn_reference:
                    if criteria:
                        criteria.generatedRef = ttn_reference
                    else:
                        criteria_dict["generatedRef"] = ttn_reference
                elif tracking_number:
                    try:
                        tid = int(tracking_number)
                    except (ValueError, TypeError):
                        _logger.warning("Tracking number %s is not a valid integer for idSaveEfact. Ignoring.", tracking_number)
                        tid = None
                    if tid:
                        if criteria:
                            criteria.idSaveEfact = tid
                        else:
                            criteria_dict["idSaveEfact"] = tid

                response = operation(self.api_username, self.api_password, self.matricule, criteria if criteria else criteria_dict)
            except Exception as e:
                _logger.error("Failed to call consultEfact: %s", e)
                raise UserError(_("Status Check Failed: %s") % str(e))

            if isinstance(response, dict):
                resp_data = response
            elif isinstance(response, list):
                if response:
                    resp_data = response[0]
                else:
                    return {"status": "pending", "message": _("Invoice not found in TTN system."), "processing_date": fields.Datetime.now()}
            else:
                resp_data = response

            def get_val(obj, key):
                if isinstance(obj, dict):
                    return obj.get(key)
                return getattr(obj, key, None)

            def _sanitize_date(dt_val):
                if not dt_val:
                    return False
                if isinstance(dt_val, datetime):
                    import pytz

                    if dt_val.tzinfo is not None:
                        return dt_val.astimezone(pytz.UTC).replace(tzinfo=None)
                    try:
                        tunisia_tz = pytz.timezone("Africa/Tunis")
                        local_dt = tunisia_tz.localize(dt_val)
                        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)
                    except Exception as e:
                        _logger.warning("Date sanitization failed: %s", e)
                        return dt_val
                return dt_val

            r_ttn_ref = get_val(resp_data, "generatedRef")
            r_id_save = get_val(resp_data, "idSaveEfact")
            r_xml = get_val(resp_data, "xmlContent")

            status = "pending"
            message = None
            if r_ttn_ref:
                status = "validated"
            else:
                acks = get_val(resp_data, "listAcknowlegments")
                if acks:
                    status = "rejected"
                    msgs = []
                    if not isinstance(acks, list):
                        acks = [acks]
                    for ack in acks:
                        desc = get_val(ack, "description") or get_val(ack, "code")
                        if desc:
                            msgs.append(str(desc))
                        sub_errors = get_val(ack, "errors")
                        if sub_errors:
                            if not isinstance(sub_errors, list):
                                sub_errors = [sub_errors]
                            for err in sub_errors:
                                err_desc = get_val(err, "errorDescription")
                                err_id = get_val(err, "errorId")
                                if err_desc:
                                    msgs.append(f"{err_desc} ({err_id})" if err_id else str(err_desc))
                                elif err_id:
                                    msgs.append(f"Error ID: {err_id}")
                    message = "; ".join(msgs)

            return {
                "status": status,
                "ttn_ref": r_ttn_ref,
                "tracking_number": str(r_id_save) if r_id_save else tracking_number,
                "v_date": _sanitize_date(get_val(resp_data, "dateProcess")) if status == "validated" else False,
                "processing_date": _sanitize_date(get_val(resp_data, "dateProcess")),
                "message": message,
                "xml_content": r_xml,
            }
        except UserError:
            raise
        except Exception as e:
            _logger.error("Status check failed: %s", e)
            raise UserError(_("Status Check Failed: %s") % str(e))

    def download_validated_xml(self, ttn_reference):
        if not self.client:
            raise UserError(_("SOAP Client not initialized."))

        try:
            if not self.config.soap_operation_check_status:
                raise UserError(_("SOAP operation name for checking status/downloading XML is not configured. Please set it in Elfatoora Configuration."))
            operation_name = self.config.soap_operation_check_status
            if not hasattr(self.client.service, operation_name):
                raise UserError(_("SOAP operation '%s' not found in WSDL. Please check the operation name in configuration.") % operation_name)

            operation = getattr(self.client.service, operation_name)
            try:
                EfactCriteria = None
                for ns_prefix in ["ns0", "tns", "ns1"]:
                    try:
                        EfactCriteria = self.client.get_type(f"{ns_prefix}:EfactCriteria")
                        if EfactCriteria:
                            break
                    except Exception:
                        continue

                if EfactCriteria:
                    criteria = EfactCriteria()
                    criteria.generatedRef = ttn_reference
                    response = operation(self.api_username, self.api_password, self.matricule, criteria)
                else:
                    criteria_dict = {"generatedRef": ttn_reference} if ttn_reference else {}
                    response = operation(self.api_username, self.api_password, self.matricule, criteria_dict)
            except Exception as e:
                _logger.error("Failed to download validated XML: %s", e)
                raise UserError(_("Download Validated XML Failed: %s") % str(e))

            if isinstance(response, str):
                return response.encode("utf-8")
            if isinstance(response, bytes):
                return response
            if hasattr(response, "xmlContent"):
                xml_content = response.xmlContent
                return xml_content if isinstance(xml_content, bytes) else xml_content.encode("utf-8")
            if hasattr(response, "xml_content"):
                xml_content = response.xml_content
                return xml_content if isinstance(xml_content, bytes) else xml_content.encode("utf-8")
            if hasattr(response, "content"):
                content = response.content
                return content if isinstance(content, bytes) else content.encode("utf-8")
            return str(response).encode("utf-8")
        except UserError:
            raise
        except Exception as e:
            _logger.error("Download validated XML failed: %s", e)
            raise UserError(_("Download Validated XML Failed: %s") % str(e))

