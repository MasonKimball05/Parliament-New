"""
The officer portal's Edit Landing Page in the app: the public front page's
text, contact details, banner, visibility switches, link-preview image, photo
library, social links, contact-form topics and form links.

* ``GET officer/landing-page/`` — everything the editor shows, plus
  ``preview_path`` (the page's own "Preview" link, ``/?preview=1``).
* ``POST officer/landing-page/`` — one of the editor's actions, under the web
  form's own ``action`` and field names (JSON, or multipart when a file comes
  with it: ``og_image`` for ``save_content``, ``image`` for
  ``upload_photo``). Answers the same body as the GET plus ``message``.
* ``GET officer/landing-page/og-image/`` and
  ``GET officer/landing-page/photos/<id>/image/`` — the images, for the app
  (``/media/`` wants a browser session).

Every action runs the web view's own function (src/view/officer/edit_landing_page.py),
so the HTML allowlist, the messages and the logging are the website's. Who:
``officer_or_advisor_required`` and the ``officer_home`` page toggle, as the
web view. What the app adds, because the page's browser does it there: each
field's ``maxlength`` (the model's lengths, which the browser enforces on the
page and the database would otherwise refuse with an error), the
``type="url"`` check on link URLs, and on images ``validate_uploaded_file``
plus an image extension (the page's ``accept="image/*"``). ``save_content``
keeps a field the app leaves out as it is, rather than blanking it.
"""
import hashlib
import mimetypes
import os

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import FileResponse
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.mobile.views_officer_tools import BadRequest, _OfficersAndAdvisors
from src.models import (
    LandingPageContactTopic, LandingPageContent, LandingPageFormLink, LandingPagePhoto, LandingPageSocialLink,
)
from src.utils.content_disposition import apply_disposition
from src.utils.file_validation import validate_uploaded_file
from src.view.officer.edit_landing_page import LandingEditRefused, run_landing_action

#: ``save_content``'s text fields, in the page's order.
CONTENT_TEXT = ['tagline', 'recruitment_banner_message', 'who_we_are_html', 'what_we_believe_html',
                'chapter_history_title', 'chapter_history_html', 'contact_location', 'contact_address',
                'contact_phone', 'meta_description']
#: Its checkboxes (present = ticked).
CONTENT_FLAGS = ['recruitment_banner_active', 'show_parliament_info', 'show_contact_section']

#: The other actions' fields, each with the model field that holds it (for its length).
ACTION_FIELDS = {
    'upload_photo': {'caption': (LandingPagePhoto, 'caption'), 'display_order': None},
    'delete_photo': {'photo_id': None},
    'reorder_photo': {'photo_id': None, 'display_order': None},
    'add_social_link': {'social_label': (LandingPageSocialLink, 'label'), 'social_url': (LandingPageSocialLink, 'url'),
                        'social_order': None},
    'delete_social_link': {'link_id': None},
    'add_contact_topic': {'topic_label': (LandingPageContactTopic, 'label'),
                          'topic_role_code': (LandingPageContactTopic, 'role_code'), 'topic_order': None},
    'delete_contact_topic': {'topic_id': None},
    'toggle_contact_topic': {'topic_id': None},
    'add_form_link': {'form_title': (LandingPageFormLink, 'title'), 'form_url': (LandingPageFormLink, 'url'),
                      'form_description': None, 'form_button_text': (LandingPageFormLink, 'button_text'),
                      'form_order': None},
    'delete_form_link': {'link_id': None},
    'toggle_form_link': {'link_id': None},
}
URL_FIELDS = {'social_url': 'URL', 'form_url': 'URL'}
LABELS = {
    'tagline': 'Hero tagline', 'recruitment_banner_message': 'Banner message',
    'chapter_history_title': 'Section title', 'contact_location': 'Location', 'contact_address': 'Street address',
    'contact_phone': 'Phone', 'meta_description': 'Meta description', 'caption': 'Caption',
    'social_label': 'Label', 'social_url': 'URL', 'topic_label': 'Label', 'topic_role_code': 'Role code',
    'form_title': 'Title', 'form_url': 'URL', 'form_button_text': 'Button text',
}
#: The page's ``accept="image/*"``, as the extensions validate_uploaded_file knows to be images.
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def _version(name):
    return hashlib.sha256(name.encode()).hexdigest()[:12]


def _str(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else ''
    return str(value)


def _truthy(value):
    return _str(value).strip().lower() in ('true', '1', 'on', 'yes')


def payload():
    content = LandingPageContent.get_instance()
    return {
        'content': {
            **{name: getattr(content, name) or '' for name in CONTENT_TEXT},
            **{name: bool(getattr(content, name)) for name in CONTENT_FLAGS},
            'recruitment_banner_end': (content.recruitment_banner_end.isoformat()
                                       if content.recruitment_banner_end else None),
            'og_image': (f'officer/landing-page/og-image/?v={_version(content.og_image.name)}'
                         if content.og_image else None),
            'updated_at': content.updated_at.isoformat() if getattr(content, 'updated_at', None) else None,
        },
        'limits': {name: LandingPageContent._meta.get_field(name).max_length
                   for name in CONTENT_TEXT if LandingPageContent._meta.get_field(name).max_length},
        'photos': [{
            'id': p.id,
            'caption': p.caption,
            'display_order': p.display_order,
            'image': f'officer/landing-page/photos/{p.id}/image/?v={_version(p.image.name)}' if p.image else None,
        } for p in LandingPagePhoto.objects.all()],
        'social_links': [{'id': link.id, 'label': link.label, 'url': link.url, 'display_order': link.display_order}
                         for link in LandingPageSocialLink.objects.all()],
        'contact_topics': [{'id': t.id, 'label': t.label, 'role_code': t.role_code,
                            'display_order': t.display_order, 'is_active': t.is_active}
                           for t in LandingPageContactTopic.objects.all()],
        'form_links': [{'id': f.id, 'title': f.title, 'description': f.description, 'url': f.url,
                        'button_text': f.button_text, 'display_order': f.display_order, 'is_active': f.is_active}
                       for f in LandingPageFormLink.objects.all()],
        'preview_path': f"{reverse('landing')}?preview=1",
    }


def _check_length(name, value, model_field):
    if model_field is None:
        return
    model, field = model_field
    limit = model._meta.get_field(field).max_length
    if limit and len(value) > limit:
        raise BadRequest(f'{LABELS.get(name, name)} must be {limit} characters or fewer.', code='invalid_input')


def _check_image(upload):
    if upload is None:
        return
    if not upload.name.lower().endswith(IMAGE_EXTENSIONS):
        raise BadRequest('Choose a photo (JPEG, PNG, GIF or WebP).', code='invalid_input')
    try:
        validate_uploaded_file(upload)
    except ValidationError as e:
        raise BadRequest(' '.join(e.messages), code='invalid_input')


def _content_data(raw):
    """``save_content``'s fields as the page posts them; a field the app leaves out keeps its value."""
    content = LandingPageContent.get_instance()
    data = {}
    for name in CONTENT_TEXT:
        value = _str(raw[name]) if name in raw else (getattr(content, name) or '')
        if name in raw:
            _check_length(name, value.strip(), (LandingPageContent, name))
        data[name] = value
    for name in CONTENT_FLAGS:
        on = _truthy(raw[name]) if name in raw else bool(getattr(content, name))
        if on:
            data[name] = 'on'
    if 'recruitment_banner_end' in raw:
        data['recruitment_banner_end'] = _str(raw['recruitment_banner_end'])
    elif content.recruitment_banner_end:
        data['recruitment_banner_end'] = content.recruitment_banner_end.isoformat()
    return data


def _action_data(action, raw):
    data = {}
    for name, model_field in ACTION_FIELDS[action].items():
        if name not in raw:
            continue
        value = _str(raw[name])
        _check_length(name, value.strip(), model_field)
        if name in URL_FIELDS and value.strip():
            try:
                URLValidator()(value.strip())
            except ValidationError:
                raise BadRequest('Enter a full web address, starting with https://.', code='invalid_input')
        data[name] = value
    return data


class _LandingEditor(_OfficersAndAdvisors):
    web_page = 'officer_home'      # edit_landing_page's @require_page_enabled('officer_home')
    web_page_toggle = True


class LandingPageView(_LandingEditor):
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request):
        return Response(payload())

    def post(self, request):
        raw = request.data
        action = _str(raw.get('action')).strip() or 'save_content'
        files = {}
        if action == 'save_content':
            data = _content_data(raw)
            og_image = request.FILES.get('og_image')
            _check_image(og_image)
            if og_image is not None:
                files['og_image'] = og_image
        elif action in ACTION_FIELDS:
            data = _action_data(action, raw)
            if action == 'upload_photo':
                image = request.FILES.get('image')
                _check_image(image)
                if image is not None:
                    files['image'] = image
        else:
            raise BadRequest('Unknown action.', code='invalid_input')
        try:
            _known, message = run_landing_action(request.user, action, data, files)
        except LandingEditRefused as refused:
            if refused.not_found:
                raise exceptions.NotFound(str(refused))
            raise BadRequest(str(refused), code='invalid_input')
        return Response({**payload(), 'message': message})


def _image_response(field):
    if not field:
        raise exceptions.NotFound()
    try:
        handle = field.open('rb')
    except (FileNotFoundError, OSError):
        raise exceptions.NotFound()
    name = os.path.basename(field.name)
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    response = FileResponse(handle, content_type=content_type)
    apply_disposition(response, content_type, name)
    response['Cache-Control'] = 'private, max-age=86400'
    return response


class LandingOgImageView(_LandingEditor):

    def get(self, request):
        return _image_response(LandingPageContent.get_instance().og_image)


class LandingPhotoImageView(_LandingEditor):

    def get(self, request, photo_id):
        photo = LandingPagePhoto.objects.filter(pk=photo_id).first()
        if photo is None:
            raise exceptions.NotFound()
        return _image_response(photo.image)
