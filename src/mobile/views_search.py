"""
The app's Search screen (v4.4.0), the native version of the website's
``global_search`` page (``/search/``).

* ``GET search/?q=`` — every result group the page shows for ``q``, found by
  the page's own ``search_everything`` (src/view/global_search.py), so the two
  agree on what matches and on who sees what: the officer-only Members and
  Election Periods groups, the Kai gate and recusal, chat access, visible
  announcements and events, the committees' documents the member belongs to.

``q`` is stripped; an empty one returns no groups, as the page does (it has
no other minimum length). Groups come in the page's order, each with the
page's heading, and are left out when empty. Every item has a ``target``
(``{url, screen, id}``, the routing a push carries) for the page's own link,
with ``id`` set where the app has a screen for that one object (an
announcement, an event). Documents also carry ``document``, the app's
document-viewer fields. Kai report descriptions are only sent to reviewers
who may read report details, as on the page.

The page is ``@require_feature_flag('global_search')`` with no
``@require_page_enabled``, so the flag and the pledge restriction apply here
but not a page toggle.
"""
import os

from django.urls import reverse
from django.utils.text import Truncator
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.deeplinks import push_data_for_url
from src.mobile.views_excuses_polls import BadRequest
from src.view.global_search import search_everything

#: The app debounces typing; anything longer than this isn't a search.
MAX_QUERY = 200

#: (results key, the page's heading), in the order the page renders them.
GROUPS = [
    ('pages', 'Pages'),
    ('chat_channels', 'Chat Channels'),
    ('committees', 'Committees'),
    ('legislation', 'Active Legislation'),
    ('passed_resolutions', 'Passed Resolutions'),
    ('announcements', 'Announcements'),
    ('events', 'Events'),
    ('chapter_documents', 'Chapter Documents'),
    ('committee_documents', 'Committee Documents'),
    ('slating_periods', 'Election Periods'),
    ('kai_reports', 'Kai Reports'),
    ('users', 'Members'),
]


def _words(text, n):
    """The page's ``truncatewords:n``."""
    return Truncator(text or '').words(n, truncate=' …')


def _iso(value):
    return value.isoformat() if value else None


def _document(doc, file):
    name = os.path.basename(doc.document.name or '') if doc.document else ''
    return {'id': doc.id, 'title': doc.title, 'file_name': name, 'file': file}


def _page(p):
    return {'name': p['name'], 'icon': p['icon'], 'description': p['description'],
            'target': push_data_for_url(reverse(p['url']))}


def _channel(c):
    return {
        'id': c.id,
        'name': c.name,
        'description': _words(c.description, 20),
        'access_type': c.access_type,
        'committee': c.committee.name if c.committee_id else None,
        'target': push_data_for_url(reverse('channel_chat', args=[c.id])),
    }


def _committee(c):
    return {'code': c.code, 'name': c.name, 'member_total': c.member_total, 'chair_total': c.chair_total,
            'target': push_data_for_url(reverse('committee_home', args=[c.code]))}


def _legislation(leg):
    return {'id': leg.id, 'title': leg.title, 'description': _words(leg.description, 20),
            'status': leg.get_status_display(), 'created_at': _iso(leg.created_at),
            'target': push_data_for_url(reverse('legislation_detail', args=[leg.id]))}


def _resolution(r):
    return {'id': r.id, 'title': r.title, 'description': _words(r.description, 20),
            'date_passed': _iso(r.date_passed),
            'target': push_data_for_url(reverse('passed_legislation'))}


def _announcement(a):
    return {'id': a.id, 'title': a.title, 'content': _words(a.content, 25),
            'posted_by': a.posted_by.get_display_name() if a.posted_by_id else '',
            'posted_at': _iso(a.posted_at),
            # The page links to the list; the app opens the announcement itself.
            'target': {**push_data_for_url(reverse('announcements')), 'id': a.id}}


def _event(e):
    return {'id': e.id, 'title': e.title, 'description': _words(e.description, 20),
            'location': e.location or '', 'date_time': _iso(e.date_time),
            # The page links to the calendar; the app opens the event itself.
            'target': {**push_data_for_url(reverse('calendar')), 'id': e.id}}


def _chapter_document(d):
    return {'id': d.id, 'title': d.title, 'description': _words(d.description, 20),
            'uploaded_at': _iso(d.uploaded_at),
            'folder': d.chapter_folder.name if d.chapter_folder_id else None,
            'document': _document(d, f'documents/{d.id}/file/'),
            'target': push_data_for_url(reverse('chapter_documents'))}


def _committee_document(d):
    code = d.committee.code
    return {'id': d.id, 'title': d.title, 'description': _words(d.description, 20),
            'uploaded_at': _iso(d.uploaded_at),
            'committee': {'code': code, 'name': d.committee.name},
            'document': _document(d, f'committees/{code}/documents/{d.id}/file/'),
            'target': push_data_for_url(reverse('committee_documents', args=[code]))}


def _slating_period(p):
    return {'id': p.id, 'name': p.name, 'description': _words(p.description, 20),
            'status': p.get_status_display(), 'created_at': _iso(p.created_at),
            'target': push_data_for_url(reverse('slating_period_setup', kwargs={'period_id': p.id}))}


def _kai_report(r, details):
    return {'id': r.id, 'title': r.title, 'category': r.get_category_display(),
            # The allegation body: only for reviewers cleared to read details.
            'description': _words(r.description, 20) if details else None,
            'submitted_at': _iso(r.submitted_at), 'status': r.get_status_display(),
            'target': push_data_for_url(reverse('manage_kai_report', kwargs={'report_id': r.id}))}


def _member(u):
    return {'user_id': u.user_id, 'name': u.get_display_name(), 'member_type': u.member_type,
            'member_status': u.member_status, 'email': u.email or ''}


_SERIALIZERS = {
    'pages': _page,
    'chat_channels': _channel,
    'committees': _committee,
    'legislation': _legislation,
    'passed_resolutions': _resolution,
    'announcements': _announcement,
    'events': _event,
    'chapter_documents': _chapter_document,
    'committee_documents': _committee_document,
    'slating_periods': _slating_period,
    'users': _member,
}


class SearchView(MobileAPIView):
    web_page = 'global_search'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        if not check_feature_enabled('global_search'):
            raise exceptions.PermissionDenied('Search is off.', code='feature_disabled')
        query = (request.query_params.get('q') or '').strip()
        if len(query) > MAX_QUERY:
            raise BadRequest(f'Search for at most {MAX_QUERY} characters.', code='invalid_input')
        groups = []
        if query:
            results, kai_details = search_everything(query, request.user)
            for key, title in GROUPS:
                items = results.get(key)
                if not items:
                    continue
                if key == 'kai_reports':
                    rows = [_kai_report(r, kai_details) for r in items]
                else:
                    rows = [_SERIALIZERS[key](x) for x in items]
                groups.append({'key': key, 'title': title, 'items': rows})
        return Response({
            'query': query,
            'total_count': sum(len(g['items']) for g in groups),
            'groups': groups,
            'web': {'page': reverse('global_search')},
        })
