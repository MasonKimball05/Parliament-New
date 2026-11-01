"""
Resolutions management in the app (v4.x): the website's legacy passed
resolutions pages (src/view/officer/manage_resolutions.py, the officer
portal's "Resolutions (Legacy)"), admins only (``admin_required``), as on the
web. These are ``PassedResolution`` cards and their section-impact tags, not
the Constitution & Bylaws builder's resolutions (``cnb/resolutions/``).

* ``GET officer/resolutions/`` — every resolution (the model's order: display
  order, then newest passed) with its section impacts, and the form's choices
  (border colours, section types, the legislation it can link to).
* ``POST officer/resolutions/`` — create (``create_resolution``). JSON, or
  multipart with a ``document`` file, checked by ``validate_uploaded_file``
  before anything is saved. Either ``legislation`` or a document is required,
  as the form requires.
* ``GET/PATCH/DELETE officer/resolutions/<id>/`` — ``edit_resolution`` /
  ``delete_resolution``. A PATCH starts from the resolution's own values, so
  what it doesn't send stays as it was (and the document is kept unless a new
  one is uploaded). Deleting deletes its section impacts too.
* ``POST officer/resolutions/<id>/sections/`` — ``add_section_impact``.
* ``PATCH/DELETE officer/resolutions/sections/<impact_id>/`` —
  ``edit_section_impact`` / ``delete_section_impact``.

Every save goes through the web views' own ``save_resolution`` /
``save_section_impact`` and their forms.
"""
import os

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.forms import PassedResolutionForm, ResolutionSectionImpactForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer import _form_data, _form_error
from src.models import PassedResolution, ResolutionSectionImpact
from src.view.officer.manage_resolutions import save_resolution, save_section_impact

RESOLUTION_FIELDS = ('title', 'description', 'date_passed', 'legislation', 'border_color',
                     'impact_summary', 'display_order', 'is_active')
IMPACT_FIELDS = ('section_name', 'section_type', 'section_anchor', 'external_url', 'display_order')


class _AdminView(MobileAPIView):
    web_page_toggle = False   # the resolution pages have no page toggle

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Admins only.', code='forbidden')


def _iso(value):
    return value.isoformat() if value else None


def _impact_json(i):
    return {
        'id': i.id,
        'section_name': i.section_name,
        'section_type': i.section_type,
        'section_type_label': i.get_section_type_display(),
        'section_anchor': i.section_anchor,
        'external_url': i.external_url,
        'display_order': i.display_order,
    }


def _resolution_json(r):
    return {
        'id': r.id,
        'title': r.title,
        'description': r.description,
        'date_passed': _iso(r.date_passed),
        'legislation': {'id': r.legislation_id, 'title': r.legislation.title} if r.legislation_id else None,
        'document_name': os.path.basename(r.document.name) if r.document else None,
        'document_url': r.get_document_url(),
        'border_color': r.border_color,
        'impact_summary': r.impact_summary,
        'display_order': r.display_order,
        'is_active': r.is_active,
        'created_by': r.created_by.get_display_name() if r.created_by_id else None,
        'created_at': _iso(r.created_at),
        'updated_at': _iso(r.updated_at),
        'section_impacts': [_impact_json(i) for i in r.section_impacts.all()],
        'web_path': reverse('edit_resolution', args=[r.id]),
    }


def _options():
    legislation = PassedResolutionForm().fields['legislation'].queryset
    return {
        'border_colors': [{'value': v, 'label': label} for v, label in PassedResolution.BORDER_COLOR_CHOICES],
        'section_types': [{'value': v, 'label': label} for v, label in ResolutionSectionImpact.SECTION_TYPE_CHOICES],
        'legislation': [{'id': x.id, 'title': x.title, 'status': x.status}
                        for x in legislation.order_by('-created_at').only('id', 'title', 'status')],
    }


def _body(request):
    """The request's fields as a plain dict (JSON, or a multipart form's values)."""
    data = request.data
    if hasattr(data, 'dict'):
        data = data.dict()
    return data if isinstance(data, dict) else {}


def _apply(data, body, allowed):
    for key in allowed:
        if key in body:
            value = body[key]
            data[key] = '' if value is None else value


def _save_resolution(request, instance=None):
    body = _body(request)
    data = _form_data(PassedResolutionForm, instance) if instance is not None else {}
    data.pop('document', None)
    if instance is None:
        data['is_active'] = True          # the website form's default
        data['border_color'] = 'green'
        data['display_order'] = 0
    _apply(data, body, RESOLUTION_FIELDS)
    files = {'document': request.FILES['document']} if 'document' in request.FILES else {}
    try:
        resolution, form = save_resolution(request.user, data, files, instance=instance)
    except ValidationError as e:
        raise BadRequest(f'File upload error: {"; ".join(e.messages)}', code='invalid_input')
    if resolution is None:
        _form_error(form)
    return resolution


def _resolutions():
    return PassedResolution.objects.select_related('legislation', 'created_by').prefetch_related('section_impacts')


class OfficerResolutionListView(_AdminView):
    web_page = 'manage_resolutions'

    def get(self, request):
        return Response({
            'resolutions': [_resolution_json(r) for r in _resolutions()],
            'options': _options(),
            'web_path': reverse('manage_resolutions'),
        })

    def post(self, request):
        resolution = _save_resolution(request)
        return Response({'resolution': _resolution_json(_resolutions().get(pk=resolution.pk))}, status=201)


class OfficerResolutionView(_AdminView):
    web_page = 'edit_resolution'

    def get(self, request, resolution_id):
        r = get_object_or_404(_resolutions(), id=resolution_id)
        return Response({'resolution': _resolution_json(r), 'options': _options()})

    def patch(self, request, resolution_id):
        r = get_object_or_404(PassedResolution, id=resolution_id)
        _save_resolution(request, instance=r)
        return Response({'resolution': _resolution_json(_resolutions().get(pk=r.pk))})

    def delete(self, request, resolution_id):
        r = get_object_or_404(PassedResolution, id=resolution_id)
        impacts = r.section_impacts.count()
        r.delete()
        return Response({'ok': True, 'section_impacts_deleted': impacts})


class OfficerResolutionImpactCreateView(_AdminView):
    web_page = 'add_section_impact'

    def post(self, request, resolution_id):
        r = get_object_or_404(PassedResolution, id=resolution_id)
        data = {'section_type': 'constitution', 'display_order': 0}
        _apply(data, _body(request), IMPACT_FIELDS)
        impact, form = save_section_impact(r, data)
        if impact is None:
            _form_error(form)
        return Response({'section_impact': _impact_json(impact)}, status=201)


class OfficerResolutionImpactView(_AdminView):
    web_page = 'edit_section_impact'

    def patch(self, request, impact_id):
        impact = get_object_or_404(ResolutionSectionImpact.objects.select_related('resolution'), id=impact_id)
        data = _form_data(ResolutionSectionImpactForm, impact)
        _apply(data, _body(request), IMPACT_FIELDS)
        saved, form = save_section_impact(impact.resolution, data, instance=impact)
        if saved is None:
            _form_error(form)
        return Response({'section_impact': _impact_json(saved)})

    def delete(self, request, impact_id):
        get_object_or_404(ResolutionSectionImpact, id=impact_id).delete()
        return Response({'ok': True})
