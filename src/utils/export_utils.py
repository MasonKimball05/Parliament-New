"""
Export utilities for generating CSV and PDF files
"""
import csv
from django.http import HttpResponse
from datetime import datetime


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

    writer = csv.writer(response)
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

    writer = csv.writer(response)
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
