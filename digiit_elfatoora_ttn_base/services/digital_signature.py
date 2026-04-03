import base64
import hashlib
import logging
import io
from datetime import datetime, timezone

from lxml import etree
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, ec
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import ExtensionOID, AuthorityInformationAccessOID, NameOID

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DigitalSignature:
    """
    Handles XAdES digital signature generation using cryptography and lxml.
    """

    NS_DS = "http://www.w3.org/2000/09/xmldsig#"
    NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
    NS_EC = "http://www.w3.org/2001/10/xml-exc-c14n#"

    def __init__(self, p12_content=None, p12_password=None):
        self.p12_content = p12_content
        self.p12_password = p12_password
        self.private_key = None
        self.certificate = None
        if self.p12_content:
            self._load_certificate()

    def _load_certificate(self):
        try:
            p12 = serialization.pkcs12.load_key_and_certificates(
                self.p12_content,
                self.p12_password.encode() if self.p12_password else None,
                default_backend(),
            )
            self.private_key = p12[0]
            self.certificate = p12[1]
            if not self.private_key or not self.certificate:
                raise ValueError(_("Could not extract key or certificate from P12 file"))
        except Exception as e:
            _logger.error(f"Certificate loading error: {e}")
            raise ValueError(_("Failed to load certificate: %s") % str(e))

    def _encode_issuer_serial_v2(self, certificate):
        try:
            issuer_name_der = certificate.issuer.public_bytes(default_backend())

            serial_bytes = certificate.serial_number.to_bytes((certificate.serial_number.bit_length() + 7) // 8, "big")
            serial_der = b"\x02" + self._encode_der_length(len(serial_bytes)) + serial_bytes

            issuer_general_name = b"\xA4" + self._encode_der_length(len(issuer_name_der)) + issuer_name_der
            issuer_general_names = b"\x30" + self._encode_der_length(len(issuer_general_name)) + issuer_general_name

            content = issuer_general_names + serial_der
            result = b"\x30" + self._encode_der_length(len(content)) + content

            return base64.b64encode(result).decode()
        except Exception as e:
            _logger.error(f"Failed to encode IssuerSerialV2: {e}", exc_info=True)
            raise UserError(_(f"Certificate encoding failed: {e}"))

    def _encode_der_length(self, length):
        if length < 128:
            return bytes([length])
        length_bytes = length.to_bytes((length.bit_length() + 7) // 8, "big")
        return bytes([0x80 | len(length_bytes)]) + length_bytes

    # NOTE: P12 signing remains present in code, but is not exposed by base config anymore.
    def sign_xml(self, xml_content):
        try:
            parser = etree.XMLParser(remove_blank_text=True)
            root = etree.fromstring(xml_content, parser)

            c14n_method = "http://www.w3.org/2001/10/xml-exc-c14n#"
            output = io.BytesIO()
            root.getroottree().write_c14n(output, exclusive=True)
            canon_xml = output.getvalue()

            digest_method = "http://www.w3.org/2001/04/xmlenc#sha256"
            doc_digest = base64.b64encode(hashlib.sha256(canon_xml).digest()).decode()

            signature = etree.SubElement(root, f"{{{self.NS_DS}}}Signature", nsmap={"ds": self.NS_DS})
            signature.set("Id", "SigFrs")

            signed_info = etree.SubElement(signature, f"{{{self.NS_DS}}}SignedInfo")
            etree.SubElement(signed_info, f"{{{self.NS_DS}}}CanonicalizationMethod", Algorithm=c14n_method)
            etree.SubElement(
                signed_info,
                f"{{{self.NS_DS}}}SignatureMethod",
                Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            )

            ref_doc = etree.SubElement(signed_info, f"{{{self.NS_DS}}}Reference", Id="r-id-frs", Type="", URI="")
            trans = etree.SubElement(ref_doc, f"{{{self.NS_DS}}}Transforms")
            xpath1 = etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
            xpath1_elem = etree.SubElement(xpath1, f"{{{self.NS_DS}}}XPath")
            xpath1_elem.text = "not(ancestor-or-self::ds:Signature)"
            xpath2 = etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
            xpath2_elem = etree.SubElement(xpath2, f"{{{self.NS_DS}}}XPath")
            xpath2_elem.text = "not(ancestor-or-self::RefTtnVal)"
            etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm=c14n_method)

            etree.SubElement(ref_doc, f"{{{self.NS_DS}}}DigestMethod", Algorithm=digest_method)
            etree.SubElement(ref_doc, f"{{{self.NS_DS}}}DigestValue").text = doc_digest

            object_elem = etree.Element(f"{{{self.NS_DS}}}Object")
            qp = etree.SubElement(
                object_elem,
                f"{{{self.NS_XADES}}}QualifyingProperties",
                Target="#SigFrs",
                nsmap={"xades": self.NS_XADES, "ds": self.NS_DS},
            )
            sp = etree.SubElement(qp, f"{{{self.NS_XADES}}}SignedProperties", Id="xades-SigFrs")
            ssp = etree.SubElement(sp, f"{{{self.NS_XADES}}}SignedSignatureProperties")
            etree.SubElement(ssp, f"{{{self.NS_XADES}}}SigningTime").text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            cert_digest = base64.b64encode(hashlib.sha1(self.certificate.public_bytes(serialization.Encoding.DER)).digest()).decode()
            sc = etree.SubElement(ssp, f"{{{self.NS_XADES}}}SigningCertificateV2")
            cert_elem = etree.SubElement(sc, f"{{{self.NS_XADES}}}Cert")
            cert_digest_elem = etree.SubElement(cert_elem, f"{{{self.NS_XADES}}}CertDigest")
            etree.SubElement(cert_digest_elem, f"{{{self.NS_DS}}}DigestMethod", Algorithm="http://www.w3.org/2000/09/xmldsig#sha1")
            etree.SubElement(cert_digest_elem, f"{{{self.NS_DS}}}DigestValue").text = cert_digest
            issuer_serial_v2_value = self._encode_issuer_serial_v2(self.certificate)
            etree.SubElement(cert_elem, f"{{{self.NS_XADES}}}IssuerSerialV2").text = issuer_serial_v2_value

            spi = etree.SubElement(ssp, f"{{{self.NS_XADES}}}SignaturePolicyIdentifier")
            sp_id = etree.SubElement(spi, f"{{{self.NS_XADES}}}SignaturePolicyId")
            sig_id_elem = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyId")
            etree.SubElement(sig_id_elem, f"{{{self.NS_XADES}}}Identifier", Qualifier="OIDasURN").text = "urn:2.16.788.1.2.1"
            etree.SubElement(sig_id_elem, f"{{{self.NS_XADES}}}Description").text = "Politique de signature de la facture electronique"
            sp_hash = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyHash")
            etree.SubElement(sp_hash, f"{{{self.NS_DS}}}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
            etree.SubElement(sp_hash, f"{{{self.NS_DS}}}DigestValue").text = "3J1oMkha+OAlm9hBNCcAS+/nbKokG8Gf9N3XPipP7yg="
            sp_qualifiers = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyQualifiers")
            sp_qualifier = etree.SubElement(sp_qualifiers, f"{{{self.NS_XADES}}}SigPolicyQualifier")
            etree.SubElement(sp_qualifier, f"{{{self.NS_XADES}}}SPURI").text = (
                "http://www.tradenet.com.tn/portal/telechargerTelechargement?lien=Politique_de_Signature_de_la_facture_electronique.pdf"
            )

            sp_output = io.BytesIO()
            etree.ElementTree(sp).write_c14n(sp_output, exclusive=True)
            sp_digest = base64.b64encode(hashlib.sha256(sp_output.getvalue()).digest()).decode()
            ref_props = etree.SubElement(
                signed_info,
                f"{{{self.NS_DS}}}Reference",
                Type="http://uri.etsi.org/01903#SignedProperties",
                URI="#xades-SigFrs",
            )
            trans_props = etree.SubElement(ref_props, f"{{{self.NS_DS}}}Transforms")
            etree.SubElement(trans_props, f"{{{self.NS_DS}}}Transform", Algorithm=c14n_method)
            etree.SubElement(ref_props, f"{{{self.NS_DS}}}DigestMethod", Algorithm=digest_method)
            etree.SubElement(ref_props, f"{{{self.NS_DS}}}DigestValue").text = sp_digest

            si_output = io.BytesIO()
            etree.ElementTree(signed_info).write_c14n(si_output, exclusive=True)
            signature_val = self.private_key.sign(si_output.getvalue(), padding.PKCS1v15(), hashes.SHA256())
            sig_b64 = base64.b64encode(signature_val).decode()
            etree.SubElement(signature, f"{{{self.NS_DS}}}SignatureValue", Id="value-SigFrs").text = sig_b64

            ki = etree.SubElement(signature, f"{{{self.NS_DS}}}KeyInfo")
            x509_data = etree.SubElement(ki, f"{{{self.NS_DS}}}X509Data")
            cert_b64 = base64.b64encode(self.certificate.public_bytes(serialization.Encoding.DER)).decode()
            etree.SubElement(x509_data, f"{{{self.NS_DS}}}X509Certificate").text = cert_b64

            signature.append(object_elem)
            etree.cleanup_namespaces(root)
            return etree.tostring(root, pretty_print=False, encoding="UTF-8", xml_declaration=True)
        except Exception as e:
            _logger.error(f"Signing failed: {e}")
            raise UserError(_("XML Signing failed: %s") % str(e))

    def prepare_remote_signing(self, xml_content, public_cert_b64):
        try:
            cert_bytes = base64.b64decode(public_cert_b64)
            certificate = x509.load_der_x509_certificate(cert_bytes, default_backend())

            parser = etree.XMLParser(remove_blank_text=True)
            root = etree.fromstring(xml_content, parser)

            c14n_method = "http://www.w3.org/2001/10/xml-exc-c14n#"
            output = io.BytesIO()
            root.getroottree().write_c14n(output, exclusive=True)
            doc_digest = base64.b64encode(hashlib.sha256(output.getvalue()).digest()).decode()

            digest_method = "http://www.w3.org/2001/04/xmlenc#sha256"

            signature = etree.SubElement(root, f"{{{self.NS_DS}}}Signature", nsmap={"ds": self.NS_DS})
            signature.set("Id", "SigFrs")

            signed_info = etree.SubElement(signature, f"{{{self.NS_DS}}}SignedInfo")
            etree.SubElement(signed_info, f"{{{self.NS_DS}}}CanonicalizationMethod", Algorithm=c14n_method)
            etree.SubElement(
                signed_info,
                f"{{{self.NS_DS}}}SignatureMethod",
                Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            )

            ref_doc = etree.SubElement(signed_info, f"{{{self.NS_DS}}}Reference", Id="r-id-frs", Type="", URI="")
            trans = etree.SubElement(ref_doc, f"{{{self.NS_DS}}}Transforms")
            xpath1 = etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
            xpath1_elem = etree.SubElement(xpath1, f"{{{self.NS_DS}}}XPath")
            xpath1_elem.text = "not(ancestor-or-self::ds:Signature)"
            xpath2 = etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
            xpath2_elem = etree.SubElement(xpath2, f"{{{self.NS_DS}}}XPath")
            xpath2_elem.text = "not(ancestor-or-self::RefTtnVal)"
            etree.SubElement(trans, f"{{{self.NS_DS}}}Transform", Algorithm=c14n_method)

            etree.SubElement(ref_doc, f"{{{self.NS_DS}}}DigestMethod", Algorithm=digest_method)
            etree.SubElement(ref_doc, f"{{{self.NS_DS}}}DigestValue").text = doc_digest

            object_elem = etree.Element(f"{{{self.NS_DS}}}Object")
            qp = etree.SubElement(
                object_elem,
                f"{{{self.NS_XADES}}}QualifyingProperties",
                Target="#SigFrs",
                nsmap={"xades": self.NS_XADES, "ds": self.NS_DS},
            )
            sp = etree.SubElement(qp, f"{{{self.NS_XADES}}}SignedProperties", Id="xades-SigFrs", nsmap={"xades": self.NS_XADES, "ds": self.NS_DS})
            ssp = etree.SubElement(sp, f"{{{self.NS_XADES}}}SignedSignatureProperties")
            etree.SubElement(ssp, f"{{{self.NS_XADES}}}SigningTime").text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            cert_digest = base64.b64encode(hashlib.sha1(certificate.public_bytes(serialization.Encoding.DER)).digest()).decode()
            sc = etree.SubElement(ssp, f"{{{self.NS_XADES}}}SigningCertificateV2")
            cert_elem = etree.SubElement(sc, f"{{{self.NS_XADES}}}Cert")
            cert_digest_elem = etree.SubElement(cert_elem, f"{{{self.NS_XADES}}}CertDigest")
            etree.SubElement(cert_digest_elem, f"{{{self.NS_DS}}}DigestMethod", Algorithm="http://www.w3.org/2000/09/xmldsig#sha1")
            etree.SubElement(cert_digest_elem, f"{{{self.NS_DS}}}DigestValue").text = cert_digest
            issuer_serial_v2_value = self._encode_issuer_serial_v2(certificate)
            etree.SubElement(cert_elem, f"{{{self.NS_XADES}}}IssuerSerialV2").text = issuer_serial_v2_value

            spi = etree.SubElement(ssp, f"{{{self.NS_XADES}}}SignaturePolicyIdentifier")
            sp_id = etree.SubElement(spi, f"{{{self.NS_XADES}}}SignaturePolicyId")
            sig_id_elem = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyId")
            etree.SubElement(sig_id_elem, f"{{{self.NS_XADES}}}Identifier", Qualifier="OIDasURN").text = "urn:2.16.788.1.2.1"
            etree.SubElement(sig_id_elem, f"{{{self.NS_XADES}}}Description").text = "Politique de signature de la facture electronique"
            sp_hash = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyHash")
            etree.SubElement(sp_hash, f"{{{self.NS_DS}}}DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
            etree.SubElement(sp_hash, f"{{{self.NS_DS}}}DigestValue").text = "3J1oMkha+OAlm9hBNCcAS+/nbKokG8Gf9N3XPipP7yg="
            sp_qualifiers = etree.SubElement(sp_id, f"{{{self.NS_XADES}}}SigPolicyQualifiers")
            sp_qualifier = etree.SubElement(sp_qualifiers, f"{{{self.NS_XADES}}}SigPolicyQualifier")
            etree.SubElement(sp_qualifier, f"{{{self.NS_XADES}}}SPURI").text = (
                "http://www.tradenet.com.tn/portal/telechargerTelechargement?lien=Politique_de_Signature_de_la_facture_electronique.pdf"
            )

            sp_output = io.BytesIO()
            etree.ElementTree(sp).write_c14n(sp_output, exclusive=True)
            sp_digest = base64.b64encode(hashlib.sha256(sp_output.getvalue()).digest()).decode()

            ref_props = etree.SubElement(signed_info, f"{{{self.NS_DS}}}Reference", Type="http://uri.etsi.org/01903#SignedProperties", URI="#xades-SigFrs")
            trans_props = etree.SubElement(ref_props, f"{{{self.NS_DS}}}Transforms")
            etree.SubElement(trans_props, f"{{{self.NS_DS}}}Transform", Algorithm=c14n_method)
            etree.SubElement(ref_props, f"{{{self.NS_DS}}}DigestMethod", Algorithm=digest_method)
            etree.SubElement(ref_props, f"{{{self.NS_DS}}}DigestValue").text = sp_digest

            sig_value_elem = etree.SubElement(signature, f"{{{self.NS_DS}}}SignatureValue", Id="value-SigFrs")
            sig_value_elem.text = ""

            ki = etree.SubElement(signature, f"{{{self.NS_DS}}}KeyInfo")
            x509_data = etree.SubElement(ki, f"{{{self.NS_DS}}}X509Data")
            cert_b64 = base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()
            etree.SubElement(x509_data, f"{{{self.NS_DS}}}X509Certificate").text = cert_b64

            signature.append(object_elem)
            etree.cleanup_namespaces(root)

            si_output = io.BytesIO()
            etree.ElementTree(signed_info).write_c14n(si_output, exclusive=True)
            digest_to_sign = hashlib.sha256(si_output.getvalue()).digest()

            context = {"root": root, "signature_elem": signature, "cert_bytes": certificate.public_bytes(serialization.Encoding.DER), "object_elem": object_elem}
            return digest_to_sign, context
        except Exception as e:
            _logger.error(f"Remote signing prepare failed: {e}")
            raise UserError(_("Failed to prepare XML for remote signing: %s") % str(e))

    def embed_signature(self, xml_content, signature_value_b64):
        try:
            xml_bytes = xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content
            placeholder = b'<ds:SignatureValue Id="value-SigFrs"></ds:SignatureValue>'
            replacement = b'<ds:SignatureValue Id="value-SigFrs">' + signature_value_b64.encode("utf-8") + b"</ds:SignatureValue>"
            if placeholder not in xml_bytes:
                placeholder = b'<ds:SignatureValue Id="value-SigFrs"/>'
                replacement = b'<ds:SignatureValue Id="value-SigFrs">' + signature_value_b64.encode("utf-8") + b"</ds:SignatureValue>"
            if placeholder not in xml_bytes:
                raise ValueError("SignatureValue placeholder not found in XML. Cannot embed signature without re-parsing.")
            return xml_bytes.replace(placeholder, replacement, 1)
        except Exception as e:
            _logger.error(f"Embed signature failed: {e}")
            raise UserError(_("Failed to embed signature into XML: %s") % str(e))

    # Verification functions (kept identical in behavior to original)
    def _parse_all_signatures(self, xml_content):
        try:
            if isinstance(xml_content, str):
                xml_content = xml_content.encode("utf-8")
            parser = etree.XMLParser(remove_blank_text=True)
            root = etree.fromstring(xml_content, parser)
            ns = {"ds": self.NS_DS}
            signatures = root.findall(".//ds:Signature", namespaces=ns)
            if not signatures:
                raise UserError(_("No XML digital signature (ds:Signature) was found in the document."))
            return root, signatures
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"XML parsing failed during signature verification: {e}", exc_info=True)
            raise UserError(_("Failed to parse XML for signature verification: %s") % str(e))

    def _extract_certificate_from_signature(self, signature):
        ns = {"ds": self.NS_DS}
        x509_cert_node = signature.find(".//ds:X509Certificate", namespaces=ns)
        if x509_cert_node is None or not (x509_cert_node.text or "").strip():
            raise UserError(_("No X509 certificate was found inside the XML signature."))
        try:
            cert_der = base64.b64decode(x509_cert_node.text.encode("utf-8"))
            return x509.load_der_x509_certificate(cert_der, default_backend())
        except Exception as e:
            _logger.error(f"Failed to load X509 certificate from XML: {e}", exc_info=True)
            raise UserError(_("Failed to load X509 certificate from XML: %s") % str(e))

    def _extract_signing_time(self, signature):
        ns = {"ds": self.NS_DS, "xades": self.NS_XADES}
        signing_time_node = signature.find(".//xades:SigningTime", namespaces=ns)
        if signing_time_node is None or not (signing_time_node.text or "").strip():
            return None
        try:
            signing_time_str = signing_time_node.text.strip()
            if signing_time_str.endswith("Z"):
                signing_time_str = signing_time_str[:-1] + "+00:00"
            signing_time = datetime.fromisoformat(signing_time_str)
            if signing_time.tzinfo is None:
                signing_time = signing_time.replace(tzinfo=timezone.utc)
            else:
                signing_time = signing_time.astimezone(timezone.utc)
            return signing_time
        except (ValueError, AttributeError) as e:
            _logger.warning(f"Failed to parse SigningTime '{signing_time_node.text}': {e}")
            return None

    def _check_certificate_validity_period(self, certificate, signing_time):
        if signing_time is None:
            raise ValueError(_("Signing time is required for certificate validity verification"))

        reference_time = signing_time
        not_before = certificate.not_valid_before_utc if hasattr(certificate, "not_valid_before_utc") else certificate.not_valid_before
        not_after = certificate.not_valid_after_utc if hasattr(certificate, "not_valid_after_utc") else certificate.not_valid_after

        if not_before.tzinfo is None:
            not_before = not_before.replace(tzinfo=timezone.utc)
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=timezone.utc)
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)

        if reference_time < not_before:
            raise UserError(
                _("The signing certificate is not yet valid (NotBefore: %s, signing time: %s).") % (not_before.isoformat(), reference_time.isoformat())
            )
        if reference_time > not_after:
            raise UserError(
                _("The signing certificate has expired (NotAfter: %s, signing time: %s).") % (not_after.isoformat(), reference_time.isoformat())
            )
        return not_before, not_after

    _TRUSTED_ANCE_ORGS = {
        "AGENCE NATIONALE DE CERTIFICATION ELECTRONIQUE",
        "National Digital Certification Agency",
    }

    _TRUSTED_INTERMEDIATES = {
        "CN=TunTrust Qualified CA Client ECC G1,O=AGENCE NATIONALE DE CERTIFICATION ELECTRONIQUE,C=TN",
        "CN=TunTrust CA QSign1,O=AGENCE NATIONALE DE CERTIFICATION ELECTRONIQUE,C=TN",
    }

    def _is_trusted_root(self, certificate):
        try:
            cert_subject = certificate.subject.rfc4514_string()
            cert_issuer = certificate.issuer.rfc4514_string()
        except Exception:
            return False
        if cert_subject != cert_issuer:
            return False
        try:
            org_values = [attr.value for attr in certificate.issuer if attr.oid == NameOID.ORGANIZATION_NAME]
            country_values = [attr.value for attr in certificate.issuer if attr.oid == NameOID.COUNTRY_NAME]
        except Exception:
            return False
        return any(org in self._TRUSTED_ANCE_ORGS for org in org_values) and "TN" in country_values

    def _is_trusted_intermediate(self, certificate):
        try:
            cert_subject = certificate.subject.rfc4514_string()
        except Exception:
            return False
        return cert_subject in self._TRUSTED_INTERMEDIATES

    def _load_issuer_from_aia(self, certificate):
        try:
            aia_ext = certificate.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        except x509.ExtensionNotFound:
            return None
        except Exception as e:
            _logger.warning(f"Failed to read AIA extension: {e}", exc_info=True)
            return None

        ca_issuer_urls = [
            d.access_location.value for d in aia_ext if getattr(d, "access_method", None) == AuthorityInformationAccessOID.CA_ISSUERS
        ]
        if not ca_issuer_urls:
            return None

        try:
            import requests  # type: ignore
        except Exception as e:
            _logger.warning(f"requests library not available for AIA download: {e}")
            return None

        for url in ca_issuer_urls:
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code != 200:
                    continue
                data = resp.content
                try:
                    return x509.load_der_x509_certificate(data, default_backend())
                except Exception:
                    try:
                        return x509.load_pem_x509_certificate(data, default_backend())
                    except Exception:
                        continue
            except Exception as e:
                _logger.warning(f"Failed to download issuer certificate from {url}: {e}", exc_info=True)
        return None

    def _verify_certificate_signature(self, certificate, issuer_cert):
        issuer_public_key = issuer_cert.public_key()
        from cryptography.hazmat.primitives.asymmetric import rsa

        if isinstance(issuer_public_key, rsa.RSAPublicKey):
            issuer_public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                padding.PKCS1v15(),
                certificate.signature_hash_algorithm,
            )
        elif isinstance(issuer_public_key, ec.EllipticCurvePublicKey):
            issuer_public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                ec.ECDSA(certificate.signature_hash_algorithm),
            )
        else:
            try:
                issuer_public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    certificate.signature_hash_algorithm,
                )
            except Exception:
                issuer_public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    ec.ECDSA(certificate.signature_hash_algorithm),
                )

    def _check_chain_of_trust(self, certificate):
        chain_info = []
        current_cert = certificate
        max_depth = 10
        depth = 0

        while depth < max_depth:
            depth += 1
            chain_info.append(
                {
                    "cert": current_cert,
                    "subject": current_cert.subject.rfc4514_string(),
                    "issuer": current_cert.issuer.rfc4514_string(),
                }
            )
            if self._is_trusted_root(current_cert):
                return "OK", current_cert, chain_info

            issuer_cert = self._load_issuer_from_aia(current_cert)
            if not issuer_cert:
                if self._is_trusted_intermediate(current_cert):
                    return "OK", current_cert, chain_info
                return "INCONCLUSIVE", None, chain_info

            try:
                self._verify_certificate_signature(current_cert, issuer_cert)
            except Exception as e:
                _logger.error(f"Certificate chain verification failed at depth {depth}: {e}", exc_info=True)
                return "FAILED", issuer_cert, chain_info

            current_cert = issuer_cert

        return "INCONCLUSIVE", None, chain_info

    def _check_crl_revocation(self, certificate):
        try:
            crl_dp_ext = certificate.extensions.get_extension_for_oid(ExtensionOID.CRL_DISTRIBUTION_POINTS).value
        except x509.ExtensionNotFound:
            return "INCONCLUSIVE"
        except Exception as e:
            _logger.warning(f"Failed to read CRL distribution points: {e}", exc_info=True)
            return "INCONCLUSIVE"

        crl_urls = []
        for dp in crl_dp_ext:
            if dp.full_name:
                for name in dp.full_name:
                    if isinstance(name, x509.UniformResourceIdentifier):
                        crl_urls.append(name.value)
        if not crl_urls:
            return "INCONCLUSIVE"

        try:
            import requests  # type: ignore
        except Exception as e:
            _logger.warning(f"requests library not available for CRL download: {e}")
            return "INCONCLUSIVE"

        for crl_url in crl_urls:
            try:
                resp = requests.get(crl_url, timeout=5)
                if resp.status_code != 200:
                    continue
                crl = x509.load_der_x509_crl(resp.content, default_backend())
                revoked_cert = crl.get_revoked_certificate_by_serial_number(certificate.serial_number)
                if revoked_cert is not None:
                    return "REVOKED"
                return "OK"
            except Exception as e:
                _logger.warning(f"CRL check failed for {crl_url}: {e}", exc_info=True)
                continue
        return "INCONCLUSIVE"

    def _check_ocsp_revocation(self, certificate, issuer_cert):
        if not issuer_cert:
            return "INCONCLUSIVE"

        try:
            aia_ext = certificate.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        except x509.ExtensionNotFound:
            return "INCONCLUSIVE"
        except Exception as e:
            _logger.warning(f"Failed to read AIA extension for OCSP: {e}", exc_info=True)
            return "INCONCLUSIVE"

        ocsp_urls = [
            d.access_location.value for d in aia_ext if getattr(d, "access_method", None) == AuthorityInformationAccessOID.OCSP
        ]
        if not ocsp_urls:
            return "INCONCLUSIVE"

        try:
            import requests  # type: ignore
            from cryptography.x509 import ocsp as ocsp_mod
        except Exception as e:
            _logger.warning(f"OCSP dependencies not available: {e}")
            return "INCONCLUSIVE"

        builder = ocsp_mod.OCSPRequestBuilder().add_certificate(certificate, issuer_cert, hashes.SHA256())
        req = builder.build()
        headers = {"Content-Type": "application/ocsp-request", "Accept": "application/ocsp-response"}

        for ocsp_url in ocsp_urls:
            try:
                resp = requests.post(ocsp_url, data=req.public_bytes(serialization.Encoding.DER), headers=headers, timeout=10)
                if resp.status_code != 200:
                    continue
                ocsp_response = ocsp_mod.load_der_ocsp_response(resp.content)
                if ocsp_response.response_status != ocsp_mod.OCSPResponseStatus.SUCCESSFUL:
                    continue
                status = ocsp_response.certificate_status
                if status == ocsp_mod.OCSPCertStatus.REVOKED:
                    return "REVOKED"
                if status == ocsp_mod.OCSPCertStatus.GOOD:
                    return "OK"
            except Exception as e:
                _logger.warning(f"OCSP check failed for {ocsp_url}: {e}", exc_info=True)
                continue
        return "INCONCLUSIVE"

    def _verify_signature_value(self, root, signature, certificate):
        ns = {"ds": self.NS_DS}
        signed_info = signature.find("ds:SignedInfo", namespaces=ns)
        if signed_info is None:
            raise UserError(_("SignedInfo element not found in XML signature."))

        sig_val_node = signature.find("ds:SignatureValue", namespaces=ns)
        if sig_val_node is None or not (sig_val_node.text or "").strip():
            raise UserError(_("SignatureValue element not found or empty in XML signature."))

        try:
            signature_value = base64.b64decode(sig_val_node.text.encode("utf-8"))
        except Exception as e:
            _logger.error(f"Failed to decode SignatureValue: {e}", exc_info=True)
            raise UserError(_("Failed to decode SignatureValue from XML: %s") % str(e))

        try:
            si_output = io.BytesIO()
            etree.ElementTree(signed_info).write_c14n(si_output, exclusive=True)
            si_canon = si_output.getvalue()
        except Exception as e:
            _logger.error(f"Failed to canonicalize SignedInfo: {e}", exc_info=True)
            raise UserError(_("Failed to canonicalize SignedInfo for verification: %s") % str(e))

        public_key = certificate.public_key()
        try:
            public_key.verify(signature_value, si_canon, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            raise UserError(_("Cryptographic verification of the signature failed. The signature is NOT valid."))

    def verify_xml_signature(self, xml_content):
        root, signatures = self._parse_all_signatures(xml_content)
        all_lines = []

        for idx, signature in enumerate(signatures, 1):
            sig_id = signature.get("Id", f"Signature-{idx}")
            try:
                certificate = self._extract_certificate_from_signature(signature)
            except UserError as e:
                all_lines.append(
                    f"""
                <div style="margin-bottom: 25px; padding: 18px; background-color: #f8f9fa; border-radius: 6px; border-left: 4px solid #dc3545;">
                    <h4 style="margin-top: 0; margin-bottom: 10px; color: #dc3545; font-size: 16px;">
                        {_("Signature %s (%s)") % (idx, sig_id)}
                    </h4>
                    <div style="padding: 10px; background-color: #f8d7da; border-left: 3px solid #dc3545; border-radius: 3px; color: #721c24; font-size: 12px;">
                        <strong>{_("FAILED:")}</strong> {str(e)}
                    </div>
                </div>
                """
                )
                continue

            validity_status = "OK"
            chain_status = "INCONCLUSIVE"
            revocation_status = "INCONCLUSIVE"
            crypto_status = "OK"
            error_msg = None

            signing_time = self._extract_signing_time(signature)

            not_before = certificate.not_valid_before_utc if hasattr(certificate, "not_valid_before_utc") else certificate.not_valid_before
            not_after = certificate.not_valid_after_utc if hasattr(certificate, "not_valid_after_utc") else certificate.not_valid_after

            if signing_time is None:
                validity_status = "INCONCLUSIVE"
                error_msg = _("Signing time not found in signature. Cannot verify certificate validity period.")
            else:
                try:
                    not_before, not_after = self._check_certificate_validity_period(certificate, signing_time)
                except UserError as e:
                    validity_status = "FAILED"
                    error_msg = str(e)
                except ValueError as e:
                    validity_status = "INCONCLUSIVE"
                    error_msg = str(e)

            issuer_cert = None
            chain_info = []
            try:
                chain_status, issuer_cert, chain_info = self._check_chain_of_trust(certificate)
            except Exception:
                chain_status = "INCONCLUSIVE"
                issuer_cert = None

            revocation_method = None
            try:
                ocsp_status = self._check_ocsp_revocation(certificate, issuer_cert)
                if ocsp_status != "INCONCLUSIVE":
                    revocation_status = ocsp_status
                    revocation_method = "OCSP"
                else:
                    crl_status = self._check_crl_revocation(certificate)
                    if crl_status != "INCONCLUSIVE":
                        revocation_status = crl_status
                        revocation_method = "CRL"
                    else:
                        revocation_status = "INCONCLUSIVE"
                        revocation_method = "OCSP/CRL (both unavailable)"
            except Exception:
                revocation_status = "INCONCLUSIVE"
                revocation_method = "Error"

            try:
                self._verify_signature_value(root, signature, certificate)
            except UserError as e:
                crypto_status = "FAILED"
                error_msg = str(e)

            def get_badge_html(status):
                if status == "OK":
                    return '<span style="background-color: #28a745; color: white; padding: 2px 8px; border-radius: 10px; font-weight: 500; font-size: 11px; display: inline-block;">✓ OK</span>'
                if status == "FAILED":
                    return '<span style="background-color: #dc3545; color: white; padding: 2px 8px; border-radius: 10px; font-weight: 500; font-size: 11px; display: inline-block;">✗ FAILED</span>'
                return '<span style="background-color: #ffc107; color: #212529; padding: 2px 8px; border-radius: 10px; font-weight: 500; font-size: 11px; display: inline-block;">⚠ INCONCLUSIVE</span>'

            def format_cert_subject_safe(cert):
                parts = []
                try:
                    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
                    parts.append(f"CN={cn}")
                except IndexError:
                    pass
                try:
                    org = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
                    parts.append(f"O={org}")
                except IndexError:
                    pass
                try:
                    country = cert.subject.get_attributes_for_oid(NameOID.COUNTRY_NAME)[0].value
                    parts.append(f"C={country}")
                except IndexError:
                    pass
                try:
                    ou = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)[0].value
                    parts.append(f"OU={ou}")
                except IndexError:
                    pass
                return ", ".join(parts) if parts else "N/A"

            def format_cert_issuer_safe(cert):
                parts = []
                try:
                    cn = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
                    parts.append(f"CN={cn}")
                except IndexError:
                    pass
                try:
                    org = cert.issuer.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
                    parts.append(f"O={org}")
                except IndexError:
                    pass
                try:
                    country = cert.issuer.get_attributes_for_oid(NameOID.COUNTRY_NAME)[0].value
                    parts.append(f"C={country}")
                except IndexError:
                    pass
                return ", ".join(parts) if parts else "N/A"

            revocation_method_label = revocation_method or _("OCSP/CRL")
            sig_html = f"""
            <div style="margin-bottom: 25px; padding: 18px; background-color: #f8f9fa; border-radius: 6px; border-left: 4px solid #007bff;">
                <h4 style="margin-top: 0; margin-bottom: 12px; color: #007bff; font-size: 16px;">
                    {_("Signature %s (%s)") % (idx, sig_id)}
                </h4>

                <div style="margin-bottom: 15px; padding-bottom: 12px; border-bottom: 1px solid #dee2e6;">
                    <div style="margin-bottom: 6px; color: #495057; font-size: 12px;">
                        <strong>{_("Certificate subject:")}</strong><br/>
                        <span style="margin-left: 15px; color: #6c757d; font-size: 11px;">{format_cert_subject_safe(certificate)}</span>
                    </div>
                    <div style="color: #495057; font-size: 12px;">
                        <strong>{_("Issuer:")}</strong><br/>
                        <span style="margin-left: 15px; color: #6c757d; font-size: 11px;">{format_cert_issuer_safe(certificate)}</span>
                    </div>
                </div>

                <div style="margin-bottom: 12px;">
                    <strong style="color: #495057; display: inline-block; width: 240px;">• {_("Certificate validity period:")}</strong>
                    {get_badge_html(validity_status)}
                </div>
                <div style="margin-left: 20px; margin-bottom: 8px; color: #6c757d; font-size: 12px;">
                    <strong>{_("NotBefore:")}</strong> {not_before.isoformat()}<br/>
                    <strong>{_("NotAfter:")}</strong> {not_after.isoformat()}
                </div>

                <div style="margin-bottom: 12px; margin-top: 12px;">
                    <strong style="color: #495057; display: inline-block; width: 240px;">• {_("Chain of trust:")}</strong>
                    {get_badge_html(chain_status)}
                </div>

                <div style="margin-bottom: 12px; margin-top: 12px;">
                    <strong style="color: #495057; display: inline-block; width: 240px;">• {_("Revocation status (%s):") % revocation_method_label}</strong>
                    {get_badge_html(revocation_status)}
                </div>

                <div style="margin-bottom: 12px; margin-top: 12px;">
                    <strong style="color: #495057; display: inline-block; width: 240px;">• {_("Cryptographic signature:")}</strong>
                    {get_badge_html(crypto_status)}
                </div>
            </div>
            """
            if error_msg and (validity_status == "FAILED" or crypto_status == "FAILED"):
                sig_html += f"""
                <div style="margin-left: 20px; margin-top: 6px; padding: 6px 10px; background-color: #f8d7da; border-left: 3px solid #dc3545; border-radius: 3px; color: #721c24; font-size: 12px;">
                    <strong>{_("Error:")}</strong> {error_msg}
                </div>
                """
            all_lines.append(sig_html)

        return "".join(all_lines)
