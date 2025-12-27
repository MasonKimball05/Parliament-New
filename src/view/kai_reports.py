from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from src.models import KaiReport, Committee, ParliamentUser
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

                # Send email notification to Kai committee chair(s) and targeted person
                try:
                    kai_committee = Committee.objects.get(code='KAI')
                    kai_chairs = kai_committee.chairs.all()

                    # Collect all emails to notify
                    recipient_emails = []

                    # Add Kai chair emails
                    if kai_chairs.exists():
                        chair_emails = [chair.email for chair in kai_chairs if chair.email]
                        recipient_emails.extend(chair_emails)

                    # Add targeted person's email if specified
                    if report.targeted_to and report.targeted_to.email:
                        if report.targeted_to.email not in recipient_emails:
                            recipient_emails.append(report.targeted_to.email)

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

                messages.success(request, 'Your Kai report has been submitted successfully! The Kai chair(s) and targeted person (if specified) have been notified.')
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

        return render(request, 'kai/submit_report.html', {'form': form})
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

        # Get reports based on filter
        if status_filter == 'pending':
            reports = KaiReport.objects.filter(status='pending')
        elif status_filter == 'reviewed':
            reports = KaiReport.objects.filter(status='reviewed')
        elif status_filter == 'archived':
            reports = KaiReport.objects.filter(status='archived')
        else:  # 'all'
            reports = KaiReport.objects.all()

        # Try select_related for production, fallback without it for test
        try:
            reports = list(reports.select_related('submitted_by', 'reviewed_by', 'targeted_to'))
        except:
            # Test database missing columns - query without select_related
            reports = list(reports)

        # Get counts for filters
        counts = {
            'all': KaiReport.objects.count(),
            'pending': KaiReport.objects.filter(status='pending').count(),
            'reviewed': KaiReport.objects.filter(status='reviewed').count(),
            'archived': KaiReport.objects.filter(status='archived').count(),
        }
    except Exception:
        # Table doesn't exist yet - show empty state
        reports = []
        status_filter = request.GET.get('status', 'all')
        counts = {
            'all': 0,
            'pending': 0,
            'reviewed': 0,
            'archived': 0,
        }
        messages.info(request, 'Kai Reports database table not yet created. This is a preview of the interface.')

    context = {
        'reports': reports,
        'status_filter': status_filter,
        'counts': counts,
        'kai_committee': kai_committee
    }

    return render(request, 'kai/view_reports.html', context)


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

        elif action == 'mark_pending':
            report.status = 'pending'
            report.reviewed_by = None
            report.reviewed_at = None
            report.save()
            messages.success(request, f'Report "{report.title}" marked as pending.')

        elif action == 'archive':
            report.status = 'archived'
            report.save()
            messages.success(request, f'Report "{report.title}" archived.')

        elif action == 'update_notes':
            report.chair_notes = request.POST.get('chair_notes', '')
            report.save()
            messages.success(request, 'Notes updated successfully.')

        elif action == 'update_tags':
            report.tags = request.POST.get('tags', '')
            report.save()
            messages.success(request, 'Tags updated successfully.')

        elif action == 'update_deliberation':
            deliberation_outcome = request.POST.get('deliberation_outcome')
            committee_notes = request.POST.get('committee_notes', '')
            closed_by_accused = request.POST.get('closed_by_accused_request') == 'on'

            if deliberation_outcome:
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
            else:
                messages.error(request, 'Please select a deliberation outcome.')

        return redirect('manage_kai_report', report_id=report.id)

    context = {
        'report': report,
        'kai_committee': kai_committee
    }

    return render(request, 'kai/manage_report.html', context)
