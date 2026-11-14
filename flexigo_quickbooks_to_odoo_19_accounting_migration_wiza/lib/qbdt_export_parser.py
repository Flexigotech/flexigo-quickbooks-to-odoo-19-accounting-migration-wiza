# -*- coding: utf-8 -*-
"""QuickBooks Desktop: the IIF export of its lists (File > Utilities > Export > Lists to IIF Files), read into the
shape of QuickBooks Online's JSON so the same loaders take it.

An IIF file is tab-separated: a header row names a list and its columns (!ACCNT NAME ACCNTTYPE …) and the rows after
it start with that list (ACCNT Checking BANK …); a value with a comma comes in double quotes, and the columns and their
order change from file to file. Column names and values from the sample files of Intuit's IIF Import Kit."""
import csv
import io

# list of the IIF file → object type of the migration staging, in load order (accounts before items)
IIF_LISTS = {'ACCNT': 'account', 'CLASS': 'class', 'CUST': 'customer', 'VEND': 'vendor', 'INVITEM': 'item'}
# ACCNTTYPE → AccountType of QuickBooks Online. BANK AR OCASSET AP CCARD OCLIAB INC COGS EXP NONPOSTING are in the
# sample files; the other six come from Intuit's list of IIF account types. Another one is said by the load.
IIF_ACCOUNT_TYPES = {
    'BANK': 'Bank', 'AR': 'Accounts Receivable', 'OCASSET': 'Other Current Asset', 'FIXASSET': 'Fixed Asset',
    'OASSET': 'Other Asset', 'AP': 'Accounts Payable', 'CCARD': 'Credit Card', 'OCLIAB': 'Other Current Liability',
    'LTLIAB': 'Long Term Liability', 'EQUITY': 'Equity', 'INC': 'Income', 'COGS': 'Cost of Goods Sold',
    'EXP': 'Expense', 'EXINC': 'Other Income', 'EXEXP': 'Other Expense', 'NONPOSTING': 'Non-Posting',
}
# INVITEMTYPE → Item.Type of QuickBooks Online (INVENTORY and PART in the sample files); the others (sales tax,
# discounts, groups…) are no product, as in QuickBooks Online.
IIF_ITEM_TYPES = {'INVENTORY': 'Inventory', 'PART': 'NonInventory', 'SERV': 'Service'}


def decode(data):
    """QuickBooks Desktop writes its exports in the Windows code page; a file saved again may be UTF-8."""
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def parse_iif(content):
    """{list: [row as {column: value}]} of every list of the file, transactions included (TRNS, SPL)."""
    lists, columns = {}, {}
    for cells in csv.reader(io.StringIO(content.replace('\0', '')), delimiter='\t'):
        if not cells or not cells[0].strip():
            continue
        name = cells[0].strip()
        if name.startswith('!'):
            columns[name[1:]] = [cell.strip() for cell in cells]
            lists.setdefault(name[1:], [])
        elif name in columns:
            lists[name].append({column: value.strip() for column, value in zip(columns[name][1:], cells[1:])
                                if column})
    return lists


def _active(row):
    return row.get('HIDDEN', 'N').upper() != 'Y'


def _ref(name):
    return {'value': name, 'name': name} if name else None


def _number(value):
    try:
        return float((value or '').replace(',', ''))
    except ValueError:
        return 0.0


def _payload(object_type, row):
    """One row of an IIF list as QuickBooks Online would give it. Desktop names each record uniquely within its list
    (sub-accounts and sub-items as Parent:Child), so the name is its Id, and an item finds its accounts by name."""
    name = row.get('NAME')
    if not name:
        return None
    payload = {'Id': name, 'Name': name.rsplit(':', 1)[-1], 'FullyQualifiedName': name, 'Active': _active(row)}
    if object_type == 'account':
        payload.update(AcctNum=row.get('ACCNUM') or None,
                       AccountType=IIF_ACCOUNT_TYPES.get(row.get('ACCNTTYPE', '').upper(), row.get('ACCNTTYPE')))
    elif object_type in ('customer', 'vendor'):
        payload.update(DisplayName=name, PrimaryEmailAddr={'Address': row['EMAIL']} if row.get('EMAIL') else None,
                       PrimaryPhone={'FreeFormNumber': row['PHONE1']} if row.get('PHONE1') else None)
        if object_type == 'vendor' and row.get('TAXID'):
            payload['TaxIdentifier'] = row['TAXID']
    elif object_type == 'item':
        payload.update(Type=IIF_ITEM_TYPES.get(row.get('INVITEMTYPE', '').upper()),
                       UnitPrice=_number(row.get('PRICE')), PurchaseCost=_number(row.get('COST')),
                       IncomeAccountRef=_ref(row.get('ACCNT')), ExpenseAccountRef=_ref(row.get('COGSACCNT')))
    return {key: value for key, value in payload.items() if value is not None}


def iif_records(content):
    """{object type: [QuickBooks Online payload]} of the lists the migration reads, and the other lists of the file
    with their number of rows (they are not read)."""
    lists = parse_iif(content)
    records = {object_type: [p for p in (_payload(object_type, row) for row in lists.get(name, [])) if p]
               for name, object_type in IIF_LISTS.items()}
    other = {name: len(rows) for name, rows in lists.items() if name not in IIF_LISTS and not name.startswith('END')}
    return records, other
