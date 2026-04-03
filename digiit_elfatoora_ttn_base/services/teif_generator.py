import logging
import re
from lxml import etree
from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TEIFGenerator:
    """
    Generates TEIF (Tunisian Electronic Invoice Format) v1.8.8 XML.
    """

    def __init__(self, invoice):
        self.invoice = invoice
        self.ns_map = {
            "xs": "http://www.w3.org/2001/XMLSchema",
            "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        }

        config = self.invoice.env["digiit_model_elfatoora.config"].search(
            [("company_id", "=", self.invoice.company_id.id)], limit=1
        )
        if not config or not config.company_vat:
            raise UserError(
                _("Company VAT/Matricule Fiscale is missing. Please set it in Elfatoora Configuration for company '%s'.")
                % self.invoice.company_id.name
            )
        self.company_vat = config.company_vat

    def _truncate_string(self, value, max_length, field_name=None):
        if value is None:
            return ""
        value = str(value)
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) > max_length:
            return value[:max_length]
        return value

    def _format_decimal_15_3(self, value):
        if value is None:
            return "0.000"
        formatted = f"{float(value):.3f}"
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
            if "." not in formatted:
                formatted += ".0"
            parts = formatted.split(".")
            if len(parts) == 2 and len(parts[1]) < 3:
                formatted = f"{parts[0]}.{parts[1].ljust(3, '0')}"
        return formatted

    def _format_decimal_15_2(self, value):
        if value is None:
            return "0.00"
        formatted = f"{float(value):.2f}"
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
            if "." not in formatted:
                formatted += ".0"
            parts = formatted.split(".")
            if len(parts) == 2 and len(parts[1]) < 2:
                formatted = f"{parts[0]}.{parts[1].ljust(2, '0')}"
        return formatted

    def _format_integer_6(self, value):
        if value is None:
            return "0"
        int_val = int(value)
        if int_val > 999999:
            _logger.warning("Integer value %s exceeds 6 digits, truncating", int_val)
            int_val = 999999
        return str(int_val)

    def generate_xml(self):
        try:
            self._check_required_fields()

            root = etree.Element("TEIF", controlingAgency="TTN", version="1.8.8")
            self._build_header(root)

            body = etree.SubElement(root, "InvoiceBody")
            self._build_bgm(body)
            self._build_dtm(body)
            self._build_partner_section(body)
            self._build_line_section(body)
            self._build_amount_section(body)
            self._build_tax_section(body)

            return etree.tostring(root, pretty_print=False, encoding="UTF-8", xml_declaration=True)
        except Exception as e:
            _logger.error("Error generating TEIF XML: %s", str(e))
            raise UserError(_("Failed to generate XML: %s") % str(e))

    def _check_required_fields(self):
        partner = self.invoice.partner_id

        if not self.invoice.name:
            raise UserError(_("Invoice number is missing. It is required for TEIF DocumentIdentifier."))
        if not self.invoice.invoice_date:
            raise UserError(_("Invoice date is missing. It is required for TEIF DateText (functionCode I-31)."))
        if not partner.vat:
            raise UserError(_("Customer VAT/Matricule is missing. It is required for TEIF PartnerIdentifier."))
        if not partner.country_id:
            raise UserError(_("Customer country is missing. It is required to determine the TEIF PartnerIdentifier type (I-01..I-04)."))
        product_lines = self.invoice.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        if not product_lines:
            raise UserError(_("At least one invoice line is required to generate TEIF (LinSection/Lin)."))

    def _get_partner_identifier_type(self, partner):
        country_code = partner.country_id.code if getattr(partner, "country_id", None) else None
        is_company = bool(getattr(partner, "is_company", False))
        if country_code == "TN":
            return "I-01" if is_company else "I-02"
        return "I-04" if is_company else "I-03"

    def _build_header(self, parent):
        header = etree.SubElement(parent, "InvoiceHeader")
        company_vat = self.company_vat or ""
        sender = etree.SubElement(header, "MessageSenderIdentifier", type="I-01")
        sender.text = self._truncate_string(company_vat, 35, "MessageSenderIdentifier")
        if not sender.text:
            raise UserError(_("Company VAT/Matricule Fiscale cannot be empty for MessageSenderIdentifier."))

        partner = self.invoice.partner_id
        customer_vat = partner.vat or ""
        rec_type = self._get_partner_identifier_type(partner)
        receiver = etree.SubElement(header, "MessageRecieverIdentifier", type=rec_type)
        receiver.text = self._truncate_string(customer_vat, 35, "MessageRecieverIdentifier")

    def _build_bgm(self, parent):
        bgm = etree.SubElement(parent, "Bgm")
        doc_id = etree.SubElement(bgm, "DocumentIdentifier")
        doc_id.text = self._truncate_string(self.invoice.name, 70, "DocumentIdentifier")

        type_code = "I-11"
        type_text = "Facture"
        if self.invoice.move_type == "out_refund":
            type_code = "I-12"
            type_text = "Avoir"

        type_code = self._truncate_string(type_code, 6, "DocumentType.code")
        type_text = self._truncate_string(type_text, 35, "DocumentType") or "Facture"
        doc_type = etree.SubElement(bgm, "DocumentType", code=type_code)
        doc_type.text = type_text

    def _build_dtm(self, parent):
        dtm = etree.SubElement(parent, "Dtm")
        inv_date = self.invoice.invoice_date
        if not inv_date:
            raise UserError(_("Invoice date is missing. It is required for TEIF DateText (functionCode I-31)."))
        date_elem = etree.SubElement(dtm, "DateText", format="ddMMyy", functionCode="I-31")
        date_elem.text = inv_date.strftime("%d%m%y")

    def _build_partner_section(self, parent):
        section = etree.SubElement(parent, "PartnerSection")
        self._add_partner_details(section, self.invoice.company_id, self.invoice.company_id.partner_id, "I-62")
        self._add_partner_details(section, self.invoice.company_id, self.invoice.partner_id, "I-64")

    def _add_partner_details(self, parent, company, partner, func_code):
        details = etree.SubElement(parent, "PartnerDetails", functionCode=func_code)
        nad = etree.SubElement(details, "Nad")

        ident = partner.vat or ""
        ident_type = "I-01"
        if func_code == "I-62":
            ident = self.company_vat or ""
            ident_type = "I-01"
        elif func_code == "I-64":
            ident_type = self._get_partner_identifier_type(partner)

        p_id = etree.SubElement(nad, "PartnerIdentifier", type=ident_type)
        p_id.text = self._truncate_string(ident, 35, "PartnerIdentifier")

    def _map_tax_to_teif(self, tax):
        default_code = "I-1602"
        default_label = "TVA"
        if not tax:
            return default_code, default_label

        name = (tax.name or "").lower()
        description = (tax.description or "").lower()
        tax_group_name = (tax.tax_group_id.name or "").lower() if tax.tax_group_id else ""
        search_text = f"{name} {description} {tax_group_name}".lower()

        timbre_keywords = [
            "timbre",
            "stamp",
            "droit de timbre",
            "stamp duty",
            "fiscal stamp",
            "timbre fiscal",
            "fiscal timbre",
        ]
        if any(keyword in search_text for keyword in timbre_keywords):
            return "I-1601", "Droit de timbre"

        tva_keywords = [
            "tva",
            "vat",
            "value added tax",
            "taxe sur la valeur ajoutée",
            "taxe valeur ajoutée",
            "added tax",
        ]
        if any(keyword in search_text for keyword in tva_keywords):
            return "I-1602", "TVA"

        return "I-1603", "Autre"

    def _build_line_section(self, parent):
        lin_sec = etree.SubElement(parent, "LinSection")
        for i, line in enumerate(self.invoice.invoice_line_ids, 1):
            if line.display_type != "product":
                continue

            lin = etree.SubElement(lin_sec, "Lin")
            etree.SubElement(lin, "ItemIdentifier").text = self._format_integer_6(i)

            imd = etree.SubElement(lin, "LinImd", lang="fr")
            item_code = self._truncate_string(line.product_id.default_code or "N/A", 35, "ItemCode")
            etree.SubElement(imd, "ItemCode").text = item_code

            description = (line.name or "").replace("\n", " ").replace("\r", " ")
            etree.SubElement(imd, "ItemDescription").text = self._truncate_string(description, 500, "ItemDescription")

            qty = etree.SubElement(lin, "LinQty")
            q_elem = etree.SubElement(qty, "Quantity", measurementUnit="UNIT")
            q_elem.text = self._format_decimal_15_3(line.quantity)

            lin_tax = etree.SubElement(lin, "LinTax")
            tax = None
            if line.tax_ids:
                percentage_taxes = line.tax_ids.filtered(lambda t: t.amount_type != "fixed")
                if percentage_taxes:
                    tax = percentage_taxes[0]

            tax_code, tax_name = self._map_tax_to_teif(tax)
            tax_rate = float(tax.amount) if tax else 0.0
            tax_code = self._truncate_string(tax_code, 6, "TaxTypeCode")
            tax_rate_str = self._truncate_string(self._format_decimal_15_2(tax_rate), 5, "TaxRate") or "0"
            etree.SubElement(lin_tax, "TaxTypeName", code=tax_code).text = self._truncate_string(tax_name, 200, "TaxTypeName")
            details = etree.SubElement(lin_tax, "TaxDetails")
            etree.SubElement(details, "TaxRate").text = tax_rate_str

            if getattr(line, "discount", 0):
                try:
                    discount_val = float(line.discount)
                except (TypeError, ValueError):
                    discount_val = 0.0
                if discount_val:
                    lin_alc = etree.SubElement(lin, "LinAlc")
                    etree.SubElement(lin_alc, "Alc", allowanceCode="I-151")
                    pcd = etree.SubElement(lin_alc, "Pcd")
                    pct_str = self._truncate_string(self._format_decimal_15_2(discount_val), 5, "DiscountPercentage") or "0"
                    etree.SubElement(pcd, "Percentage").text = pct_str
                    etree.SubElement(pcd, "PercentageBasis").text = self._truncate_string(_("Montant HT ligne"), 35, "DiscountPercentageBasis")

            moa_sec = etree.SubElement(lin, "LinMoa")
            currency = self.invoice.currency_id.name
            self._add_moa(moa_sec, "I-183", line.price_unit, currency)
            self._add_moa(moa_sec, "I-171", line.price_subtotal, currency)

    def _build_amount_section(self, parent):
        moa_sec = etree.SubElement(parent, "InvoiceMoa")
        currency = self.invoice.currency_id.name

        self._add_moa_details(moa_sec, "I-176", self.invoice.amount_untaxed, currency)
        self._add_moa_details_with_description(moa_sec, "I-180", self.invoice.amount_total, currency)
        self._add_moa_details(moa_sec, "I-181", self.invoice.amount_tax, currency)

        tax_lines = self.invoice.line_ids.filtered(lambda l: l.display_type == "tax")
        if tax_lines:
            percentage_tax_lines = tax_lines.filtered(lambda l: l.tax_line_id and l.tax_line_id.amount_type != "fixed")
            total_tax_base = sum(abs(line.tax_base_amount) for line in percentage_tax_lines) if percentage_tax_lines else 0.0
        else:
            total_tax_base = self.invoice.amount_untaxed
        self._add_moa_details(moa_sec, "I-182", total_tax_base, currency)

    def _build_tax_section(self, parent):
        tax_sec = etree.SubElement(parent, "InvoiceTax")
        currency = self.invoice.currency_id.name
        tax_lines = self.invoice.line_ids.filtered(lambda l: l.display_type == "tax")

        if not tax_lines:
            details = etree.SubElement(tax_sec, "InvoiceTaxDetails")
            tax_elem = etree.SubElement(details, "Tax")
            etree.SubElement(tax_elem, "TaxTypeName", code="I-1602").text = "TVA"
            rate = etree.SubElement(tax_elem, "TaxDetails")
            etree.SubElement(rate, "TaxRate").text = "0"
            self._add_moa_details(details, "I-177", self.invoice.amount_untaxed, currency)
            self._add_moa_details(details, "I-178", 0.0, currency)
            return

        for tax_line in tax_lines:
            details = etree.SubElement(tax_sec, "InvoiceTaxDetails")
            tax_elem = etree.SubElement(details, "Tax")
            tax = tax_line.tax_line_id
            tax_code, tax_name = self._map_tax_to_teif(tax)
            tax_code = self._truncate_string(tax_code, 6, "TaxTypeCode")
            etree.SubElement(tax_elem, "TaxTypeName", code=tax_code).text = self._truncate_string(tax_name, 200, "TaxTypeName")

            rate_val = 0.0 if (tax and tax.amount_type == "fixed") else (tax.amount if tax else 0.0)
            rate = etree.SubElement(tax_elem, "TaxDetails")
            rate_str = self._truncate_string(self._format_decimal_15_2(rate_val), 5, "TaxRate") or "0"
            etree.SubElement(rate, "TaxRate").text = rate_str

            if tax and tax.amount_type == "fixed":
                self._add_moa_details(details, "I-178", abs(tax_line.amount_currency), currency)
            else:
                self._add_moa_details(details, "I-177", abs(tax_line.tax_base_amount), currency)
                self._add_moa_details(details, "I-178", abs(tax_line.amount_currency), currency)

    def _add_moa(self, parent, type_code, amount, currency):
        details = etree.SubElement(parent, "MoaDetails")
        type_code = self._truncate_string(type_code, 6, "AmountTypeCode")
        moa = etree.SubElement(details, "Moa", amountTypeCode=type_code, currencyCodeList="ISO_4217")
        etree.SubElement(moa, "Amount", currencyIdentifier=currency).text = self._format_decimal_15_3(amount)

    def _add_moa_details(self, parent, type_code, amount, currency):
        details = etree.SubElement(parent, "AmountDetails")
        type_code = self._truncate_string(type_code, 6, "AmountTypeCode")
        moa = etree.SubElement(details, "Moa", amountTypeCode=type_code, currencyCodeList="ISO_4217")
        etree.SubElement(moa, "Amount", currencyIdentifier=currency).text = self._format_decimal_15_3(amount)

    def _add_moa_details_with_description(self, parent, type_code, amount, currency):
        details = etree.SubElement(parent, "AmountDetails")
        moa = etree.SubElement(details, "Moa", amountTypeCode=type_code, currencyCodeList="ISO_4217")
        etree.SubElement(moa, "Amount", currencyIdentifier=currency).text = "{:.3f}".format(amount)

