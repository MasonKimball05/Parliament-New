"""
Export utilities for generating CSV and PDF files
"""
import csv
import re
from django.http import HttpResponse
from datetime import datetime

# v3.45.2: a spreadsheet program treats a cell that starts with one of these
# as a formula. A member can type one into any text field that is later
# exported (a service-hours description, a poll answer, a phone number).
_FORMULA_START = ('=', '+', '-', '@', '\t', '\r', '\n')

# Digits and phone punctuation only: "+1 (205) 555-0100", "-3.5". With no
# letters there is nothing to call and nothing to refer to, so these are left
# exactly as typed.
_PLAIN_NUMBER = re.compile(r'^[+\-]?[\d\s().,\-]*$')


def csv_safe_cell(value):
    """
    Return `value` in a form a spreadsheet will show as text, not run.

    Only strings are touched. A string that starts with a formula character
    gets a leading apostrophe, which is the usual way to say "this is text".
    Numbers, dates, None and plain numeric strings are returned unchanged.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_FORMULA_START) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


class _SafeCsvWriter:
    """`csv.writer` whose rows go through `csv_safe_cell` first."""

    def __init__(self, fileobj, *args, **kwargs):
        self._writer = csv.writer(fileobj, *args, **kwargs)

    def writerow(self, row):
        return self._writer.writerow([csv_safe_cell(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def safe_csv_writer(fileobj, *args, **kwargs):
    """
    Use this instead of `csv.writer` for anything a person will download.

    `src/tests/guards/test_csv_exports_are_safe.py` fails if a view calls
    `csv.writer` directly.
    """
    return _SafeCsvWriter(fileobj, *args, **kwargs)


def export_to_csv(filename, headers, rows):
    """
    Generic CSV export function

    Args:
        filename: Name of the file (without .csv extension)
        headers: List of column headers
        rows: List of row data (each row is a list/tuple)

    Returns:
        HttpResponse with CSV content
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

    writer = safe_csv_writer(response)
    writer.writerow(headers)

    for row in rows:
        writer.writerow(row)

    return response


def export_queryset_to_csv(filename, queryset, field_names, headers=None):
    """
    Export a Django queryset to CSV

    Args:
        filename: Name of the file (without .csv extension)
        queryset: Django queryset to export
        field_names: List of field names to include
        headers: Optional list of custom headers (defaults to field names)

    Returns:
        HttpResponse with CSV content
    """
    if headers is None:
        headers = field_names

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

    writer = safe_csv_writer(response)
    writer.writerow(headers)

    for obj in queryset:
        row = []
        for field in field_names:
            # Handle nested fields (e.g., 'user.name')
            if '.' in field:
                value = obj
                for part in field.split('.'):
                    value = getattr(value, part, '')
                    if callable(value):
                        value = value()
                row.append(value if value is not None else '')
            else:
                value = getattr(obj, field, '')
                if callable(value):
                    value = value()
                row.append(value if value is not None else '')
        writer.writerow(row)

    return response
