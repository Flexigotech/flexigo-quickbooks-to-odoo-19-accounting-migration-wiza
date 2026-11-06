# -*- coding: utf-8 -*-
"""
FR-008, FR-040, FR-041: QuickBooks Online Reports API Client
Handles reconciliation report extraction (TB, A/R aging, A/P aging, inventory)
"""
import logging
import requests
from typing import Dict, Any

_logger = logging.getLogger(__name__)


class QboReportsClient:
    """
    FR-008: QBO Reports API for reconciliation inputs
    FR-040, FR-041, FR-042, FR-043: Extract reports for reconciliation pack
    """

    def __init__(self, access_token: str, realm_id: str, minor_version: str = '65', sandbox: bool = False, send=None):
        self.access_token = access_token
        self.realm_id = realm_id
        self.minor_version = minor_version
        self.sandbox = sandbox

        self.base_url = 'https://sandbox-quickbooks.api.intuit.com' if sandbox else 'https://quickbooks.api.intuit.com'
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        })
        # send(method, path, **kwargs): qb.connection._qbo_send, the one way to Intuit (direct or FlexigoTech Cloud)
        self.send = send or self._send_direct

    def _send_direct(self, method: str, path: str, **kwargs):
        return self.session.request(method, f"{self.base_url}/{path}", **kwargs)

    def get_trial_balance(self, as_of_date: str) -> Dict[str, Any]:
        """
        FR-040: Retrieve Trial Balance report as of cutover date

        as_of_date: YYYY-MM-DD format
        """
        path = f"v3/company/{self.realm_id}/reports/TrialBalance"
        params = {
            'minorversion': self.minor_version,
            'as_of_date': as_of_date,
        }

        response = self.send('GET', path, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Trial Balance report failed: {response.status_code}")

    def get_aged_receivables(self, as_of_date: str) -> Dict[str, Any]:
        """
        FR-041: Retrieve Aged Receivables report for A/R reconciliation
        """
        path = f"v3/company/{self.realm_id}/reports/AgedReceivables"
        params = {
            'minorversion': self.minor_version,
            'as_of_date': as_of_date,
        }

        response = self.send('GET', path, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Aged Receivables report failed: {response.status_code}")

    def get_aged_payables(self, as_of_date: str) -> Dict[str, Any]:
        """
        FR-042: Retrieve Aged Payables report for A/P reconciliation
        """
        path = f"v3/company/{self.realm_id}/reports/AgedPayables"
        params = {
            'minorversion': self.minor_version,
            'as_of_date': as_of_date,
        }

        response = self.send('GET', path, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Aged Payables report failed: {response.status_code}")

    def get_inventory_valuation(self, as_of_date: str) -> Dict[str, Any]:
        """
        FR-043: Retrieve Inventory Valuation report for inventory reconciliation
        """
        path = f"v3/company/{self.realm_id}/reports/InventoryValuationSummary"
        params = {
            'minorversion': self.minor_version,
            'as_of_date': as_of_date,
        }

        response = self.send('GET', path, params=params, timeout=30)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Inventory Valuation report failed: {response.status_code}")
