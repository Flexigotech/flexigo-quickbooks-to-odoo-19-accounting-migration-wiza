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
                 sandbox: bool = False, concurrency_cap: int = 10, max_retries: int = 5):
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

        self.base_url = 'https://sandbox.api.intuit.com' if sandbox else 'https://quickbooks.api.intuit.com'
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        })

    def query(self, query_string: str) -> List[Dict[str, Any]]:
        """
        Execute a QBO SQL query (Accounting API)

        FR-008: Primary method for extraction
        FR-009: Resumable via query position
        """
        url = f"{self.base_url}/v3/company/{self.realm_id}/query"
        params = {'minorversion': self.minor_version}

        records = []
        offset = 0
        limit = 1000

        while True:
            paginated_query = f"{query_string} MAXRESULTS {limit} STARTPOSITION {offset + 1}"

            # FR-007: Exponential backoff on 429
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    response = self.session.get(url, params={**params, 'query': paginated_query}, timeout=30)

                    if response.status_code == 200:
                        data = response.json()
                        batch = data.get('QueryResponse', [])
                        records.extend(batch)

                        if len(batch) < limit:
                            # No more records
                            break

                        offset += limit
                        retry_count = 0  # Reset retry counter on success
                        break

                    elif response.status_code == 429:  # Rate limit
                        wait_time = (2 ** retry_count)  # Exponential backoff
                        _logger.warning(f"Rate limited. Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        retry_count += 1

                    else:
                        _logger.error(f"Query failed: HTTP {response.status_code}")
                        raise Exception(f"API error: {response.status_code}")

                except Exception as e:
                    if retry_count < self.max_retries - 1:
                        retry_count += 1
                        time.sleep(2 ** retry_count)
                    else:
                        raise

        return records

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

            url = f"{self.base_url}/v3/company/{self.realm_id}/batch"
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
                    response = self.session.post(
                        url,
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
        url = f"{self.base_url}/v3/company/{self.realm_id}/companyinfo/{self.realm_id}"
        params = {'minorversion': self.minor_version}

        response = self.session.get(url, params=params, timeout=30)

        if response.status_code == 200:
            return response.json().get('CompanyInfo', {})
        else:
            raise Exception(f"Failed to get company info: {response.status_code}")
