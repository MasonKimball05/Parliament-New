import logging
import re
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from src.utils.security_utils import get_client_ip
from ..models import (
    LandingPageContent, LandingPagePhoto, ParliamentUser,
    ContactSubmission, LandingPageContactTopic, LandingPageFormLink,
    LandingPageSocialLink,
)

logger = logging.getLogger('function_calls')

_SIZE_CLASSES = {
    'small':  'landing-photo-small',
    'medium': 'landing-photo-medium',
    'large':  'landing-photo-large',
    'full':   'landing-photo-full',
}

_ALIGN_CLASSES = {
    'left':   'landing-photo-left',
    'center': 'landing-photo-center',
    'right':  'landing-photo-right',
}


def _replace_photo_tags(html, photos_by_id):
    def replacer(match):
        try:
            photo = photos_by_id.get(int(match.group(1)))
        except (ValueError, TypeError):
            return ''
        if not photo:
            return ''
        size  = (match.group(2) or 'medium').strip().lower()
        align = (match.group(3) or 'center').strip().lower()
        size_class  = _SIZE_CLASSES.get(size, 'landing-photo-medium')
        align_class = _ALIGN_CLASSES.get(align, 'landing-photo-center')
        caption_html = f'<figcaption class="landing-photo-caption">{photo.caption}</figcaption>' if photo.caption else ''
        return (
            f'<figure class="landing-photo-inline {size_class} {align_class}">'
            f'<img src="{photo.image.url}" alt="{photo.caption or ""}">'
            f'{caption_html}'
            f'</figure>'
        )
    return re.sub(r'\[photo:(\d+)(?::(\w+))?(?::(\w+))?\]', replacer, html) if html else html


def _resolve_email_for_role(role_code, fallback_president, fallback_vpr):
    if role_code:
        user = ParliamentUser.objects.filter(
            roles__code=role_code, is_active=True
        ).first()
        if user and user.email:
            return user.email
    if fallback_president and fallback_president.email:
        return fallback_president.email
    if fallback_vpr and fallback_vpr.email:
        return fallback_vpr.email
    return ''


def landing_page(request):
    """Public landing page — redirects authenticated users straight to the dashboard."""
    is_preview = request.GET.get('preview') == '1'
    if request.user.is_authenticated and not is_preview:
        return redirect('home')

    content = LandingPageContent.get_instance()
    photos = LandingPagePhoto.objects.all()
    photos_by_id = {p.pk: p for p in photos}

    content.who_we_are_html = _replace_photo_tags(content.who_we_are_html, photos_by_id)
    content.what_we_believe_html = _replace_photo_tags(content.what_we_believe_html, photos_by_id)
    content.chapter_history_html = _replace_photo_tags(content.chapter_history_html, photos_by_id)

    president = ParliamentUser.objects.filter(roles__code='President', is_active=True).first()
    vpr       = ParliamentUser.objects.filter(roles__code='VPR',       is_active=True).first()

    raw_topics = LandingPageContactTopic.objects.filter(is_active=True)
    contact_topics = [
        {
            'label': t.label,
            'email': _resolve_email_for_role(t.role_code, president, vpr),
        }
        for t in raw_topics
    ]

    form_links   = LandingPageFormLink.objects.filter(is_active=True)
    social_links = LandingPageSocialLink.objects.all()

    # Recruitment banner — auto-hide after end date
    today = timezone.now().date()
    show_banner = (
        content.recruitment_banner_active
        and bool(content.recruitment_banner_message)
        and (not content.recruitment_banner_end or content.recruitment_banner_end >= today)
    )

    return render(request, 'landing.html', {
        'content': content,
        'photos': photos,
        'president': president,
        'vpr': vpr,
        'contact_topics': contact_topics,
        'form_links': form_links,
        'social_links': social_links,
        'show_banner': show_banner,
    })


@csrf_exempt
@require_POST
def contact_submit(request):
    """Save a contact form submission and notify the recipient."""
    # Rate limit: 5 submissions per IP per 10 minutes
    ip = get_client_ip(request)
    rate_key = f'contact_submit_{ip}'
    submission_count = cache.get(rate_key, 0)
    if submission_count >= 5:
        return JsonResponse({'ok': False, 'error': 'Too many submissions. Please try again later.'}, status=429)
    cache.set(rate_key, submission_count + 1, 600)

    name      = request.POST.get('name', '').strip().replace('\r', '').replace('\n', '')
    email     = request.POST.get('email', '').strip().replace('\r', '').replace('\n', '')
    message   = request.POST.get('message', '').strip()
    topic     = request.POST.get('topic', '').strip().replace('\r', '').replace('\n', '')
    recipient = request.POST.get('recipient_email', '').strip()

    if not name or not email or not message:
        return JsonResponse({'ok': False, 'error': 'Missing required fields.'}, status=400)

    # Basic email format check (no external library needed)
    if '@' not in email or '.' not in email.split('@')[-1]:
        return JsonResponse({'ok': False, 'error': 'Invalid email address.'}, status=400)

    # Reject if recipient is not a plausible email (prevents misuse if manipulated client-side)
    if recipient and ('@' not in recipient or '\n' in recipient or '\r' in recipient):
        recipient = ''

    ContactSubmission.objects.create(
        name=name,
        email=email,
        message=message,
        topic=topic,
        recipient_email=recipient,
    )

    # Email the recipient officer
    if recipient:
        topic_line = f' ({topic})' if topic else ''
        try:
            send_mail(
                subject=f'[Parliament] New contact message from {name}{topic_line}',
                message=(
                    f'Someone submitted a message via the chapter landing page.\n\n'
                    f'Name:    {name}\n'
                    f'Email:   {email}\n'
                    f'Topic:   {topic or "General"}\n\n'
                    f'Message:\n{message}\n\n'
                    f'---\n'
                    f'You can reply directly to {email} or view all messages at /officers/contact-messages/'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=True,
            )
        except Exception as e:
            logger.error(f'contact_submit: failed to send notification email: {e}')

    return JsonResponse({'ok': True})
