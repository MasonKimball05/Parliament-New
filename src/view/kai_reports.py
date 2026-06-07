from django.db.models import Count
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.tasks import send_email
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import localtime
from django.http import HttpResponse
from django.core.exceptions import ValidationError
import csv
from src.models import KaiReport, Committee, ParliamentUser, KaiReportActivity, KaiReportTemplate, KaiFormField, KaiReportFieldResponse, KaiClosureRequest, ActivityLog, KaiMemberPermission
from src.forms import KaiReportForm
from src.decorators import log_function_call
from src.feature_flag_decorators import require_feature_flag
from src.utils.file_validation import validate_uploaded_file


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def submit_kai_report(request):
    """Allow any logged-in user to submit a Kai report"""
    # Check if KaiReport table exists
    try:
        if request.method == 'POST':
            # Validate uploaded file if provided
            if 'supporting_document' in request.FILES:
                try:
                    validate_uploaded_file(request.FILES['supporting_document'])
                except ValidationError as e:
                    messages.error(request, f'File upload error: {str(e)}')
                    # Re-initialize form with templates
                    form = KaiReportForm()
                    try:
                        queryset = ParliamentUser.objects.filter(member_status='Active').order_by('name')
                        list(queryset)
                        form.fields['targeted_to'].queryset = queryset
                    except:
                        try:
                            queryset = ParliamentUser.objects.filter(is_active=True).only('name', 'member_type').order_by('name')
                            list(queryset)
                            form.fields['targeted_to'].queryset = queryset
                        except:
                            queryset = ParliamentUser.objects.all().only('name', 'member_type')
                            form.fields['targeted_to'].queryset = queryset
                    templates = KaiReportTemplate.objects.filter(is_active=True)
                    return render(request, 'kai/submit_report.html', {'form': form, 'templates': templates})

            form = KaiReportForm(request.POST, request.FILES)
            if form.is_valid():
                report = form.save(commit=False)
                report.submitted_by = request.user
                report.save()

                # Save custom field responses
                custom_fields = KaiFormField.objects.filter(is_active=True, is_builtin=False)
                for field in custom_fields:
                    field_key = f'custom_field_{field.id}'
                    value = request.POST.get(field_key, '').strip()
                    file_value = request.FILES.get(field_key)

                    # Only save if there's a value
                    if value or file_value:
                        response_data = {
                            'report': report,
                            'field': field,
                        }

                        if field.field_type in ['text', 'textarea', 'email', 'date', 'select', 'radio']:
                            response_data['text_value'] = value
                        elif field.field_type == 'number':
                            try:
                                response_data['number_value'] = float(value) if value else None
                            except ValueError:
                                response_data['text_value'] = value
                        elif field.field_type in ['multiselect', 'checkbox']:
                            values = request.POST.getlist(field_key)
                            response_data['json_value'] = values if values else None
                        elif field.field_type == 'file' and file_value:
                            response_data['file_value'] = file_value
                        elif field.field_type == 'member_select':
                            response_data['text_value'] = value

                        KaiReportFieldResponse.objects.create(**response_data)

                # Log activity
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='created',
                    details=f'Report created with category: {report.get_category_display()}'
                )
                ActivityLog.log_activity(
                    action_type='kai_action',
                    user=request.user,
                    description=f'{request.user.name} submitted Kai case #{report.id}',
                    request=request,
                    object_type='KaiReport',
                    object_id=report.id,
                    object_repr=f'Case #{report.id}',
                    metadata={'action': 'submitted'},
                )

                # Send email notification to Kai committee chair(s) only (NOT targeted person yet)
                try:
                    kai_committee = Committee.objects.get(is_kai_committee=True)
                    kai_chairs = kai_committee.chairs.all()

                    # Collect Kai chair emails only
                    recipient_emails = []

                    # Add Kai chair emails
                    if kai_chairs.exists():
                        chair_emails = [chair.email for chair in kai_chairs if chair.email]
                        recipient_emails.extend(chair_emails)

                    if recipient_emails:
                        subject = f'New Kai Report: {report.title}'
                        message = f"""
A new Kai report has been submitted.

Title: {report.title}
Submitted by: {report.submitted_by.name}
Submitted at: {localtime(report.submitted_at).strftime('%B %d, %Y at %I:%M %p %Z')}
{f"Directed to: {report.targeted_to.name}" if report.targeted_to else ""}

Description:
{report.description}

Tags: {', '.join(report.tags) if report.tags else 'None'}

Please log in to the Kai Committee page to review this report.
                        """

                        import logging
                        kai_logger = logging.getLogger('src')
                        kai_logger.info(f"[KAI EMAIL] Sending notification to {len(recipient_emails)} recipients: {recipient_emails}")

                        send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)
                        kai_logger.info(f"[KAI EMAIL] Email queued for report: {report.title}")
                    else:
                        import logging
                        kai_logger = logging.getLogger('src')
                        kai_logger.warning(f"[KAI EMAIL] No recipient emails found for Kai report notification")
                except Committee.DoesNotExist:
                    import logging
                    kai_logger = logging.getLogger('src')
                    kai_logger.warning(f"[KAI EMAIL] KAI committee not found - cannot send notification")
                except Exception as e:
                    # Log error but don't fail the submission
                    import logging
                    logger = logging.getLogger('src')
                    logger.error(f"[KAI EMAIL] Failed to send Kai report email: {e}")

                messages.success(request, 'Your Kai report has been submitted successfully! The Kai chair(s) have been notified.')
                return redirect('home')
        else:
            form = KaiReportForm()

        # Populate the targeted_to dropdown with active members
        # Use is_active for compatibility with test database
        try:
            queryset = ParliamentUser.objects.filter(
                member_status='Active'
            ).order_by('name')
            # Force evaluation to catch missing column error
            list(queryset)
            form.fields['targeted_to'].queryset = queryset
        except:
            # Fallback for test database that doesn't have member_status
            try:
                queryset = ParliamentUser.objects.filter(
                    is_active=True
                ).only('name', 'member_type').order_by('name')
                # Force evaluation
                list(queryset)
                form.fields['targeted_to'].queryset = queryset
            except:
                # If that still fails, just get all users with minimal fields
                queryset = ParliamentUser.objects.all().only('name', 'member_type')
                form.fields['targeted_to'].queryset = queryset

        # Get active templates
        templates = KaiReportTemplate.objects.filter(is_active=True)

        # Get custom fields (non-builtin)
        custom_fields = KaiFormField.objects.filter(is_active=True, is_builtin=False).order_by('section', 'display_order')

        # Group custom fields by section
        custom_sections = {}
        for field in custom_fields:
            section = field.section or 'Additional Information'
            if section not in custom_sections:
                custom_sections[section] = []
            custom_sections[section].append(field)

        # Get all active members for member_select fields
        all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

        return render(request, 'kai/submit_report.html', {
            'form': form,
            'templates': templates,
            'custom_fields': custom_fields,
            'custom_sections': custom_sections,
            'all_members': all_members,
        })
    except Exception as e:
        # Table doesn't exist yet
        import logging
        logger = logging.getLogger('function_calls')
        logger.error(f"Error in submit_kai_report: {e}")
        messages.warning(request, f'Kai Reports feature error: {str(e)}')
        return redirect('home')


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def _get_kai_access(user, committee):
    """
    Return a dict of Kai permission flags for the given user.
    Chairs and site admins get full access. Other users get their KaiMemberPermission
    flags; users with no permission row get all False.
    """
    FIELDS = [
        'can_view_report_list', 'can_view_report_details',
        'can_view_submitter_identity', 'can_view_accused_identity',
        'can_edit_open_cases', 'can_add_activity', 'can_close_cases',
    ]
    if committee.is_chair(user) or user.is_admin:
        return {f: True for f in FIELDS} | {'is_full_access': True}
    try:
        perm = KaiMemberPermission.objects.get(committee=committee, user=user)
        return {f: getattr(perm, f) for f in FIELDS} | {'is_full_access': False}
    except KaiMemberPermission.DoesNotExist:
        return {f: False for f in FIELDS} | {'is_full_access': False}


def view_kai_reports(request):
    """View for Kai chairs to see all submitted reports"""
    # Check if user is a Kai chair
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_list']:
        messages.error(request, 'You do not have permission to view Kai reports.')
        return redirect('home')

    # Check if KaiReport table exists
    try:
        # Get filter from query params
        status_filter = request.GET.get('status', 'all')
        category_filter = request.GET.get('category', 'all')
        search_query = request.GET.get('search', '').strip()
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')

        # Start with all reports
        reports = KaiReport.objects.all()

        # Apply status filter
        if status_filter == 'pending':
            reports = reports.filter(status='pending')
        elif status_filter == 'reviewed':
            reports = reports.filter(status='reviewed')
        elif status_filter == 'archived':
            reports = reports.filter(status='archived')

        # Apply category filter
        if category_filter != 'all':
            reports = reports.filter(category=category_filter)

        # Apply search filter
        if search_query:
            from django.db.models import Q
            reports = reports.filter(
                Q(title__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(submitted_by__name__icontains=search_query) |
                Q(targeted_to__name__icontains=search_query) |
                Q(tags__icontains=search_query)
            )

        # Apply date range filter
        if date_from:
            from datetime import datetime
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                reports = reports.filter(submitted_at__gte=date_from_obj)
            except ValueError:
                pass

        if date_to:
            from datetime import datetime, timedelta
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                # Include the entire day
                date_to_obj = date_to_obj + timedelta(days=1)
                reports = reports.filter(submitted_at__lt=date_to_obj)
            except ValueError:
                pass

        # Try select_related for production, fallback without it for test
        try:
            reports = list(reports.select_related('submitted_by', 'reviewed_by', 'targeted_to').order_by('-submitted_at'))
        except:
            # Test database missing columns - query without select_related
            reports = list(reports.order_by('-submitted_at'))

        # Get counts for status filters
        counts = {
            'all': KaiReport.objects.count(),
            'pending': KaiReport.objects.filter(status='pending').count(),
            'reviewed': KaiReport.objects.filter(status='reviewed').count(),
            'archived': KaiReport.objects.filter(status='archived').count(),
        }

        # Get counts for category filters — one aggregated query instead of one per category
        cat_qs = KaiReport.objects.values('category').annotate(total=Count('id'))
        cat_map = {row['category']: row['total'] for row in cat_qs}
        category_counts = {cat_value: cat_map.get(cat_value, 0) for cat_value, _ in KaiReport.CATEGORY_CHOICES}
    except Exception:
        # Table doesn't exist yet - show empty state
        reports = []
        status_filter = request.GET.get('status', 'all')
        category_filter = request.GET.get('category', 'all')
        search_query = request.GET.get('search', '').strip()
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        counts = {
            'all': 0,
            'pending': 0,
            'reviewed': 0,
            'archived': 0,
        }
        category_counts = {}
        cat_map = {}
        messages.info(request, 'Kai Reports database table not yet created. This is a preview of the interface.')

    # Dashboard stats (compute after main try/except so counts are available)
    try:
        from datetime import timedelta
        import json

        category_data = {
            cat_label: cat_map.get(cat_value, 0)
            for cat_value, cat_label in KaiReport.CATEGORY_CHOICES
            if cat_map.get(cat_value, 0)
        }

        outcome_pending = KaiReport.objects.filter(deliberation_outcome='pending').count()
        outcome_heard = KaiReport.objects.filter(deliberation_outcome='heard').count()
        outcome_thrown_out = KaiReport.objects.filter(deliberation_outcome='thrown_out').count()

        monthly_data = {}
        current_date = timezone.now()
        for i in range(5, -1, -1):
            month_date = current_date - timedelta(days=30 * i)
            month_key = month_date.strftime('%b %Y')
            month_start = month_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if month_date.month == 12:
                next_month_start = month_date.replace(year=month_date.year + 1, month=1, day=1)
            else:
                next_month_start = month_date.replace(month=month_date.month + 1, day=1)
            monthly_data[month_key] = KaiReport.objects.filter(
                submitted_at__gte=month_start, submitted_at__lt=next_month_start
            ).count()

        recent_activities = list(
            KaiReportActivity.objects.select_related('report', 'user').order_by('-timestamp')[:8]
        )
    except Exception:
        category_data = {}
        outcome_pending = outcome_heard = outcome_thrown_out = 0
        monthly_data = {}
        recent_activities = []

    import json as _json
    context = {
        'reports': reports,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'counts': counts,
        'category_counts': category_counts,
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
        'total_reports': counts['all'],
        'pending_count': counts['pending'],
        'reviewed_count': counts['reviewed'],
        'archived_count': counts['archived'],
        'category_data': _json.dumps(category_data),
        'monthly_data': _json.dumps(monthly_data),
        'outcome_pending': outcome_pending,
        'outcome_heard': outcome_heard,
        'outcome_thrown_out': outcome_thrown_out,
        'recent_activities': recent_activities,
        'kai_access': kai_access,
    }

    return render(request, 'kai/view_reports.html', context)


@login_required
@log_function_call
def export_kai_reports_csv(request):
    """Export filtered Kai reports to CSV"""
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_list']:
        messages.error(request, 'You do not have permission to export Kai reports.')
        return redirect('home')

    # Get same filters as view
    status_filter = request.GET.get('status', 'all')
    category_filter = request.GET.get('category', 'all')
    search_query = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    try:
        # Start with all reports
        reports = KaiReport.objects.all()

        # Apply filters (same logic as view)
        if status_filter == 'pending':
            reports = reports.filter(status='pending')
        elif status_filter == 'reviewed':
            reports = reports.filter(status='reviewed')
        elif status_filter == 'archived':
            reports = reports.filter(status='archived')

        if category_filter != 'all':
            reports = reports.filter(category=category_filter)

        if search_query:
            from django.db.models import Q
            reports = reports.filter(
                Q(title__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(submitted_by__name__icontains=search_query) |
                Q(targeted_to__name__icontains=search_query) |
                Q(tags__icontains=search_query)
            )

        if date_from:
            from datetime import datetime
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                reports = reports.filter(submitted_at__gte=date_from_obj)
            except ValueError:
                pass

        if date_to:
            from datetime import datetime, timedelta
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                date_to_obj = date_to_obj + timedelta(days=1)
                reports = reports.filter(submitted_at__lt=date_to_obj)
            except ValueError:
                pass

        # Try select_related
        try:
            reports = list(reports.select_related('submitted_by', 'reviewed_by', 'targeted_to').order_by('-submitted_at'))
        except:
            reports = list(reports.order_by('-submitted_at'))

        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="kai_reports_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'ID',
            'Title',
            'Category',
            'Submitted By',
            'Targeted To',
            'Submitted At',
            'Status',
            'Deliberation Outcome',
            'Minutes Closed',
            'Reviewed By',
            'Reviewed At',
            'Tags',
            'Description'
        ])

        for report in reports:
            writer.writerow([
                report.id,
                report.title,
                report.get_category_display(),
                report.submitted_by.name if kai_access['can_view_submitter_identity'] else '[Redacted]',
                (report.targeted_to.name if report.targeted_to else '') if kai_access['can_view_accused_identity'] else '[Redacted]',
                localtime(report.submitted_at).strftime('%Y-%m-%d %H:%M:%S'),
                report.get_status_display(),
                report.get_deliberation_outcome_display(),
                'Yes' if report.closed_by_accused_request else 'No',
                report.reviewed_by.name if report.reviewed_by else '',
                localtime(report.reviewed_at).strftime('%Y-%m-%d %H:%M:%S') if report.reviewed_at else '',
                ', '.join(report.tags),
                report.description
            ])

        ActivityLog.log_activity(
            action_type='kai_action',
            user=request.user,
            description=f'{request.user.name} exported Kai reports CSV ({len(reports)} records)',
            request=request,
            object_type='KaiReport',
            metadata={'action': 'export_csv', 'record_count': len(reports)},
        )

        return response

    except Exception as e:
        messages.error(request, f'Failed to export reports: {str(e)}')
        return redirect('view_kai_reports')


@login_required
@log_function_call
def manage_kai_report(request, report_id):
    """Manage a specific Kai report (mark as reviewed, add notes, etc.)"""
    # Check if KaiReport table exists
    try:
        report = get_object_or_404(KaiReport, id=report_id)
    except Exception:
        messages.warning(request, 'Kai Reports feature is not yet set up. Please run database migrations.')
        return redirect('home')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('home')

    if request.method == 'POST':
        action = request.POST.get('action')

        # Action-level permission checks
        _edit_actions = {'mark_reviewed', 'mark_pending', 'update_tags', 'update_deliberation',
                         'link_report', 'unlink_report', 'update_accused', 'notify_accused', 'notify_submitter'}
        _activity_actions = {'update_notes', 'add_activity'}
        _close_actions = {'archive', 'approve_closure', 'deny_closure'}

        if action in _edit_actions and not kai_access['can_edit_open_cases']:
            messages.error(request, 'You do not have permission to edit cases.')
            return redirect('manage_kai_report', report_id=report.id)
        if action in _activity_actions and not kai_access['can_add_activity']:
            messages.error(request, 'You do not have permission to add activity to cases.')
            return redirect('manage_kai_report', report_id=report.id)
        if action in _close_actions and not kai_access['can_close_cases']:
            messages.error(request, 'You do not have permission to close cases.')
            return redirect('manage_kai_report', report_id=report.id)

        if action == 'mark_reviewed':
            report.mark_as_reviewed(request.user)
            messages.success(request, f'Report "{report.title}" marked as reviewed.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='status_changed',
                details=f'Status changed from pending to reviewed'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} marked Kai case #{report.id} as reviewed',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'mark_reviewed'},
            )

            # Send email notification to submitter
            try:
                if report.submitted_by.email:
                    subject = f'Kai Report Update: {report.title}'
                    message = f"""
Your Kai report has been reviewed.

Report Title: {report.title}
Status: Reviewed
Reviewed by: {request.user.name}
Reviewed at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}

You can view the full report details at the Kai Committee page.
                    """
                    send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, [report.submitted_by.email])
            except Exception as e:
                import logging
                logger = logging.getLogger('function_calls')
                logger.error(f"Failed to queue status update email: {e}")

        elif action == 'mark_pending':
            report.status = 'pending'
            report.reviewed_by = None
            report.reviewed_at = None
            report.save()
            messages.success(request, f'Report "{report.title}" marked as pending.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='status_changed',
                details='Status changed back to pending'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} set Kai case #{report.id} back to pending',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'mark_pending'},
            )

        elif action == 'archive':
            report.status = 'archived'
            report.save()
            messages.success(request, f'Report "{report.title}" archived.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='archived',
                details='Report manually archived'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} archived Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'archived'},
            )

        elif action == 'update_notes':
            report.chair_notes = request.POST.get('chair_notes', '')
            report.save()
            messages.success(request, 'Notes updated successfully.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='notes_updated',
                details='Chair notes updated'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} updated chair notes on Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'update_notes'},
            )

        elif action == 'update_tags':
            tags_str = request.POST.get('tags', '')
            report.tags = [t.strip() for t in tags_str.split(',') if t.strip()]
            report.save()
            messages.success(request, 'Tags updated successfully.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='tags_updated',
                details=f'Tags updated to: {", ".join(report.tags) if report.tags else "none"}'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} updated tags on Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'update_tags'},
            )

        elif action == 'update_deliberation':
            deliberation_outcome = request.POST.get('deliberation_outcome')
            committee_notes = request.POST.get('committee_notes', '')
            closed_by_accused = request.POST.get('closed_by_accused_request') == 'on'

            if deliberation_outcome:
                old_outcome = report.deliberation_outcome
                report.deliberation_outcome = deliberation_outcome
                report.committee_notes = committee_notes
                report.closed_by_accused_request = closed_by_accused

                # If minutes closed at accused's request, archive the report
                if closed_by_accused and deliberation_outcome == 'heard':
                    report.status = 'archived'
                    # Append closure note to committee notes if not already there
                    closure_note = "Minutes closed at the request of the accused."
                    if closure_note not in report.committee_notes:
                        if report.committee_notes:
                            report.committee_notes += f"\n\n{closure_note}"
                        else:
                            report.committee_notes = closure_note
                    messages.success(request, 'Deliberation outcome updated. Minutes closed and report archived.')
                else:
                    outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                    messages.success(request, f'Deliberation outcome updated to: {outcome_display}')

                report.save()

                # Log activity
                if old_outcome != deliberation_outcome:
                    outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='deliberation_updated',
                        details=f'Deliberation outcome changed to: {outcome_display}'
                    )

                if committee_notes:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='committee_notes_updated',
                        details='Committee notes added/updated'
                    )

                if closed_by_accused:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='minutes_closed',
                        details='Minutes closed at the request of the accused'
                    )

                ActivityLog.log_activity(
                    action_type='kai_action',
                    user=request.user,
                    description=f'{request.user.name} updated deliberation on Kai case #{report.id}',
                    request=request,
                    object_type='KaiReport',
                    object_id=report.id,
                    object_repr=f'Case #{report.id}',
                    metadata={'action': 'update_deliberation'},
                )

                # Send email notifications about outcome (ONLY to targeted person, NOT submitter)
                if old_outcome != deliberation_outcome and report.targeted_to and report.targeted_to.email:
                    try:
                        outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                        message = None  # Initialize before conditional branches

                        # Notify targeted person about deliberation outcome
                        if deliberation_outcome == 'heard':
                            subject = 'Kai Committee Notification - Case Heard'
                            message = f"""
This is to inform you that a report has been submitted to the Kai Committee that involves you.

The Kai Committee has decided to hear this case and may reach out to you for further information.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}
                            """
                        elif deliberation_outcome == 'thrown_out':
                            subject = 'Kai Committee Notification - Case Resolved'
                            message = f"""
This is to inform you that a report submitted to the Kai Committee that involved you has been resolved.

The case has been thrown out and no further action is required from you.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}
                            """
                        elif deliberation_outcome == 'pending':
                            # Don't notify for pending status
                            message = None

                        if message:
                            send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, [report.targeted_to.email])
                    except Exception as e:
                        import logging
                        logger = logging.getLogger('function_calls')
                        logger.error(f"Failed to queue deliberation update email: {e}")
            else:
                messages.error(request, 'Please select a deliberation outcome.')

        elif action == 'notify_submitter':
            # Only allow if minutes are not closed
            if report.closed_by_accused_request:
                messages.error(request, 'Cannot notify submitter when minutes are closed.')
            else:
                # Send notification to submitter with deliberation outcome and notes
                try:
                    if report.submitted_by.email:
                        from django.core.mail import EmailMultiAlternatives
                        from django.urls import reverse
                        from django.utils.html import escape

                        outcome_display = dict(report.DELIBERATION_CHOICES).get(report.deliberation_outcome, 'Pending')
                        notify_time = localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')

                        subject = f'Kai Report Update: {report.title}'

                        # Plain text version
                        text_message = f"""
This is a notification regarding your Kai report submission.

Case Number: #{report.id}
Deliberation Outcome: {outcome_display}

Committee Notes:
{report.committee_notes if report.committee_notes else 'No additional notes provided.'}

If you have any questions, please contact the Kai Committee chair(s).

Notified at: {notify_time}
                        """

                        # Build tracking pixel URL
                        tracking_url = request.build_absolute_uri(
                            reverse('track_kai_submitter_email', kwargs={'report_id': report.id})
                        )

                        escaped_notes = escape(report.committee_notes or 'No additional notes provided.').replace('\n', '<br>')

                        # HTML version with tracking pixel
                        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2c5282 100%); padding: 30px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Kai Committee Notification</h1>
        <p style="color: #a0c4e8; margin: 10px 0 0 0; font-size: 14px;">Case Update — Case #{report.id}</p>
    </div>

    <div style="background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-top: none;">
        <p style="margin-top: 0;">This is a notification regarding your Kai report submission.</p>

        <div style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 15px 20px; margin: 20px 0; border-radius: 0 4px 4px 0;">
            <p style="margin: 0;"><strong>Deliberation Outcome:</strong> {escape(outcome_display)}</p>
        </div>

        <h3 style="font-size: 16px; color: #2d3748;">Committee Notes</h3>
        <p style="margin: 0; white-space: pre-wrap;">{escaped_notes}</p>

        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 25px 0;">

        <p style="color: #718096; font-size: 12px; margin-bottom: 0;">
            If you have any questions, please contact the Kai Committee chair(s).<br>
            Notified at: {notify_time}<br>
            Kai Committee &bull; Beta Theta Pi - Samford Chapter
        </p>
    </div>

    <!-- Tracking pixel -->
    <img src="{tracking_url}" width="1" height="1" alt="" style="display:none;">
</body>
</html>
                        """

                        email = EmailMultiAlternatives(
                            subject=subject,
                            body=text_message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[report.submitted_by.email],
                        )
                        email.attach_alternative(html_message, "text/html")
                        email.send(fail_silently=False)

                        # Update report tracking fields — reset viewed on new send
                        report.submitter_notified_at = timezone.now()
                        report.submitter_email_viewed_at = None
                        report.save()

                        # Log activity
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='status_changed',
                            details=f'Submitter notified of deliberation outcome'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} notified submitter of Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'notify_submitter'},
                        )

                        messages.success(request, f'Submitter has been notified via email.')
                    else:
                        messages.warning(request, f'Submitter does not have an email address on file.')
                except Exception as e:
                    import logging
                    logger = logging.getLogger('function_calls')
                    logger.error(f"Failed to send submitter notification: {e}")
                    messages.error(request, f'Failed to send notification: {str(e)}')

        elif action == 'link_report':
            # Link a related report
            related_id = request.POST.get('related_report_id')
            if related_id:
                try:
                    related_report = KaiReport.objects.get(id=related_id)
                    report.related_reports.add(related_report)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Linked to related report: {related_report.title} (#{related_report.id})'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} linked Kai case #{report.id} to case #{related_report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'link_report', 'linked_case_id': related_report.id},
                    )

                    messages.success(request, f'Linked to report: {related_report.title}')
                except KaiReport.DoesNotExist:
                    messages.error(request, 'Related report not found.')
            else:
                messages.error(request, 'No report selected.')

        elif action == 'unlink_report':
            # Unlink a related report
            related_id = request.POST.get('related_report_id')
            if related_id:
                try:
                    related_report = KaiReport.objects.get(id=related_id)
                    report.related_reports.remove(related_report)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Unlinked from related report: {related_report.title} (#{related_report.id})'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} unlinked Kai case #{report.id} from case #{related_report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'unlink_report', 'unlinked_case_id': related_report.id},
                    )

                    messages.success(request, f'Unlinked from report: {related_report.title}')
                except KaiReport.DoesNotExist:
                    messages.error(request, 'Related report not found.')
            else:
                messages.error(request, 'No report selected.')

        elif action == 'update_accused':
            # Update or set the accused person
            accused_id = request.POST.get('accused_id', '').strip()
            accused_email = request.POST.get('accused_email', '').strip()

            if accused_id:
                try:
                    accused_user = ParliamentUser.objects.get(user_id=accused_id)
                    old_targeted = report.targeted_to
                    report.targeted_to = accused_user

                    # Update email if provided and different
                    if accused_email and accused_email != accused_user.email:
                        accused_user.email = accused_email
                        accused_user.save()

                    report.save()

                    # Log activity
                    if old_targeted != accused_user:
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='status_changed',
                            details=f'Accused person set to: {accused_user.name}'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} updated accused person on Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'update_accused'},
                        )

                    messages.success(request, f'Accused person updated to {accused_user.name}.')
                except ParliamentUser.DoesNotExist:
                    messages.error(request, 'Selected member not found.')
            else:
                # Clear the accused person
                if report.targeted_to:
                    old_name = report.targeted_to.name
                    report.targeted_to = None
                    report.save()

                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Accused person removed (was: {old_name})'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} removed accused person from Kai case #{report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'update_accused', 'cleared': True},
                    )
                    messages.success(request, 'Accused person removed from report.')

        elif action == 'notify_accused':
            # Notify the accused person of the case
            notification_message = request.POST.get('accused_notification_message', '').strip()

            if not report.targeted_to:
                messages.error(request, 'No accused person specified for this report.')
            elif not report.targeted_to.email:
                messages.error(request, f'{report.targeted_to.name} does not have an email address on file.')
            elif not notification_message:
                messages.error(request, 'Please enter a message explaining what the person is being reported for.')
            else:
                try:
                    from django.core.mail import EmailMultiAlternatives
                    from django.urls import reverse

                    subject = 'Kai Committee Notification - Case Filed'

                    # Plain text version
                    text_message = f"""
Dear {report.targeted_to.name},

This is an official notification from the Kai Committee of Beta Theta Pi.

A report has been filed with the Kai Committee that involves you. The details are as follows:

{notification_message}

The Kai Committee will review this matter and may contact you for further information or to schedule a hearing. You have the right to:
- Present your side of the story
- Bring witnesses or evidence in your defense
- Request that the minutes be closed (kept confidential)

If you have any questions or concerns, please contact the Kai Committee chair(s).

This notification was sent on {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}.

Kai Committee
Beta Theta Pi - Samford Chapter
                    """

                    # Build tracking pixel URL
                    tracking_url = request.build_absolute_uri(
                        reverse('track_kai_accused_email', kwargs={'report_id': report.id})
                    )

                    # Escape notification message for HTML
                    from django.utils.html import escape
                    escaped_message = escape(notification_message).replace('\n', '<br>')

                    # HTML version with tracking pixel
                    html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2c5282 100%); padding: 30px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Kai Committee Notification</h1>
        <p style="color: #a0c4e8; margin: 10px 0 0 0; font-size: 14px;">Official Notice - Case Filed</p>
    </div>

    <div style="background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-top: none;">
        <p style="margin-top: 0;">Dear <strong>{report.targeted_to.name}</strong>,</p>

        <p>This is an official notification from the Kai Committee of Beta Theta Pi.</p>

        <p>A report has been filed with the Kai Committee that involves you. The details are as follows:</p>

        <div style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 15px 20px; margin: 20px 0; border-radius: 0 4px 4px 0;">
            <p style="margin: 0; white-space: pre-wrap;">{escaped_message}</p>
        </div>

        <p>The Kai Committee will review this matter and may contact you for further information or to schedule a hearing.</p>

        <div style="background: #ebf8ff; border: 1px solid #90cdf4; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #2b6cb0; font-size: 16px;">Your Rights</h3>
            <ul style="margin: 0; padding-left: 20px; color: #2c5282;">
                <li>Present your side of the story</li>
                <li>Bring witnesses or evidence in your defense</li>
                <li>Request that the minutes be closed (kept confidential)</li>
            </ul>
        </div>

        <p>If you have any questions or concerns, please contact the Kai Committee chair(s).</p>

        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 25px 0;">

        <p style="color: #718096; font-size: 12px; margin-bottom: 0;">
            This notification was sent on {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}.<br>
            Kai Committee &bull; Beta Theta Pi - Samford Chapter
        </p>
    </div>

    <!-- Tracking pixel -->
    <img src="{tracking_url}" width="1" height="1" alt="" style="display:none;">
</body>
</html>
                    """

                    # Send email with both plain text and HTML
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=text_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[report.targeted_to.email]
                    )
                    email.attach_alternative(html_message, "text/html")
                    email.send(fail_silently=False)

                    # Update report - reset viewed status since new email sent
                    report.accused_notified = True
                    report.accused_notified_at = timezone.now()
                    report.accused_notification_message = notification_message
                    report.accused_email_viewed_at = None  # Reset on new notification
                    report.save()

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Accused ({report.targeted_to.name}) notified of the case'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} notified accused on Kai case #{report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'notify_accused'},
                    )

                    messages.success(request, f'{report.targeted_to.name} has been notified of the case via email.')

                except Exception as e:
                    import logging
                    logger = logging.getLogger('function_calls')
                    logger.error(f"Failed to send accused notification: {e}")
                    messages.error(request, f'Failed to send notification: {str(e)}')

        elif action == 'approve_closure':
            # Approve a closure request
            closure_request_id = request.POST.get('closure_request_id')
            review_notes = request.POST.get('review_notes', '').strip()

            if closure_request_id:
                try:
                    closure_request = KaiClosureRequest.objects.get(id=closure_request_id, report=report)
                    if closure_request.status == 'pending':
                        closure_request.status = 'approved'
                        closure_request.reviewed_by = request.user
                        closure_request.reviewed_at = timezone.now()
                        closure_request.review_notes = review_notes
                        closure_request.save()

                        # Archive the report
                        report.status = 'archived'
                        report.save()

                        # Log activity
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='closure_approved',
                            details=f'Closure request approved. Report archived.'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} approved closure request on Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'approve_closure'},
                        )

                        # Notify the requester
                        if closure_request.requested_by.email:
                            send_email.delay(
                                f'[Kai] Closure Request Approved: {report.title}',
                                f"""Your closure request has been approved.

Report: {report.title}
Decision: Approved
{f"Notes: {review_notes}" if review_notes else ""}

The case has been archived.
""",
                                settings.DEFAULT_FROM_EMAIL,
                                [closure_request.requested_by.email],
                            )

                        messages.success(request, 'Closure request approved. Report has been archived.')
                    else:
                        messages.warning(request, 'This closure request has already been processed.')
                except KaiClosureRequest.DoesNotExist:
                    messages.error(request, 'Closure request not found.')

        elif action == 'deny_closure':
            # Deny a closure request
            closure_request_id = request.POST.get('closure_request_id')
            review_notes = request.POST.get('review_notes', '').strip()

            if closure_request_id:
                try:
                    closure_request = KaiClosureRequest.objects.get(id=closure_request_id, report=report)
                    if closure_request.status == 'pending':
                        if not review_notes:
                            messages.error(request, 'Please provide a reason for denying the closure request.')
                        else:
                            closure_request.status = 'denied'
                            closure_request.reviewed_by = request.user
                            closure_request.reviewed_at = timezone.now()
                            closure_request.review_notes = review_notes
                            closure_request.save()

                            # Log activity
                            KaiReportActivity.objects.create(
                                report=report,
                                user=request.user,
                                action='closure_denied',
                                details=f'Closure request denied. Reason: {review_notes[:100]}...' if len(review_notes) > 100 else f'Closure request denied. Reason: {review_notes}'
                            )
                            ActivityLog.log_activity(
                                action_type='kai_action',
                                user=request.user,
                                description=f'{request.user.name} denied closure request on Kai case #{report.id}',
                                request=request,
                                object_type='KaiReport',
                                object_id=report.id,
                                object_repr=f'Case #{report.id}',
                                metadata={'action': 'deny_closure'},
                            )

                            # Notify the requester
                            if closure_request.requested_by.email:
                                send_email.delay(
                                    f'[Kai] Closure Request Denied: {report.title}',
                                    f"""Your closure request has been denied.

Report: {report.title}
Decision: Denied
Reason: {review_notes}

You may submit another closure request in the future if circumstances change.
""",
                                    settings.DEFAULT_FROM_EMAIL,
                                    [closure_request.requested_by.email],
                                )

                            messages.success(request, 'Closure request denied.')
                    else:
                        messages.warning(request, 'This closure request has already been processed.')
                except KaiClosureRequest.DoesNotExist:
                    messages.error(request, 'Closure request not found.')

        return redirect('manage_kai_report', report_id=report.id)

    # Get activity log
    try:
        activity_log = list(report.activity_log.all().select_related('user')[:20])  # Last 20 activities
    except:
        activity_log = []

    # Get related reports
    try:
        related_reports = list(report.related_reports.all().select_related('submitted_by', 'targeted_to'))
    except:
        related_reports = []

    # Get available reports to link (excluding current report and already linked ones)
    try:
        available_reports = KaiReport.objects.exclude(id=report.id).exclude(id__in=[r.id for r in related_reports]).select_related('submitted_by', 'targeted_to').order_by('-submitted_at')[:20]
    except:
        available_reports = []

    # Get all members for accused person selection (active first, then inactive/alumni)
    try:
        all_members = ParliamentUser.objects.exclude(member_status='Removed').order_by('member_status', 'name')
    except:
        all_members = ParliamentUser.objects.exclude(member_status='Removed').order_by('name')

    # Get pending closure requests for this report
    try:
        closure_requests = list(report.closure_requests.all().select_related('requested_by', 'reviewed_by').order_by('-requested_at'))
    except:
        closure_requests = []

    # Get custom field responses
    try:
        custom_responses = list(report.custom_responses.all().select_related('field'))
    except:
        custom_responses = []

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'related_reports': related_reports,
        'available_reports': available_reports,
        'all_members': all_members,
        'closure_requests': closure_requests,
        'custom_responses': custom_responses,
        'kai_access': kai_access,
    }

    return render(request, 'kai/manage_report.html', context)


@login_required
@log_function_call
def print_kai_report(request, report_id):
    """Print-friendly view for a Kai report (can be printed to PDF)"""
    # Check if KaiReport table exists
    try:
        report = get_object_or_404(KaiReport, id=report_id)
    except Exception:
        messages.warning(request, 'Kai Reports feature is not yet set up. Please run database migrations.')
        return redirect('home')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('home')

    # Get activity log
    try:
        activity_log = list(report.activity_log.all().select_related('user'))
    except:
        activity_log = []

    ActivityLog.log_activity(
        action_type='kai_action',
        user=request.user,
        description=f'{request.user.name} printed/exported Kai case #{report.id}',
        request=request,
        object_type='KaiReport',
        object_id=report.id,
        object_repr=f'Case #{report.id}',
        metadata={'action': 'print_report'},
    )

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'print_date': timezone.now(),
    }

    return render(request, 'kai/print_report.html', context)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def kai_dashboard(request):
    """Redirects to the consolidated Kai reports page (dashboard merged in)."""
    return redirect('view_kai_reports')


@login_required
@log_function_call
def bulk_actions_kai_reports(request):
    """Handle bulk actions on multiple Kai reports"""
    if request.method != 'POST':
        return redirect('view_kai_reports')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_list']:
        messages.error(request, 'You do not have permission to perform bulk actions.')
        return redirect('home')

    # Get selected report IDs and action
    report_ids = request.POST.getlist('report_ids')
    action = request.POST.get('bulk_action')

    # Action-level permission check
    if action in ('mark_reviewed', 'mark_pending') and not kai_access['can_edit_open_cases']:
        messages.error(request, 'You do not have permission to edit cases.')
        return redirect('view_kai_reports')
    if action == 'archive' and not kai_access['can_close_cases']:
        messages.error(request, 'You do not have permission to close cases.')
        return redirect('view_kai_reports')

    if not report_ids:
        messages.warning(request, 'No reports selected.')
        return redirect('view_kai_reports')

    if not action:
        messages.warning(request, 'No action selected.')
        return redirect('view_kai_reports')

    try:
        # Get the reports
        reports = KaiReport.objects.filter(id__in=report_ids)
        count = reports.count()

        if action == 'mark_reviewed':
            # Mark all as reviewed
            for report in reports:
                if report.status != 'reviewed':
                    report.mark_as_reviewed(request.user)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details='Bulk action: Status changed to reviewed'
                    )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk marked {count} Kai case(s) as reviewed',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_mark_reviewed', 'count': count},
            )
            messages.success(request, f'{count} report(s) marked as reviewed.')

        elif action == 'archive':
            # Archive all
            updated = reports.update(status='archived')

            # Log activity for each
            for report in reports:
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='archived',
                    details='Bulk action: Report archived'
                )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk archived {updated} Kai case(s)',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_archive', 'count': updated},
            )
            messages.success(request, f'{updated} report(s) archived.')

        elif action == 'mark_pending':
            # Mark all as pending
            updated = reports.update(status='pending', reviewed_by=None, reviewed_at=None)

            # Log activity for each
            for report in reports:
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='status_changed',
                    details='Bulk action: Status changed to pending'
                )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk marked {updated} Kai case(s) as pending',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_mark_pending', 'count': updated},
            )
            messages.success(request, f'{updated} report(s) marked as pending.')

        elif action == 'export_csv':
            # Export selected reports to CSV
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="selected_kai_reports_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

            writer = csv.writer(response)
            writer.writerow([
                'ID', 'Title', 'Category', 'Submitted By', 'Targeted To',
                'Submitted At', 'Status', 'Deliberation Outcome', 'Minutes Closed',
                'Reviewed By', 'Reviewed At', 'Tags', 'Description'
            ])

            for report in reports.select_related('submitted_by', 'reviewed_by', 'targeted_to'):
                writer.writerow([
                    report.id,
                    report.title,
                    report.get_category_display(),
                    report.submitted_by.name,
                    report.targeted_to.name if report.targeted_to else '',
                    report.submitted_at.strftime('%Y-%m-%d %H:%M:%S'),
                    report.get_status_display(),
                    report.get_deliberation_outcome_display(),
                    'Yes' if report.closed_by_accused_request else 'No',
                    report.reviewed_by.name if report.reviewed_by else '',
                    report.reviewed_at.strftime('%Y-%m-%d %H:%M:%S') if report.reviewed_at else '',
                    ', '.join(report.tags),
                    report.description
                ])

            return response

        else:
            messages.error(request, 'Invalid action selected.')

    except Exception as e:
        messages.error(request, f'Error performing bulk action: {str(e)}')

    return redirect('view_kai_reports')


@login_required
@log_function_call
def manage_kai_templates(request):
    """Manage Kai report templates (for chairs only)"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can manage templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    templates = KaiReportTemplate.objects.all()

    context = {
        'templates': templates,
        'kai_committee': kai_committee,
    }

    return render(request, 'kai/manage_templates.html', context)


@login_required
@log_function_call
def create_kai_template(request):
    """Create a new Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can create templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description')
        category = request.POST.get('category')
        title_template = request.POST.get('title_template')
        description_template = request.POST.get('description_template')
        suggested_tags_str = request.POST.get('suggested_tags', '')
        suggested_tags = [t.strip() for t in suggested_tags_str.split(',') if t.strip()]
        is_active = request.POST.get('is_active') == 'on'

        if name and description and category and title_template and description_template:
            template = KaiReportTemplate.objects.create(
                name=name,
                description=description,
                category=category,
                title_template=title_template,
                description_template=description_template,
                suggested_tags=suggested_tags,
                is_active=is_active,
                created_by=request.user
            )
            messages.success(request, f'Template "{template.name}" created successfully.')
            return redirect('manage_kai_templates')
        else:
            messages.error(request, 'Please fill in all required fields.')

    context = {
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
    }

    return render(request, 'kai/create_template.html', context)


@login_required
@log_function_call
def edit_kai_template(request, template_id):
    """Edit an existing Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can edit templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    template = get_object_or_404(KaiReportTemplate, id=template_id)

    if request.method == 'POST':
        template.name = request.POST.get('name')
        template.description = request.POST.get('description')
        template.category = request.POST.get('category')
        template.title_template = request.POST.get('title_template')
        template.description_template = request.POST.get('description_template')
        suggested_tags_str = request.POST.get('suggested_tags', '')
        template.suggested_tags = [t.strip() for t in suggested_tags_str.split(',') if t.strip()]
        template.is_active = request.POST.get('is_active') == 'on'
        template.save()

        messages.success(request, f'Template "{template.name}" updated successfully.')
        return redirect('manage_kai_templates')

    context = {
        'template': template,
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
    }

    return render(request, 'kai/edit_template.html', context)


@login_required
def delete_kai_template(request, template_id):
    """Delete a Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can delete templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    template = get_object_or_404(KaiReportTemplate, id=template_id)
    template_name = template.name
    template.delete()

    messages.success(request, f'Template "{template_name}" deleted successfully.')
    return redirect('manage_kai_templates')


def track_kai_accused_email_view(request, report_id):
    """
    Track when an accused person views their notification email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    import base64
    import logging

    logger = logging.getLogger('function_calls')

    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        report = KaiReport.objects.get(id=report_id)
        logger.info(f"Kai email tracking pixel accessed for report {report_id}")

        # Only update if notified and not already viewed
        if report.accused_notified:
            # Check if already viewed
            current_viewed = getattr(report, 'accused_email_viewed_at', None)
            if not current_viewed:
                report.accused_email_viewed_at = timezone.now()
                report.save()  # Full save to handle migration issues
                logger.info(f"Marked Kai report {report_id} accused email as viewed")

                # Log the view in activity log
                try:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=None,  # System action
                        action='status_changed',
                        details='Accused person viewed notification email'
                    )
                except Exception as e:
                    logger.error(f"Failed to log activity for report {report_id}: {e}")
    except KaiReport.DoesNotExist:
        logger.warning(f"Kai email tracking: Report {report_id} not found")
    except Exception as e:
        logger.error(f"Kai email tracking error for report {report_id}: {e}")

    return HttpResponse(PIXEL_GIF, content_type='image/gif')


def track_kai_submitter_email_view(request, report_id):
    """
    Track when a submitter views their outcome notification email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    import base64
    import logging

    logger = logging.getLogger('function_calls')

    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        report = KaiReport.objects.get(id=report_id)
        logger.info(f"Kai submitter email tracking pixel accessed for report {report_id}")

        # Only update if notified and not already viewed
        if report.submitter_notified_at:
            if not report.submitter_email_viewed_at:
                report.submitter_email_viewed_at = timezone.now()
                report.save()
                logger.info(f"Marked Kai report {report_id} submitter email as viewed")

                try:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=None,  # System action
                        action='status_changed',
                        details='Submitter viewed outcome notification email'
                    )
                except Exception as e:
                    logger.error(f"Failed to log activity for report {report_id}: {e}")
    except KaiReport.DoesNotExist:
        logger.warning(f"Kai submitter email tracking: Report {report_id} not found")
    except Exception as e:
        logger.error(f"Kai submitter email tracking error for report {report_id}: {e}")

    return HttpResponse(PIXEL_GIF, content_type='image/gif')
