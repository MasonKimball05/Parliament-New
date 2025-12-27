from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponse
import csv
from src.models import KaiReport, Committee, ParliamentUser, KaiReportActivity, KaiReportTemplate
from src.forms import KaiReportForm
from src.decorators import log_function_call


@login_required
@log_function_call
def submit_kai_report(request):
    """Allow any logged-in user to submit a Kai report"""
    # Check if KaiReport table exists
    try:
        if request.method == 'POST':
            form = KaiReportForm(request.POST, request.FILES)
            if form.is_valid():
                report = form.save(commit=False)
                report.submitted_by = request.user
                report.save()

                # Log activity
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='created',
                    details=f'Report created with category: {report.get_category_display()}'
                )

                # Send email notification to Kai committee chair(s) only (NOT targeted person yet)
                try:
                    kai_committee = Committee.objects.get(code='KAI')
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
Submitted at: {report.submitted_at.strftime('%B %d, %Y at %I:%M %p')}
{f"Directed to: {report.targeted_to.name}" if report.targeted_to else ""}

Description:
{report.description}

Tags: {report.tags if report.tags else 'None'}

Please log in to the Kai Committee page to review this report.
                        """

                        send_mail(
                            subject,
                            message,
                            settings.DEFAULT_FROM_EMAIL,
                            recipient_emails,
                            fail_silently=True,
                        )
                except Committee.DoesNotExist:
                    pass  # Kai committee doesn't exist yet
                except Exception as e:
                    # Log error but don't fail the submission
                    import logging
                    logger = logging.getLogger('function_calls')
                    logger.error(f"Failed to send Kai report email: {e}")

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

        return render(request, 'kai/submit_report.html', {
            'form': form,
            'templates': templates,
        })
    except Exception as e:
        # Table doesn't exist yet
        import logging
        logger = logging.getLogger('function_calls')
        logger.error(f"Error in submit_kai_report: {e}")
        messages.warning(request, f'Kai Reports feature error: {str(e)}')
        return redirect('home')


@login_required
@log_function_call
def view_kai_reports(request):
    """View for Kai chairs to see all submitted reports"""
    # Check if user is a Kai chair
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can access this page.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
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

        # Get counts for category filters
        category_counts = {}
        for cat_value, cat_label in KaiReport.CATEGORY_CHOICES:
            category_counts[cat_value] = KaiReport.objects.filter(category=cat_value).count()
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
        messages.info(request, 'Kai Reports database table not yet created. This is a preview of the interface.')

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
    }

    return render(request, 'kai/view_reports.html', context)


@login_required
@log_function_call
def export_kai_reports_csv(request):
    """Export filtered Kai reports to CSV"""
    # Check if user is a Kai chair
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can export reports.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
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
                report.submitted_by.name,
                report.targeted_to.name if report.targeted_to else '',
                report.submitted_at.strftime('%Y-%m-%d %H:%M:%S'),
                report.get_status_display(),
                report.get_deliberation_outcome_display(),
                'Yes' if report.closed_by_accused_request else 'No',
                report.reviewed_by.name if report.reviewed_by else '',
                report.reviewed_at.strftime('%Y-%m-%d %H:%M:%S') if report.reviewed_at else '',
                report.tags,
                report.description
            ])

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

    # Check if user is a Kai chair
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can manage reports.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    if request.method == 'POST':
        action = request.POST.get('action')

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

            # Send email notification to submitter
            try:
                if report.submitted_by.email:
                    subject = f'Kai Report Update: {report.title}'
                    message = f"""
Your Kai report has been reviewed.

Report Title: {report.title}
Status: Reviewed
Reviewed by: {request.user.name}
Reviewed at: {timezone.now().strftime('%B %d, %Y at %I:%M %p')}

You can view the full report details at the Kai Committee page.
                    """
                    send_mail(
                        subject,
                        message,
                        settings.DEFAULT_FROM_EMAIL,
                        [report.submitted_by.email],
                        fail_silently=True,
                    )
            except Exception as e:
                import logging
                logger = logging.getLogger('function_calls')
                logger.error(f"Failed to send status update email: {e}")

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

        elif action == 'update_tags':
            report.tags = request.POST.get('tags', '')
            report.save()
            messages.success(request, 'Tags updated successfully.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='tags_updated',
                details=f'Tags updated to: {report.tags if report.tags else "none"}'
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

                # Send email notifications about outcome (ONLY to targeted person, NOT submitter)
                if old_outcome != deliberation_outcome and report.targeted_to and report.targeted_to.email:
                    try:
                        outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)

                        # Notify targeted person about deliberation outcome
                        if deliberation_outcome == 'heard':
                            subject = 'Kai Committee Notification - Case Heard'
                            message = f"""
This is to inform you that a report has been submitted to the Kai Committee that involves you.

The Kai Committee has decided to hear this case and may reach out to you for further information.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {timezone.now().strftime('%B %d, %Y at %I:%M %p')}
                            """
                        elif deliberation_outcome == 'thrown_out':
                            subject = 'Kai Committee Notification - Case Resolved'
                            message = f"""
This is to inform you that a report submitted to the Kai Committee that involved you has been resolved.

The case has been thrown out and no further action is required from you.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {timezone.now().strftime('%B %d, %Y at %I:%M %p')}
                            """
                        elif deliberation_outcome == 'pending':
                            # Don't notify for pending status
                            message = None

                        if message:
                            send_mail(
                                subject,
                                message,
                                settings.DEFAULT_FROM_EMAIL,
                                [report.targeted_to.email],
                                fail_silently=True,
                            )
                    except Exception as e:
                        import logging
                        logger = logging.getLogger('function_calls')
                        logger.error(f"Failed to send deliberation update email: {e}")
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
                        outcome_display = dict(report.DELIBERATION_CHOICES).get(report.deliberation_outcome, 'Pending')

                        subject = f'Kai Report Update: {report.title}'
                        message = f"""
This is a notification regarding your Kai report.

Report Title: {report.title}
Deliberation Outcome: {outcome_display}

Committee Notes:
{report.committee_notes if report.committee_notes else 'No additional notes provided.'}

If you have any questions, please contact the Kai Committee chair(s).

Notified at: {timezone.now().strftime('%B %d, %Y at %I:%M %p')}
                        """

                        send_mail(
                            subject,
                            message,
                            settings.DEFAULT_FROM_EMAIL,
                            [report.submitted_by.email],
                            fail_silently=False,
                        )

                        # Log activity
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='status_changed',
                            details=f'Submitter ({report.submitted_by.name}) notified of deliberation outcome'
                        )

                        messages.success(request, f'Submitter ({report.submitted_by.name}) has been notified via email.')
                    else:
                        messages.warning(request, f'Submitter ({report.submitted_by.name}) does not have an email address on file.')
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

                    messages.success(request, f'Unlinked from report: {related_report.title}')
                except KaiReport.DoesNotExist:
                    messages.error(request, 'Related report not found.')
            else:
                messages.error(request, 'No report selected.')

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

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'related_reports': related_reports,
        'available_reports': available_reports,
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

    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can view report details.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    # Get activity log
    try:
        activity_log = list(report.activity_log.all().select_related('user'))
    except:
        activity_log = []

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'print_date': timezone.now(),
    }

    return render(request, 'kai/print_report.html', context)


@login_required
@log_function_call
def kai_dashboard(request):
    """Dashboard with statistics and charts for Kai reports"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can access the dashboard.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    try:
        from django.db.models import Count, Q
        from datetime import datetime, timedelta
        import json

        # Get counts by status
        total_reports = KaiReport.objects.count()
        pending_count = KaiReport.objects.filter(status='pending').count()
        reviewed_count = KaiReport.objects.filter(status='reviewed').count()
        archived_count = KaiReport.objects.filter(status='archived').count()

        # Get counts by category
        category_data = {}
        for cat_value, cat_label in KaiReport.CATEGORY_CHOICES:
            count = KaiReport.objects.filter(category=cat_value).count()
            category_data[cat_label] = count

        # Get counts by deliberation outcome
        outcome_pending = KaiReport.objects.filter(deliberation_outcome='pending').count()
        outcome_heard = KaiReport.objects.filter(deliberation_outcome='heard').count()
        outcome_thrown_out = KaiReport.objects.filter(deliberation_outcome='thrown_out').count()

        # Get monthly submission trends (last 6 months)
        six_months_ago = timezone.now() - timedelta(days=180)
        monthly_data = {}

        # Generate last 6 months
        current_date = timezone.now()
        for i in range(6):
            month_date = current_date - timedelta(days=30*i)
            month_key = month_date.strftime('%b %Y')
            month_start = month_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            # Calculate next month start
            if month_date.month == 12:
                next_month_start = month_date.replace(year=month_date.year + 1, month=1, day=1)
            else:
                next_month_start = month_date.replace(month=month_date.month + 1, day=1)

            count = KaiReport.objects.filter(
                submitted_at__gte=month_start,
                submitted_at__lt=next_month_start
            ).count()

            monthly_data[month_key] = count

        # Reverse to show oldest to newest
        monthly_data = dict(reversed(list(monthly_data.items())))

        # Get recent activity (last 10 activities across all reports)
        recent_activities = list(
            KaiReportActivity.objects.all()
            .select_related('report', 'user')
            .order_by('-timestamp')[:10]
        )

        # Get most recent reports
        recent_reports = list(
            KaiReport.objects.all()
            .select_related('submitted_by', 'targeted_to')
            .order_by('-submitted_at')[:5]
        )

        context = {
            'kai_committee': kai_committee,
            'total_reports': total_reports,
            'pending_count': pending_count,
            'reviewed_count': reviewed_count,
            'archived_count': archived_count,
            'category_data': json.dumps(category_data),
            'outcome_pending': outcome_pending,
            'outcome_heard': outcome_heard,
            'outcome_thrown_out': outcome_thrown_out,
            'monthly_data': json.dumps(monthly_data),
            'recent_activities': recent_activities,
            'recent_reports': recent_reports,
        }

        return render(request, 'kai/dashboard.html', context)

    except Exception as e:
        messages.error(request, f'Error loading dashboard: {str(e)}')
        return redirect('home')


@login_required
@log_function_call
def bulk_actions_kai_reports(request):
    """Handle bulk actions on multiple Kai reports"""
    if request.method != 'POST':
        return redirect('view_kai_reports')

    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
            messages.error(request, 'Only Kai chairs can perform bulk actions.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    # Get selected report IDs and action
    report_ids = request.POST.getlist('report_ids')
    action = request.POST.get('bulk_action')

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
                    report.tags,
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
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
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
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
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
        suggested_tags = request.POST.get('suggested_tags', '')
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
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
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
        template.suggested_tags = request.POST.get('suggested_tags', '')
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
        kai_committee = Committee.objects.get(code='KAI')
        if not kai_committee.is_chair(request.user) and not request.user.is_admin:
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
