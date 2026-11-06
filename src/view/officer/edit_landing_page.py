import json
import logging
import bleach
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from src.decorators import officer_or_advisor_required
from src.feature_flag_decorators import require_page_enabled
from src.models import (
    LandingPageContent, LandingPagePhoto,
    LandingPageContactTopic, LandingPageFormLink, LandingPageSocialLink,
)

# Tags and attributes produced by Quill Snow editor that are safe to store.
_ALLOWED_TAGS = [
    'p', 'br', 'b', 'i', 'em', 'strong', 'u', 's', 'strike',
    'a', 'blockquote', 'ol', 'ul', 'li',
    'h1', 'h2', 'h3', 'h4',
    'img', 'hr', 'span', 'div',
    'figure', 'figcaption',
]
_ALLOWED_ATTRS = {
    'a':          ['href', 'target', 'rel'],
    'img':        ['src', 'alt', 'class', 'width', 'height'],
    'span':       ['class'],
    'div':        ['class'],
    'p':          ['class'],
    'figure':     ['class'],
    'figcaption': ['class'],
}


def _sanitize_html(html):
    """Strip any tags/attributes not in the allowlist before saving to DB."""
    if not html:
        return html
    return bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)

logger = logging.getLogger('function_calls')


# ── The page's actions, shared with the app ──────────────────────────────────
#
# Each POST ``action`` of the editor is one function below, called by the web
# view and by src/mobile/views_landing_page.py. ``data`` is the page's form
# fields under their HTML names (a QueryDict, or a dict the app view builds
# the same way: a checkbox is present when ticked). Each returns the success
# banner, or None where the page shows none, and raises ``LandingEditRefused``
# with the page's error banner.

class LandingEditRefused(Exception):
    """An action the editor refuses, with its message. ``not_found`` for a missing row."""

    def __init__(self, message, not_found=False):
        super().__init__(message)
        self.not_found = not_found


def _order(data, key):
    try:
        return int(data.get(key, 0))
    except (ValueError, TypeError):
        return 0


#: 11-06-26: the page's ``accept="image/*"``, enforced on the server too.
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def check_landing_image(upload):
    """
    Refuse anything that isn't really a photo (11-06-26). The website only had
    the browser's ``accept="image/*"``, so any file could be uploaded as a
    landing photo or link-preview image, which the public page then serves.
    """
    if not upload:
        return
    if not upload.name.lower().endswith(IMAGE_EXTENSIONS):
        raise LandingEditRefused('Choose a photo (JPEG, PNG, GIF or WebP).')
    from django.core.exceptions import ValidationError
    from src.utils.file_validation import validate_uploaded_file
    try:
        validate_uploaded_file(upload)
    except ValidationError as e:
        raise LandingEditRefused(' '.join(e.messages))


def save_landing_content(user, data, og_image=None):
    """``save_content``: the text, contact, SEO, visibility and banner fields, and the optional OG image."""
    check_landing_image(og_image)
    content = LandingPageContent.get_instance()
    content.tagline = data.get('tagline', content.tagline).strip()
    content.who_we_are_html = _sanitize_html(data.get('who_we_are_html', '').strip())
    content.what_we_believe_html = _sanitize_html(data.get('what_we_believe_html', '').strip())
    content.chapter_history_html = _sanitize_html(data.get('chapter_history_html', '').strip())
    content.chapter_history_title = data.get('chapter_history_title', content.chapter_history_title).strip()

    # SEO
    content.meta_description = data.get('meta_description', '').strip()

    # Contact info
    content.contact_location = data.get('contact_location', '').strip()
    content.contact_address  = data.get('contact_address',  '').strip()
    content.contact_phone    = data.get('contact_phone',    '').strip()

    # Section visibility
    content.show_parliament_info = 'show_parliament_info' in data
    content.show_contact_section = 'show_contact_section' in data

    # Recruitment banner
    content.recruitment_banner_active  = 'recruitment_banner_active' in data
    content.recruitment_banner_message = data.get('recruitment_banner_message', '').strip()
    end_date_str = data.get('recruitment_banner_end', '').strip()
    if end_date_str:
        from datetime import date
        try:
            content.recruitment_banner_end = date.fromisoformat(end_date_str)
        except ValueError:
            content.recruitment_banner_end = None
    else:
        content.recruitment_banner_end = None

    # OG image (file upload, optional)
    if og_image:
        content.og_image = og_image

    content.updated_by = user
    content.save()
    logger.info(f"{user.username} updated landing page content")
    return 'Landing page content saved.'


def upload_landing_photo(user, data, image):
    """``upload_photo``: a photo for the library (caption and order optional)."""
    if not image:
        raise LandingEditRefused('No image file provided.')
    check_landing_image(image)
    LandingPagePhoto.objects.create(
        image=image,
        caption=data.get('caption', '').strip(),
        display_order=_order(data, 'display_order'),
        uploaded_by=user,
    )
    logger.info(f"{user.username} uploaded landing page photo")
    return 'Photo uploaded.'


def delete_landing_photo(user, photo_id):
    try:
        photo = LandingPagePhoto.objects.get(pk=photo_id)
    except (LandingPagePhoto.DoesNotExist, ValueError, TypeError):
        raise LandingEditRefused('Photo not found.', not_found=True)
    photo.image.delete(save=False)
    photo.delete()
    logger.info(f"{user.username} deleted landing page photo {photo_id}")
    return 'Photo deleted.'


def reorder_landing_photo(user, photo_id, data):
    try:
        new_order = int(data.get('display_order', 0))
        LandingPagePhoto.objects.filter(pk=photo_id).update(display_order=new_order)
    except (ValueError, TypeError, LandingPagePhoto.DoesNotExist):
        pass
    return None


def add_social_link(user, data):
    label = data.get('social_label', '').strip()
    url   = data.get('social_url', '').strip()
    if not (label and url):
        raise LandingEditRefused('Label and URL are required.')
    LandingPageSocialLink.objects.create(label=label, url=url, display_order=_order(data, 'social_order'))
    logger.info(f"{user.username} added social link '{label}'")
    return f'Link "{label}" added.'


def delete_social_link(user, link_id):
    try:
        link = LandingPageSocialLink.objects.get(pk=link_id)
    except (LandingPageSocialLink.DoesNotExist, ValueError, TypeError):
        raise LandingEditRefused('Link not found.', not_found=True)
    logger.info(f"{user.username} deleted social link '{link.label}'")
    link.delete()
    return 'Link deleted.'


def add_contact_topic(user, data):
    label = data.get('topic_label', '').strip()
    role_code = data.get('topic_role_code', '').strip()
    if not label:
        raise LandingEditRefused('Topic label is required.')
    LandingPageContactTopic.objects.create(label=label, role_code=role_code, display_order=_order(data, 'topic_order'))
    logger.info(f"{user.username} added contact topic '{label}'")
    return f'Contact topic "{label}" added.'


def delete_contact_topic(user, topic_id):
    try:
        topic = LandingPageContactTopic.objects.get(pk=topic_id)
    except (LandingPageContactTopic.DoesNotExist, ValueError, TypeError):
        raise LandingEditRefused('Topic not found.', not_found=True)
    logger.info(f"{user.username} deleted contact topic '{topic.label}'")
    topic.delete()
    return 'Contact topic deleted.'


def toggle_contact_topic(user, topic_id):
    try:
        topic = LandingPageContactTopic.objects.get(pk=topic_id)
    except (LandingPageContactTopic.DoesNotExist, ValueError, TypeError):
        return None
    topic.is_active = not topic.is_active
    topic.save(update_fields=['is_active'])
    return None


def add_form_link(user, data):
    title = data.get('form_title', '').strip()
    url = data.get('form_url', '').strip()
    if not (title and url):
        raise LandingEditRefused('Title and URL are required.')
    LandingPageFormLink.objects.create(
        title=title,
        description=data.get('form_description', '').strip(),
        url=url,
        button_text=data.get('form_button_text', 'Apply Now').strip() or 'Apply Now',
        display_order=_order(data, 'form_order'),
        created_by=user,
    )
    logger.info(f"{user.username} added form link '{title}'")
    return f'Form link "{title}" added.'


def delete_form_link(user, link_id):
    try:
        link = LandingPageFormLink.objects.get(pk=link_id)
    except (LandingPageFormLink.DoesNotExist, ValueError, TypeError):
        raise LandingEditRefused('Form link not found.', not_found=True)
    logger.info(f"{user.username} deleted form link '{link.title}'")
    link.delete()
    return 'Form link deleted.'


def toggle_form_link(user, link_id):
    try:
        link = LandingPageFormLink.objects.get(pk=link_id)
    except (LandingPageFormLink.DoesNotExist, ValueError, TypeError):
        return None
    link.is_active = not link.is_active
    link.save(update_fields=['is_active'])
    return None


def run_landing_action(user, action, data, files):
    """
    One editor action by its form name. Returns ``(known, message)``: ``known``
    False for an action the page doesn't have (the page then just re-renders).
    """
    if action == 'save_content':
        return True, save_landing_content(user, data, files.get('og_image'))
    if action == 'upload_photo':
        return True, upload_landing_photo(user, data, files.get('image'))
    if action == 'delete_photo':
        return True, delete_landing_photo(user, data.get('photo_id'))
    if action == 'reorder_photo':
        return True, reorder_landing_photo(user, data.get('photo_id'), data)
    if action == 'add_social_link':
        return True, add_social_link(user, data)
    if action == 'delete_social_link':
        return True, delete_social_link(user, data.get('link_id'))
    if action == 'add_contact_topic':
        return True, add_contact_topic(user, data)
    if action == 'delete_contact_topic':
        return True, delete_contact_topic(user, data.get('topic_id'))
    if action == 'toggle_contact_topic':
        return True, toggle_contact_topic(user, data.get('topic_id'))
    if action == 'add_form_link':
        return True, add_form_link(user, data)
    if action == 'delete_form_link':
        return True, delete_form_link(user, data.get('link_id'))
    if action == 'toggle_form_link':
        return True, toggle_form_link(user, data.get('link_id'))
    return False, None


@login_required
@officer_or_advisor_required
@require_page_enabled('officer_home')
@require_http_methods(["GET", "POST"])
def edit_landing_page(request):
    """Officer editor for the public landing page content."""
    if request.method == 'POST':
        action = request.POST.get('action', 'save_content')
        try:
            known, message = run_landing_action(request.user, action, request.POST, request.FILES)
        except LandingEditRefused as refused:
            messages.error(request, str(refused))
            return redirect('edit_landing_page')
        if known:
            if message:
                messages.success(request, message)
            return redirect('edit_landing_page')

    return render(request, 'officer/edit_landing_page.html', {
        'content': LandingPageContent.get_instance(),
        'photos': LandingPagePhoto.objects.all(),
        'contact_topics': LandingPageContactTopic.objects.all(),
        'form_links': LandingPageFormLink.objects.all(),
        'social_links': LandingPageSocialLink.objects.all(),
    })
