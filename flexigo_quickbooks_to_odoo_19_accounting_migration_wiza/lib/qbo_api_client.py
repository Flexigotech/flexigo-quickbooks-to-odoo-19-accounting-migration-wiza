# -*- coding: utf-8 -*-
"""
FR-006, FR-007, FR-008: QuickBooks Online API Client
Handles rate-limiting, exponential backoff, and batch operations
"""
import logging
import time
import requests
from typing import Dict, List, Any, Optional

_logger = logging.getLogger(__name__)


class QboApiClient:
    """
    FR-006: Intuit Accounting API client with configurable concurrency cap (default ≤10)
    FR-007: HTTP 429 retry with exponential backoff
    FR-008: Uses Accounting API (and Batch endpoint for bulk reads)
    """

    def __init__(self, access_token: str, realm_id: str, minor_version: str = '65',
                 sandbox: bool = False, concurrency_cap: int = 10, max_retries: int = 5, send=None):
        """
        Initialize QBO API client

        FR-002: Access token provided from connection profile
        FR-006: Concurrency cap configuration
        """
        self.access_token = access_token
        self.realm_id = realm_id
        self.minor_version = minor_version
        self.sandbox = sandbox
        self.concurrency_cap = concurrency_cap
        self.max_retries = max_retries

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

    def batch_query(self, queries: List[str]) -> List[Dict[str, Any]]:
        """
        FR-006: Use Batch endpoint for bulk reads (up to 30 operations per call)
        FR-007: Implement exponential backoff on 429
        """
        # Batch endpoint has lower rate limit (120 req/min)
        # Split queries into batches of 30
        all_results = []

        for i in range(0, len(queries), 30):
            batch = queries[i:i + 30]

            path = f"v3/company/{self.realm_id}/batch"
            params = {'minorversion': self.minor_version}

            batch_body = {
                'BatchItems': [
                    {
                        'bId': str(j),
                        'Query': query,
                    }
                    for j, query in enumerate(batch)
                ]
            }

            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    response = self.send(
                        'POST', path,
                        json=batch_body,
                        params=params,
                        timeout=30
                    )

                    if response.status_code == 200:
                        data = response.json()
                        batch_items = data.get('BatchResults', [])
                        all_results.extend(batch_items)
                        retry_count = 0
                        break

                    elif response.status_code == 429:
                        wait_time = (2 ** retry_count)
                        _logger.warning(f"Batch rate limited. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        retry_count += 1

                    else:
                        raise Exception(f"Batch API error: {response.status_code}")

                except Exception as e:
                    if retry_count < self.max_retries - 1:
                        retry_count += 1
                        time.sleep(2 ** retry_count)
                    else:
                        raise

        return all_results

    def get_company_info(self) -> Dict[str, Any]:
        """
        FR-005: Get company info for connection test
        """
        path = f"v3/company/{self.realm_id}/companyinfo/{self.realm_id}"
        params = {'minorversion': self.minor_version}

        response = self.send('GET', path, params=params, timeout=30)

        if response.status_code == 200:
            return response.json().get('CompanyInfo', {})
        else:
            raise Exception(f"Failed to get company info: {response.status_code}")
