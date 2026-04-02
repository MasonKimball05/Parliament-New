from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from src.models import Announcement, UserAnnouncementView, ParliamentUser, AnnouncementEmailLog, AnnouncementEmailRecipient
from src.forms import AnnouncementForm
from src.decorators import log_function_call, officer_required
from src.notifications import send_announcement_notification, get_site_url
from src.notification_service import notify_all_active_members
from django.utils import timezone
import base64
import logging

logger = logging.getLogger('src')

@login_required
@officer_required
@log_function_call
def manage_announcements(request):
    """View to manage all announcements"""
    announcements = Announcement.objects.all().order_by('-posted_at')

    # Pagination - 25 announcements per page
    paginator = Paginator(announcements, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'officer/manage_announcements.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'total_count': paginator.count,
    })

@login_required
@officer_required
@log_function_call
def create_announcement(request):
    """View to create a new announcement"""
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.posted_by = request.user

            # Check if user wants to send email notifications
            send_email = request.POST.get('send_email') == 'on'

            # If scheduled for later and user wants emails, remember that preference
            if not announcement.is_published() and send_email:
                announcement.send_email_on_publish = True

            announcement.save()

            # Note: We don't create in-app notifications for announcements because
            # announcements have their own dedicated display system (home page popup,
            # announcements page) with UserAnnouncementView tracking. This saves
            # significant database space (~1 row per member per announcement).

            # If send_email is checked and announcement is published, redirect to confirmation
            if announcement.is_published() and send_email:
                return redirect('confirm_announcement_email', announcement_id=announcement.id)
            elif announcement.is_published():
                messages.success(request, 'Announcement created successfully!')
            elif send_email:
                messages.success(request, 'Announcement scheduled! Emails will be sent automatically when published.')
            else:
                messages.success(request, 'Announcement created and scheduled for publication!')

            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(initial={'is_active': True})

    return render(request, 'officer/create_announcement.html', {
        'form': form
    })


@login_required
@officer_required
@log_function_call
def confirm_announcement_email(request, announcement_id):
    """
    Show confirmation page before sending announcement emails.
    Displays exactly who will receive the email.
    """
    announcement = get_object_or_404(Announcement, id=announcement_id)

    # Calculate who would receive the email (same logic as send_announcement_notification)
    all_active_users = ParliamentUser.objects.filter(member_status='Active')

    if announcement.visible_to:
        member_types = list(announcement.visible_to)
        if 'Member' in member_types:
            member_types.extend(['Chair', 'Officer'])
        targeted_users = all_active_users.filter(member_type__in=member_types)
        excluded_by_visibility = all_active_users.exclude(member_type__in=member_types)
    else:
        member_types = None  # All types
        targeted_users = all_active_users
        excluded_by_visibility = ParliamentUser.objects.none()

    # Filter to users with valid emails who want notifications
    users_with_email = targeted_users.filter(
        email__isnull=False
    ).filter(
        Q(preferences__email_announcements=True) | Q(preferences__isnull=True)
    ).exclude(email='')

    # Users who match visibility but won't receive email
    users_no_email = targeted_users.exclude(
        user_id__in=users_with_email.values_list('user_id', flat=True)
    )

    # Group by member type for display
    recipients_by_type = {}
    for user in users_with_email:
        if user.member_type not in recipients_by_type:
            recipients_by_type[user.member_type] = []
        recipients_by_type[user.member_type].append(user)

    excluded_by_type = {}
    for user in excluded_by_visibility:
        if user.member_type not in excluded_by_type:
            excluded_by_type[user.member_type] = []
        excluded_by_type[user.member_type].append(user)

    context = {
        'announcement': announcement,
        'visible_to': announcement.visible_to or ['All Members'],
        'expanded_types': member_types or ['All Types'],
        'recipients_count': users_with_email.count(),
        'recipients_by_type': recipients_by_type,
        'no_email_count': users_no_email.count(),
        'excluded_count': excluded_by_visibility.count(),
        'excluded_by_type': excluded_by_type,
    }

    return render(request, 'officer/confirm_announcement_email.html', context)


@login_required
@officer_required
@log_function_call
def send_announcement_emails(request, announcement_id):
    """
    Actually send the announcement emails after confirmation.
    Uses pre-warmed data if available for faster sending.
    """
    from django.core.mail import EmailMultiAlternatives

    if request.method != 'POST':
        return redirect('manage_announcements')

    announcement = get_object_or_404(Announcement, id=announcement_id)
    cache_key = f'email_warmup_{announcement_id}'
    warmup_data = cache.get(cache_key)

    if warmup_data:
        # Use pre-warmed data for faster sending
        logger.info(f"[SEND] Using pre-warmed data for announcement {announcement_id}")

        try:
            log_id = warmup_data.get('log_id')

            # Verify the log still exists and is in warming_up state
            try:
                email_log = AnnouncementEmailLog.objects.get(id=log_id)
                if email_log.status != 'warming_up':
                    logger.warning(f"[SEND] Warmup log {log_id} has status '{email_log.status}', not 'warming_up'. Falling back to regular send.")
                    cache.delete(cache_key)
                    warmup_data = None
            except AnnouncementEmailLog.DoesNotExist:
                logger.warning(f"[SEND] Warmup log {log_id} no longer exists. Falling back to regular send.")
                cache.delete(cache_key)
                warmup_data = None
        except Exception as e:
            logger.warning(f"[SEND] Error checking warmup log: {e}. Falling back to regular send.")
            cache.delete(cache_key)
            warmup_data = None

    if warmup_data:
        # Continue with warmup send (log was verified to exist)
        try:
            log_id = warmup_data.get('log_id')
            email_log = AnnouncementEmailLog.objects.get(id=log_id)
            rendered_emails = warmup_data.get('rendered_emails', {})
            subject = warmup_data.get('subject')
            from_email = warmup_data.get('from_email')

            # Immediately mark as 'started' to prevent cancel race condition
            email_log.status = 'started'
            email_log.save(update_fields=['status'])

            # Console log buffer
            console = []
            def log_msg(msg):
                console.append(f"[{timezone.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")

            log_msg("=" * 60)
            log_msg("SENDING EMAILS (Using Pre-warmed Data)")
            log_msg("=" * 60)
            log_msg(f"Pre-rendered emails available: {len(rendered_emails)}")

            sent_count = 0
            failed_count = 0

            for user_id, email_data in rendered_emails.items():
                recipient = AnnouncementEmailRecipient.objects.filter(
                    email_log=email_log,
                    user_id=user_id
                ).first()

                try:
                    msg = EmailMultiAlternatives(
                        subject=subject,
                        body=email_data['plain'],
                        from_email=from_email,
                        to=[email_data['email']]
                    )
                    msg.attach_alternative(email_data['html'], "text/html")
                    msg.send()

                    sent_count += 1
                    if recipient:
                        recipient.status = 'sent'
                        recipient.save()
                    log_msg(f"  SENT: {email_data['name']} <{email_data['email']}>")

                except Exception as e:
                    failed_count += 1
                    if recipient:
                        recipient.status = 'failed'
                        recipient.error_message = str(e)
                        recipient.save()
                    log_msg(f"  FAIL: {email_data['name']} <{email_data['email']}> - {str(e)}")

            # Update email log
            log_msg("")
            log_msg("=" * 60)
            log_msg("COMPLETE")
            log_msg("=" * 60)
            log_msg(f"Emails sent: {sent_count}")
            log_msg(f"Emails failed: {failed_count}")

            if failed_count == 0 and sent_count > 0:
                email_log.status = 'completed'
            elif sent_count > 0 and failed_count > 0:
                email_log.status = 'partial'
            elif sent_count == 0 and failed_count > 0:
                email_log.status = 'failed'
            else:
                email_log.status = 'completed'

            email_log.emails_sent = sent_count
            email_log.emails_failed = failed_count
            email_log.completed_at = timezone.now()
            email_log.console_log = '\n'.join(console)
            email_log.save()

            # Clear warmup cache
            cache.delete(cache_key)

            messages.success(request, f'Announcement created and {sent_count} email notifications sent!')

        except Exception as e:
            logger.error(f"[SEND] Failed using warmup data: {e}", exc_info=True)
            cache.delete(cache_key)
            # Fall back to regular send
            try:
                sent_count = send_announcement_notification(announcement, initiated_by=request.user)
                messages.success(request, f'Announcement created and {sent_count} email notifications sent!')
            except Exception as e2:
                messages.warning(request, f'Announcement created but email notifications failed: {str(e2)}')
    else:
        # No warmup data, use regular send
        logger.info(f"[SEND] No warmup data, using regular send for announcement {announcement_id}")
        try:
            sent_count = send_announcement_notification(announcement, initiated_by=request.user)
            messages.success(request, f'Announcement created and {sent_count} email notifications sent!')
        except Exception as e:
            messages.warning(request, f'Announcement created but email notifications failed: {str(e)}')

    return redirect('manage_announcements')


@login_required
@officer_required
@log_function_call
def skip_announcement_email(request, announcement_id):
    """
    Skip sending emails for an announcement (user cancelled from confirmation page).
    Also cleans up any warmup data.
    """
    # Clean up warmup data if it exists
    cache_key = f'email_warmup_{announcement_id}'
    warmup_data = cache.get(cache_key)

    if warmup_data:
        log_id = warmup_data.get('log_id')
        if log_id:
            try:
                email_log = AnnouncementEmailLog.objects.get(id=log_id, status='warming_up')
                # Delete recipients to save space
                email_log.recipients.all().delete()
                # Mark as cancelled
                email_log.status = 'cancelled'
                email_log.completed_at = timezone.now()
                email_log.console_log = f"[{timezone.now().strftime('%H:%M:%S')}] Email send skipped by user"
                email_log.save()
            except AnnouncementEmailLog.DoesNotExist:
                pass
        cache.delete(cache_key)
    else:
        # Fall back to deleting any orphaned warming_up logs
        AnnouncementEmailLog.objects.filter(
            announcement_id=announcement_id,
            status='warming_up'
        ).delete()

    messages.success(request, 'Announcement created successfully! (No emails sent)')
    return redirect('manage_announcements')


@login_required
@officer_required
@require_POST
def warmup_announcement_email(request, announcement_id):
    """
    Pre-warm the email sending process by:
    1. Creating the email log entry
    2. Pre-creating all recipient records
    3. Pre-rendering email templates and caching them

    This runs in the background while the user reviews the confirmation page.
    """
    announcement = get_object_or_404(Announcement, id=announcement_id)
    cache_key = f'email_warmup_{announcement_id}'

    # Check if warmup already exists
    existing_warmup = cache.get(cache_key)
    if existing_warmup:
        return JsonResponse({'status': 'already_warming', 'log_id': existing_warmup.get('log_id')})

    try:
        # Get all users for comprehensive processing
        all_users = ParliamentUser.objects.all()
        all_active_users = ParliamentUser.objects.filter(member_status='Active')

        # Determine member types to target
        if announcement.visible_to:
            member_types = list(announcement.visible_to)
            if 'Member' in member_types:
                member_types.extend(['Chair', 'Officer'])
            targeted_users = all_active_users.filter(member_type__in=member_types)
        else:
            member_types = None
            targeted_users = all_active_users

        # Filter to users with valid emails who want notifications
        users_to_email = targeted_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__email_announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        # Create the email log entry with warming_up status
        email_log = AnnouncementEmailLog.objects.create(
            announcement=announcement,
            initiated_by=request.user,
            visible_to_raw=announcement.visible_to,
            expanded_member_types=member_types,
            total_active_users=all_active_users.count(),
            users_matching_visibility=targeted_users.count(),
            users_with_valid_email=users_to_email.count(),
            status='warming_up'
        )

        # Pre-create all recipient records
        recipients_to_create = []
        for user in all_users:
            if user.member_status != 'Active':
                user_status = 'skipped_inactive'
            elif member_types is not None and user.member_type not in member_types:
                user_status = 'skipped_visibility'
            elif not user.email or not user.email.strip():
                user_status = 'skipped_no_email'
            elif hasattr(user, 'preferences') and user.preferences and not user.preferences.email_announcements:
                user_status = 'skipped_disabled'
            else:
                user_status = 'pending'

            recipients_to_create.append(AnnouncementEmailRecipient(
                email_log=email_log,
                user=user,
                user_name=user.get_display_name() if hasattr(user, 'get_display_name') else user.name,
                user_email=user.email or '',
                user_member_type=user.member_type,
                user_member_status=user.member_status,
                status=user_status
            ))

        AnnouncementEmailRecipient.objects.bulk_create(recipients_to_create)

        # Pre-render email templates for users who will receive emails
        site_url = get_site_url()
        subject = f"New Announcement: {announcement.title}"
        rendered_emails = {}

        for user in users_to_email:
            tracking_url = f"{site_url}/track/announcement/{announcement.id}/user/{user.user_id}/"
            html_message = render_to_string('emails/announcement_notification.html', {
                'announcement': announcement,
                'site_url': site_url,
                'tracking_url': tracking_url,
                'user': user,
            })
            plain_message = strip_tags(html_message)
            rendered_emails[user.user_id] = {
                'html': html_message,
                'plain': plain_message,
                'email': user.email,
                'name': user.name,
            }

        # Store warmup data in cache (expires in 10 minutes)
        warmup_data = {
            'log_id': email_log.id,
            'subject': subject,
            'rendered_emails': rendered_emails,
            'from_email': settings.DEFAULT_FROM_EMAIL,
            'site_url': site_url,
        }
        cache.set(cache_key, warmup_data, timeout=600)  # 10 minutes

        logger.info(f"[WARMUP] Pre-warmed email send for announcement {announcement_id}: {len(rendered_emails)} emails ready")

        return JsonResponse({
            'status': 'success',
            'log_id': email_log.id,
            'emails_prepared': len(rendered_emails),
        })

    except Exception as e:
        logger.error(f"[WARMUP] Failed to warmup announcement {announcement_id}: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
@officer_required
def cancel_warmup_announcement_email(request, announcement_id):
    """
    Cancel a warmup operation and mark the log as cancelled.
    Called when user decides to skip sending emails or navigates away.
    Accepts both POST and sendBeacon requests.
    """
    if request.method not in ['POST']:
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    cache_key = f'email_warmup_{announcement_id}'
    warmup_data = cache.get(cache_key)

    if warmup_data:
        # Mark the log as cancelled (keep for audit trail) and delete recipients
        log_id = warmup_data.get('log_id')
        if log_id:
            try:
                email_log = AnnouncementEmailLog.objects.get(id=log_id, status='warming_up')
                # Delete recipients to save space
                email_log.recipients.all().delete()
                # Mark as cancelled
                email_log.status = 'cancelled'
                email_log.completed_at = timezone.now()
                email_log.console_log = f"[{timezone.now().strftime('%H:%M:%S')}] Warmup cancelled by user"
                email_log.save()
            except AnnouncementEmailLog.DoesNotExist:
                pass  # Already deleted or status changed
        cache.delete(cache_key)
        logger.info(f"[WARMUP] Cancelled warmup for announcement {announcement_id}")

    return JsonResponse({'status': 'cancelled'})

@login_required
@officer_required
@log_function_call
def edit_announcement(request, announcement_id):
    """View to edit an existing announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if request.method == 'POST':
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(request, 'Announcement updated successfully!')
            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(instance=announcement)

    return render(request, 'officer/edit_announcement.html', {
        'form': form,
        'announcement': announcement
    })

@login_required
@officer_required
@log_function_call
def delete_announcement(request, announcement_id):
    """View to delete an announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if request.method == 'POST':
        announcement.delete()
        messages.success(request, 'Announcement deleted successfully!')
        return redirect('manage_announcements')

    return render(request, 'officer/delete_announcement.html', {
        'announcement': announcement
    })

@login_required
@officer_required
@log_function_call
def toggle_announcement_status(request, announcement_id):
    """View to toggle announcement active status"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    announcement.is_active = not announcement.is_active
    announcement.save()

    status = "activated" if announcement.is_active else "deactivated"
    messages.success(request, f'Announcement "{announcement.title}" has been {status}!')
    return redirect('manage_announcements')


def track_email_view(request, announcement_id, user_id):
    """
    Track when an announcement is viewed from email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        announcement = Announcement.objects.get(id=announcement_id)
        user = ParliamentUser.objects.get(user_id=user_id)

        # Record or update the view
        view, created = UserAnnouncementView.objects.get_or_create(
            user=user,
            announcement=announcement,
            defaults={'view_source': 'email'}
        )

        # If already viewed on site, update to show email view happened
        if not created and view.view_source == 'site':
            # Keep as site view but note they also saw email
            pass
    except (Announcement.DoesNotExist, ParliamentUser.DoesNotExist):
        pass

    return HttpResponse(PIXEL_GIF, content_type='image/gif')


@login_required
@officer_required
def announcement_stats(request, announcement_id):
    """View detailed statistics for an announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    stats = announcement.get_view_stats()
    viewers = announcement.get_viewers()

    # Get users who haven't viewed
    viewed_user_ids = viewers.values_list('user_id', flat=True)
    target_users = ParliamentUser.objects.filter(member_status='Active')
    if announcement.visible_to:
        visible_types = list(announcement.visible_to)
        if 'Member' in visible_types:
            visible_types.extend(['Chair', 'Officer'])
        target_users = target_users.filter(member_type__in=visible_types)

    non_viewers = target_users.exclude(user_id__in=viewed_user_ids)

    context = {
        'announcement': announcement,
        'stats': stats,
        'viewers': viewers,
        'non_viewers': non_viewers,
    }
    return render(request, 'officer/announcement_stats.html', context)
