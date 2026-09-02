import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.db.models import Count, Q
from django.views.decorators.http import require_POST
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from src.models import Announcement, UserAnnouncementView, ParliamentUser, AnnouncementEmailLog, AnnouncementEmailRecipient, CommitteeDocument, AnnouncementPoll, AnnouncementPollQuestion, AnnouncementPollOption
from src.forms import AnnouncementForm
from src.decorators import log_function_call, officer_required
from src.notifications import send_announcement_notification, get_site_url
from src.notification_service import notify_all_active_members
from django.utils import timezone
from django.utils.timezone import localtime
import base64
import logging
from src.models.users import member_defer

logger = logging.getLogger('src')

@login_required
@officer_required
@log_function_call
def manage_announcements(request):
    """View to manage all announcements"""
    # v3.17.4: the template calls `announcement.get_view_stats` per row, which
    # was four queries each, and `{% if announcement.poll %}` was a fifth — about
    # 100 queries for a 25-row page. Now: the three view counts are annotated,
    # `poll` is joined (reverse OneToOne), and the active-member counts per type
    # are fetched once and handed to each object so the target-audience number
    # needs no query either.
    announcements = Announcement.annotate_view_stats(
        Announcement.objects
        .select_related('posted_by', 'poll')
        .defer(*member_defer('posted_by'))
    ).order_by('-posted_at')

    # Pagination - 25 announcements per page
    paginator = Paginator(announcements, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    active_counts = Announcement.active_counts_by_member_type()
    for announcement in page_obj:
        announcement._active_counts_by_type = active_counts

    # v3.28.6: freeze the target-audience snapshot for every row that needs
    # one BEFORE the template calls get_view_stats() per row — one query for
    # the roster and one bulk_update for however many rows are unsnapshotted,
    # rather than one of each per row. See
    # Announcement.ensure_target_audience_snapshots()'s docstring; skipping
    # this and relying on get_view_stats()'s own per-object fallback would
    # reintroduce exactly the N+1 shape active_counts above was built to
    # avoid, just one field over.
    Announcement.ensure_target_audience_snapshots(list(page_obj))

    return render(request, 'officer/manage_announcements.html', {
        'announcements': page_obj,
        'page_obj': page_obj,
        'total_count': paginator.count,
    })

def _save_poll_from_post(post, announcement, user):
    """
    Create or update a poll from form POST data.
    Called from create_announcement when poll fields are submitted alongside the
    announcement form. Uses the same field names as create_or_edit_poll so the
    template JS is reusable.
    """
    from django.utils.dateparse import parse_datetime

    title = post.get('poll_title', '').strip()
    if not title:
        return

    poll, _ = AnnouncementPoll.objects.get_or_create(
        announcement=announcement,
        defaults={'created_by': user},
    )
    poll.title = title
    poll.description = post.get('poll_description', '').strip()
    poll.is_anonymous = post.get('is_anonymous') == 'on'
    poll.is_open = post.get('is_open') == 'on'
    closes_at_raw = post.get('closes_at', '').strip()
    poll.closes_at = parse_datetime(closes_at_raw) if closes_at_raw else None
    poll.save()

    question_indices = sorted(
        set(k.split('_')[-1] for k in post if k.startswith('question_text_')),
        key=lambda x: int(x) if x.isdigit() else 0,
    )
    for order, idx in enumerate(question_indices):
        text = post.get(f'question_text_{idx}', '').strip()
        if not text:
            continue
        q_type = post.get(f'question_type_{idx}', 'single')
        is_required = post.get(f'question_required_{idx}') == 'on'
        question = AnnouncementPollQuestion.objects.create(
            poll=poll, text=text, question_type=q_type,
            is_required=is_required, order=order,
        )
        if q_type in ('single', 'multiple'):
            opt_indices = sorted(
                set(k.split('_')[-1] for k in post if k.startswith(f'option_text_{idx}_')),
                key=lambda x: int(x) if x.isdigit() else 0,
            )
            for opt_order, opt_idx in enumerate(opt_indices):
                opt_text = post.get(f'option_text_{idx}_{opt_idx}', '').strip()
                if opt_text:
                    AnnouncementPollOption.objects.create(
                        question=question, text=opt_text, order=opt_order,
                    )


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

            # Collect individually-selected inactive/alumni user IDs
            extra_ids = request.POST.getlist('extra_user_ids')
            # user_id is a CharField (e.g. "ABC123"), validate they exist
            valid_ids = list(
                ParliamentUser.objects.filter(user_id__in=extra_ids)
                .exclude(member_status='Active').exclude(member_status='Removed')
                .values_list('user_id', flat=True)
            )
            extra_ids_str = ','.join(valid_ids)

            # If scheduled for later and user wants emails, remember that preference
            if not announcement.is_published() and send_email:
                announcement.send_email_on_publish = True

            announcement.save()

            # Save linked documents (M2M — must be after save())
            linked_doc_ids = request.POST.getlist('linked_document_ids')
            if linked_doc_ids:
                valid_docs = CommitteeDocument.objects.filter(
                    id__in=linked_doc_ids, published_to_chapter=True
                )
                announcement.linked_documents.set(valid_docs)
            else:
                announcement.linked_documents.clear()

            # Note: We don't create in-app notifications for announcements because
            # announcements have their own dedicated display system (home page popup,
            # announcements page) with UserAnnouncementView tracking. This saves
            # significant database space (~1 row per member per announcement).

            # Create poll if poll fields were submitted
            if request.POST.get('poll_title', '').strip():
                _save_poll_from_post(request.POST, announcement, request.user)

            # If send_email is checked and announcement is published, redirect to confirmation
            if announcement.is_published() and send_email:
                url = f"{redirect('confirm_announcement_email', announcement_id=announcement.id).url}"
                if extra_ids_str:
                    url += f"?extra_user_ids={extra_ids_str}"
                return redirect(url)
            elif announcement.is_published():
                messages.success(request, 'Announcement created!')
            elif send_email:
                messages.success(request, 'Announcement scheduled! Emails will be sent automatically when published.')
            else:
                messages.success(request, 'Announcement created and scheduled for publication!')

            return redirect('edit_announcement', announcement_id=announcement.id)
    else:
        form = AnnouncementForm(initial={'is_active': True})

    inactive_members = ParliamentUser.objects.exclude(
        member_status='Active'
    ).filter(email__isnull=False).exclude(email='').order_by('member_status', 'name')

    chapter_docs = CommitteeDocument.objects.filter(
        published_to_chapter=True
    ).order_by('title')

    return render(request, 'officer/create_announcement.html', {
        'form': form,
        'inactive_members': inactive_members,
        'chapter_docs': chapter_docs,
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

    # Parse individually-added inactive user IDs from query param
    extra_ids_str = request.GET.get('extra_user_ids', '')
    extra_user_ids = [p.strip() for p in extra_ids_str.split(',') if p.strip()]

    # Base queryset: active members only
    base_users = ParliamentUser.objects.filter(member_status='Active')

    if announcement.visible_to:
        member_types = list(announcement.visible_to)
        if 'Member' in member_types:
            member_types.extend(['Chair', 'Officer'])
        targeted_users = base_users.filter(member_type__in=member_types)
        excluded_by_visibility = base_users.exclude(member_type__in=member_types)
    else:
        member_types = None  # All types
        targeted_users = base_users
        excluded_by_visibility = ParliamentUser.objects.none()

    # Filter active recipients to users with valid emails who want notifications
    active_with_email = targeted_users.filter(
        email__isnull=False
    ).filter(
        Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
    ).exclude(email='')

    # Individually-added inactive users (must have email, must want notifications)
    extra_users = []
    if extra_user_ids:
        extra_qs = ParliamentUser.objects.filter(
            user_id__in=extra_user_ids,
        ).exclude(member_status='Active').exclude(member_status='Removed').filter(
            email__isnull=False
        ).filter(
            Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')
        extra_users = list(extra_qs)

    # All users who will receive email (active matching + individually added inactive)
    users_with_email = list(active_with_email) + extra_users

    # Users who match visibility but won't receive email (active only, no email/disabled)
    users_no_email = targeted_users.exclude(
        user_id__in=active_with_email.values_list('user_id', flat=True)
    )

    # Inactive members available to add individually (have email, not already added)
    added_ids = {u.user_id for u in extra_users}
    inactive_available = list(
        ParliamentUser.objects.exclude(member_status='Active').exclude(member_status='Removed').filter(
            email__isnull=False
        ).exclude(email='').order_by('member_status', 'name')
    )

    # Group active recipients by member type for display
    recipients_by_type = {}
    for user in active_with_email:
        key = user.member_type
        if key not in recipients_by_type:
            recipients_by_type[key] = []
        recipients_by_type[key].append(user)

    excluded_by_type = {}
    for user in excluded_by_visibility:
        if user.member_type not in excluded_by_type:
            excluded_by_type[user.member_type] = []
        excluded_by_type[user.member_type].append(user)

    # Build URL helper: extra_user_ids string with one id added or removed
    def ids_with(uid):
        new_ids = sorted(set(extra_user_ids) | {uid})
        return ','.join(str(i) for i in new_ids)

    def ids_without(uid):
        new_ids = sorted(set(extra_user_ids) - {uid})
        return ','.join(str(i) for i in new_ids)

    # Annotate each inactive_available with add/remove URL
    for member in inactive_available:
        if member.user_id in added_ids:
            member.is_added = True
            member.toggle_url = f"?extra_user_ids={ids_without(member.user_id)}"
        else:
            member.is_added = False
            member.toggle_url = f"?extra_user_ids={ids_with(member.user_id)}"

    context = {
        'announcement': announcement,
        'visible_to': announcement.visible_to or ['All Members'],
        'expanded_types': member_types or ['All Types'],
        'recipients_count': len(users_with_email),
        'recipients_by_type': recipients_by_type,
        'extra_users': extra_users,
        'no_email_count': users_no_email.count(),
        'excluded_count': excluded_by_visibility.count(),
        'excluded_by_type': excluded_by_type,
        'inactive_available': inactive_available,
        'extra_user_ids_str': extra_ids_str,
        'has_inactive_with_email': bool(inactive_available),
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
    extra_ids_str = request.POST.get('extra_user_ids', '')
    extra_user_ids = [x.strip() for x in extra_ids_str.split(',') if x.strip()]
    ids_key = '_'.join(sorted(extra_user_ids)) if extra_user_ids else ''
    cache_key = f'email_warmup_{announcement_id}_{ids_key}' if ids_key else f'email_warmup_{announcement_id}'
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
                console.append(f"[{localtime(timezone.now()).strftime('%H:%M:%S.%f')[:-3]}] {msg}")

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
                        recipient.save(update_fields=['status'])
                    log_msg(f"  SENT: {email_data['name']} <{email_data['email']}>")

                except Exception as e:
                    failed_count += 1
                    if recipient:
                        recipient.status = 'failed'
                        recipient.error_message = str(e)
                        recipient.save(update_fields=['status', 'error_message'])
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
            email_log.save(update_fields=['status', 'emails_sent', 'emails_failed', 'completed_at', 'console_log'])

            # Clear warmup cache
            cache.delete(cache_key)

            messages.success(request, f'Announcement created and {sent_count} email notifications sent! You can add a poll below.')

        except Exception as e:
            logger.error(f"[SEND] Failed using warmup data: {e}", exc_info=True)
            cache.delete(cache_key)
            # Fall back to regular send
            try:
                sent_count = send_announcement_notification(announcement, initiated_by=request.user)
                messages.success(request, f'Announcement created and {sent_count} email notifications sent! You can add a poll below.')
            except Exception as e2:
                messages.warning(request, f'Announcement created but email notifications failed: {str(e2)}')
    else:
        # No warmup data, use regular send
        logger.info(f"[SEND] No warmup data, using regular send for announcement {announcement_id}")
        try:
            sent_count = send_announcement_notification(announcement, initiated_by=request.user)
            messages.success(request, f'Announcement created and {sent_count} email notifications sent! You can add a poll below.')
        except Exception as e:
            messages.warning(request, f'Announcement created but email notifications failed: {str(e)}')

    return redirect('edit_announcement', announcement_id=announcement_id)


@login_required
@officer_required
@log_function_call
def skip_announcement_email(request, announcement_id):
    """
    Skip sending emails for an announcement (user cancelled from confirmation page).
    Also cleans up any warmup data.
    """
    # Cancel any active warmup logs for this announcement
    for email_log in AnnouncementEmailLog.objects.filter(announcement_id=announcement_id, status='warming_up'):
        email_log.recipients.all().delete()
        email_log.status = 'cancelled'
        email_log.completed_at = timezone.now()
        email_log.console_log = f"[{localtime(timezone.now()).strftime('%H:%M:%S')}] Email send skipped by user"
        email_log.save(update_fields=['status', 'completed_at', 'console_log'])

    for extra in ['', '_inactive']:
        cache.delete(f'email_warmup_{announcement_id}{extra}')

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

    try:
        body_data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, Exception):
        body_data = {}

    # Parse individually-added inactive user IDs
    extra_ids_raw = body_data.get('extra_user_ids', [])
    if isinstance(extra_ids_raw, str):
        extra_user_ids = [x.strip() for x in extra_ids_raw.split(',') if x.strip()]
    elif isinstance(extra_ids_raw, list):
        extra_user_ids = [str(x).strip() for x in extra_ids_raw if str(x).strip()]
    else:
        extra_user_ids = []

    # Cache key includes extra user IDs so different recipient sets get different warmups
    ids_key = '_'.join(sorted(extra_user_ids)) if extra_user_ids else ''
    cache_key = f'email_warmup_{announcement_id}_{ids_key}' if ids_key else f'email_warmup_{announcement_id}'

    # Check if warmup already exists
    existing_warmup = cache.get(cache_key)
    if existing_warmup:
        return JsonResponse({'status': 'already_warming', 'log_id': existing_warmup.get('log_id')})

    try:
        # Get all users for comprehensive processing
        all_users = ParliamentUser.objects.all()
        all_active_users = ParliamentUser.objects.filter(member_status='Active')

        # Individually added inactive users
        extra_users_qs = ParliamentUser.objects.filter(
            user_id__in=extra_user_ids
        ).exclude(member_status='Active').exclude(member_status='Removed') if extra_user_ids else ParliamentUser.objects.none()
        extra_user_id_set = set(extra_users_qs.values_list('user_id', flat=True))

        # Determine member types to target
        if announcement.visible_to:
            member_types = list(announcement.visible_to)
            if 'Member' in member_types:
                member_types.extend(['Chair', 'Officer'])
            targeted_users = all_active_users.filter(member_type__in=member_types)
        else:
            member_types = None
            targeted_users = all_active_users

        # Active users with email + individually-added inactive users with email
        active_to_email = targeted_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        extra_to_email = extra_users_qs.filter(
            email__isnull=False
        ).filter(
            Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        users_to_email_count = active_to_email.count() + extra_to_email.count()

        # Create the email log entry with warming_up status
        email_log = AnnouncementEmailLog.objects.create(
            announcement=announcement,
            initiated_by=request.user,
            visible_to_raw=announcement.visible_to,
            expanded_member_types=member_types,
            total_active_users=all_active_users.count(),
            users_matching_visibility=targeted_users.count(),
            users_with_valid_email=users_to_email_count,
            status='warming_up'
        )

        # Pre-create all recipient records
        recipients_to_create = []
        for user in all_users:
            if user.member_status != 'Active' and user.user_id not in extra_user_id_set:
                user_status = 'skipped_inactive'
            elif member_types is not None and user.member_type not in member_types and user.user_id not in extra_user_id_set:
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

        for user in list(active_to_email) + list(extra_to_email):
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
            'cache_key': cache_key,
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

    # Cancel any active warmup by finding it via the AnnouncementEmailLog
    cancelled = AnnouncementEmailLog.objects.filter(
        announcement_id=announcement_id,
        status='warming_up'
    )
    for email_log in cancelled:
        email_log.recipients.all().delete()
        email_log.status = 'cancelled'
        email_log.completed_at = timezone.now()
        email_log.console_log = f"[{localtime(timezone.now()).strftime('%H:%M:%S')}] Warmup cancelled by user"
        email_log.save(update_fields=['status', 'completed_at', 'console_log'])

    # Also clear any cache keys we know about
    for extra in ['', '_inactive']:
        cache.delete(f'email_warmup_{announcement_id}{extra}')

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
            # Save linked documents
            linked_doc_ids = request.POST.getlist('linked_document_ids')
            if linked_doc_ids:
                valid_docs = CommitteeDocument.objects.filter(
                    id__in=linked_doc_ids, published_to_chapter=True
                )
                announcement.linked_documents.set(valid_docs)
            else:
                announcement.linked_documents.clear()
            messages.success(request, 'Announcement updated successfully!')
            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(instance=announcement)

    chapter_docs = CommitteeDocument.objects.filter(
        published_to_chapter=True
    ).order_by('title')
    linked_doc_ids = list(announcement.linked_documents.values_list('id', flat=True))

    # v3.17.5: edit_announcement.html printed `announcement.poll.questions.count`
    # and `announcement.poll.responses.count` twice each (:218, :223 — once for
    # the number, once for `|pluralize`), which is four COUNT round trips for
    # two numbers. One aggregate over the poll instead.
    poll_totals = {'poll_question_total': 0, 'poll_response_total': 0}
    poll = getattr(announcement, 'poll', None)
    if poll is not None:
        poll_totals = AnnouncementPoll.objects.filter(pk=poll.pk).aggregate(
            poll_question_total=Count('questions', distinct=True),
            poll_response_total=Count('responses', distinct=True),
        )

    return render(request, 'officer/edit_announcement.html', {
        'form': form,
        'announcement': announcement,
        'chapter_docs': chapter_docs,
        'linked_doc_ids': linked_doc_ids,
        **poll_totals,
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
@require_POST
def toggle_announcement_status(request, announcement_id):
    """View to toggle announcement active status"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    announcement.is_active = not announcement.is_active
    announcement.save(update_fields=['is_active'])

    status = "activated" if announcement.is_active else "deactivated"
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'is_active': announcement.is_active, 'status': status})
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
        announcement.ensure_target_audience_snapshot()

        # Record or update the view
        view, created = UserAnnouncementView.objects.get_or_create(
            user=user,
            announcement=announcement,
            defaults={
                'view_source': 'email',
                'counted_in_target': announcement.is_in_target_audience(user),
            },
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
    stats = announcement.get_view_stats()  # also freezes the snapshot, if not already
    viewers = announcement.get_viewers()

    # Get users who haven't viewed. v3.28.6: this used to re-derive the
    # target population live from the current roster — the same bug as
    # get_view_stats() had, and on the same page, so a former pledge who'd
    # since initiated would silently vanish from (or wrongly appear in) this
    # list depending on their current member_type. Now reads the SAME frozen
    # population get_view_stats() just used for the denominator above, so the
    # count on this page and the names in this list can't disagree with each
    # other.
    viewed_user_ids = viewers.values_list('user_id', flat=True)
    non_viewers = (
        ParliamentUser.objects
        .filter(user_id__in=announcement.target_audience_snapshot)
        .exclude(user_id__in=viewed_user_ids)
    )

    context = {
        'announcement': announcement,
        'stats': stats,
        'viewers': viewers,
        'non_viewers': non_viewers,
    }
    return render(request, 'officer/announcement_stats.html', context)
