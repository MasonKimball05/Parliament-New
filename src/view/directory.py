"""
Public member directory view.
Shows basic member information visible to all authenticated members.
"""
import csv
import io
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from src.models import ParliamentUser


@login_required
def member_directory(request):
    """Display a public directory of all active members."""

    # Get all active members, ordered by name
    members = ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        member_type='Advisor'  # Optionally exclude advisors from main list
    ).order_by('name')

    # Get advisors separately
    advisors = ParliamentUser.objects.filter(
        member_status='Active',
        member_type='Advisor'
    ).order_by('name')

    # Group members by type for display
    officers = [m for m in members if m.member_type == 'Officer']
    chairs = [m for m in members if m.member_type == 'Chair']
    regular_members = [m for m in members if m.member_type == 'Member']
    pledges = [m for m in members if m.member_type == 'Pledge']

    context = {
        'officers': officers,
        'chairs': chairs,
        'members': regular_members,
        'pledges': pledges,
        'advisors': advisors,
        'total_count': members.count() + advisors.count(),
    }

    return render(request, 'directory.html', context)


@login_required
def export_directory(request):
    """Export the member directory in various formats (CSV, TXT, XLSX)."""
    format_type = request.GET.get('format', 'csv').lower()

    # Get all active members including advisors
    all_members = ParliamentUser.objects.filter(
        member_status='Active'
    ).order_by('member_type', 'name')

    # Prepare data rows
    rows = []
    for member in all_members.prefetch_related('roles'):
        roles = ', '.join([role.name for role in member.roles.all()]) if member.roles.exists() else ''
        rows.append({
            'name': member.name,
            'roll_number': member.role_number if member.role_number else member.user_id,
            'email': member.email or '',
            'phone': member.phone_number or '',
            'member_type': member.member_type,
            'roles': roles,
        })

    if format_type == 'csv':
        return _export_csv(rows)
    elif format_type == 'txt':
        return _export_txt(rows)
    elif format_type in ['xlsx', 'excel']:
        return _export_xlsx(rows)
    else:
        return _export_csv(rows)


def _export_csv(rows):
    """Export directory as CSV file."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="member_directory.csv"'

    writer = csv.writer(response)
    writer.writerow(['Name', 'Roll Number', 'Email', 'Phone', 'Member Type', 'Role(s)'])

    for row in rows:
        writer.writerow([row['name'], row['roll_number'], row['email'], row['phone'], row['member_type'], row['roles']])

    return response


def _export_txt(rows):
    """Export directory as plain text file."""
    response = HttpResponse(content_type='text/plain')
    response['Content-Disposition'] = 'attachment; filename="member_directory.txt"'

    lines = ['MEMBER DIRECTORY', '=' * 60, '']

    for row in rows:
        lines.append(f"Name: {row['name']}")
        lines.append(f"Roll Number: {row['roll_number']}")
        if row['email']:
            lines.append(f"Email: {row['email']}")
        if row['phone']:
            lines.append(f"Phone: {row['phone']}")
        lines.append(f"Type: {row['member_type']}")
        if row['roles']:
            lines.append(f"Role(s): {row['roles']}")
        lines.append('-' * 40)

    response.write('\n'.join(lines))
    return response


def _export_xlsx(rows):
    """Export directory as Excel file."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        # Fallback to CSV if openpyxl not installed
        return _export_csv(rows)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Member Directory"

    # Header row styling
    headers = ['Name', 'Roll Number', 'Email', 'Phone', 'Member Type', 'Role(s)']
    header_fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    # Data rows
    for row_num, row in enumerate(rows, 2):
        ws.cell(row=row_num, column=1, value=row['name'])
        ws.cell(row=row_num, column=2, value=row['roll_number'])
        ws.cell(row=row_num, column=3, value=row['email'])
        ws.cell(row=row_num, column=4, value=row['phone'])
        ws.cell(row=row_num, column=5, value=row['member_type'])
        ws.cell(row=row_num, column=6, value=row['roles'])

    # Auto-adjust column widths
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[column].width = min(max_length + 2, 50)

    # Save to response
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="member_directory.xlsx"'

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response.write(buffer.read())

    return response
