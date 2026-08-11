# -*- coding: utf-8 -*-
"""
FR-003, FR-008: QuickBooks Desktop Export Format Parsers
Parses IIF, CSV, and qbXML export formats from QBDT uploads
"""
import csv
import json
import logging
from typing import Dict, List, Any
from xml.etree import ElementTree as ET

_logger = logging.getLogger(__name__)


class QbdtExportParser:
    """
    FR-003: Parse QBDT export uploads (IIF, CSV, qbXML)
    FR-008: Extract data from uploaded export sets
    """

    @staticmethod
    def parse_iif(content: str) -> List[Dict[str, Any]]:
        """
        FR-003: Parse .IIF (Interchange Format) export from QBDT

        IIF format is a text-based export format from QuickBooks Desktop
        """
        records = []
        current_record = {}

        for line in content.split('\n'):
            line = line.strip()

            if not line or line.startswith('!'):
                # Empty line or comment line
                if current_record and 'ID' in current_record:
                    records.append(current_record)
                    current_record = {}
                continue

            parts = line.split('\t')
            if len(parts) >= 2:
                key = parts[0]
                value = parts[1] if len(parts) > 1 else ''

                current_record[key] = value

        return records

    @staticmethod
    def parse_csv(content: str, delimiter: str = ',') -> List[Dict[str, Any]]:
        """
        FR-003: Parse CSV export from QBDT
        """
        reader = csv.DictReader(content.split('\n'), delimiter=delimiter)
        records = []

        for row in reader:
            if any(row.values()):  # Skip empty rows
                records.append(dict(row))

        return records

    @staticmethod
    def parse_qbxml(content: str) -> List[Dict[str, Any]]:
        """
        FR-003: Parse qbXML (QuickBooks XML) export from QBDT
        """
        records = []

        try:
            root = ET.fromstring(content)

            # qbXML has a flat structure with individual entity elements
            for elem in root:
                record = {}

                for child in elem:
                    record[child.tag] = child.text

                if record:
                    records.append(record)

        except ET.ParseError as e:
            _logger.error(f"Failed to parse qbXML: {str(e)}")
            raise

        return records

    @staticmethod
    def validate_export_structure(content: str, format_type: str) -> tuple[bool, Dict[str, int]]:
        """
        FR-005: Validate QBDT export structure

        Returns: (is_valid, record_counts)
        """
        counts = {}

        try:
            if format_type == 'iif':
                records = QbdtExportParser.parse_iif(content)
            elif format_type == 'csv':
                records = QbdtExportParser.parse_csv(content)
            elif format_type == 'qbxml':
                records = QbdtExportParser.parse_qbxml(content)
            else:
                return False, {}

            counts['total_records'] = len(records)

            # Count by entity type if available
            for record in records:
                entity_type = record.get('Type') or record.get('!TRNS') or record.get('root')
                counts[entity_type] = counts.get(entity_type, 0) + 1

            return len(records) > 0, counts

        except Exception as e:
            _logger.error(f"Export validation failed: {str(e)}")
            return False, {}
