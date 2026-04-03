import requests
import json
import logging
import urllib3

from odoo import _
from odoo.exceptions import UserError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_logger = logging.getLogger(__name__)


class DigigoAPI:
    """Client for DigiGo Mobile/Cloud Signature API."""

    def __init__(self, config):
        self.config = config
        self.base_url = (config.digigo_api_url or "").rstrip("/")

        if not self.base_url:
            raise UserError(_("DigiGo API URL is not configured."))

    def get_authorize_url(self, hash_b64, state):
        stripped_redirect_uri = (self.config.digigo_redirect_uri or "").strip()
        if not stripped_redirect_uri:
            raise UserError(_("DigiGo Redirect URI is not configured."))

        params = {
            "redirectUri": stripped_redirect_uri,
            "responseType": "code",
            "scope": "credential",
            "clientId": self.config.digigo_client_id,
            "numSignatures": "1",
            "hash": hash_b64,
            "state": state,
        }
        auth_url = f"{self.base_url}/oauth2/authorize"
        import urllib.parse

        query_string = urllib.parse.urlencode(params)
        return f"{auth_url}?{query_string}"

    def exchange_code_for_sad(self, code):
        client_id = self.config.digigo_client_id
        client_secret = self.config.digigo_client_secret
        redirect_uri = (self.config.digigo_redirect_uri or "").strip()

        if not redirect_uri:
            raise UserError(_("DigiGo Redirect URI is not configured."))
        if not client_id or not client_secret:
            raise UserError(_("DigiGo Client ID or Client Secret is not configured."))

        import urllib.parse

        safe_client_id = urllib.parse.quote(client_id, safe="")
        safe_client_secret = urllib.parse.quote(client_secret, safe="")
        safe_code = urllib.parse.quote(code, safe="")

        token_url = f"{self.base_url}/services/v1/oauth2/token/{safe_client_id}/authorization_code/{safe_client_secret}/{safe_code}"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            response = requests.post(token_url, data=redirect_uri, headers=headers, timeout=30, verify=False)
            if response.status_code == 200:
                return response.json()
            _logger.error("DigiGo Token Exchange Failed: HTTP %s", response.status_code)
            raise UserError(_("DigiGo Token Exchange Failed (Status: %s)") % response.status_code)
        except Exception as e:
            raise UserError(_("DigiGo Connection Error: %s") % str(e))

    def sign_hash(self, sad, hash_b64, credential_id):
        client_id = self.config.digigo_client_id
        hash_algo = "SHA256"
        sign_algo = "RSA"

        url = f"{self.base_url}/services/v1/signatures/signHash/{client_id}/{credential_id}/{sad}/{hash_algo}/{sign_algo}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        payload = [hash_b64]

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60, verify=False)
            if response.status_code == 200:
                resp_json = response.json()
                sig_val = None
                if isinstance(resp_json, list) and resp_json:
                    sig_val = resp_json[0].get("value")
                return {"signature": sig_val, "response": json.dumps(resp_json, indent=2)}

            _logger.error("DigiGo API Error: HTTP %s", response.status_code)
            raise UserError(_("DigiGo API Error: HTTP %s") % response.status_code)
        except Exception as e:
            _logger.error("DigiGo Signing Failed: %s", e)
            raise UserError(_("DigiGo Signing Failed: %s") % str(e))

    def get_certificate(self, credential_id):
        client_id = self.config.digigo_client_id
        url = f"{self.base_url}/services/v1/credentials/info/{client_id}/{credential_id}/chain"

        try:
            response = requests.get(url, headers={"Accept": "application/json"}, timeout=60, verify=False)
            if response.status_code == 200:
                data = response.json()
                if "certificates" in data and len(data["certificates"]) > 0:
                    return data["certificates"][0].get("encodedCertificate")
            _logger.error("DigiGo Certificate Error: HTTP %s", response.status_code)
            raise UserError(_("DigiGo Certificate Error (Status: %s)") % response.status_code)
        except Exception as e:
            raise UserError(_("Failed to retrieve DigiGo Certificate: %s") % str(e))

