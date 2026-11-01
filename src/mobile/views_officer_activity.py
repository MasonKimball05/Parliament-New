"""
All Activity in the app (v4.x): the website's ``view_all_activity`` page
(/officers/all-activity/), officers, chairs and advisors
(``officer_or_advisor_required``), as on the web.

* ``GET officer/activity/`` — chapter legislation, committee legislation and
  committee documents, the newest ``ACTIVITY_FETCH_LIMIT`` of each, built by the
  page's own ``all_activity_context`` so the rows, the status tabs' true totals
  and the "showing the N most recent" notice are the page's. The app splits
  the legislation lists into Draft / Passed / Removed on the phone, as the
  page's tabs do in the browser.

Read only. A chapter bill's ``id`` opens the app's own result screen
(``legislation/<id>``); a committee document opens through
``officer/reports/<id>/file/`` (the same officers-and-advisors gate).
"""
import os

from django.urls import reverse
from django.utils.text import Truncator
from rest_framework.response import Response

from src.mobile.views_officer_tools import _OfficersAndAdvisors
from src.view.officer.view_all_activity import all_activity_context


def _iso(value):
    return value.isoformat() if value else None


def _legislation_json(leg, committee):
    out = {
        'id': leg.id,
        'title': leg.title,
        'description': Truncator(leg.description or '').words(30),
        'status': leg.status,
        'voting_closed': bool(leg.voting_closed),
        'vote_mode': leg.vote_mode,
        'required_percentage': leg.required_percentage if leg.vote_mode == 'percentage' else None,
        'required_number': leg.required_number if leg.vote_mode != 'percentage' else None,
        'posted_by': leg.posted_by.get_display_name() if leg.posted_by_id else '',
        'created_at': _iso(leg.created_at),
        'available_at': _iso(leg.available_at),
        'has_document': bool(leg.document),
    }
    if committee:
        out['committee'] = leg.committee.code if leg.committee_id else None
        out['web_path'] = None
    else:
        out['web_path'] = reverse('legislation_detail', args=[leg.id])
    return out


def _document_json(doc):
    return {
        'id': doc.id,
        'title': doc.title,
        'description': Truncator(doc.description or '').words(30),
        'type': doc.document_type,
        'type_label': doc.get_document_type_display(),
        'committee': doc.committee.code if doc.committee_id else None,
        'meeting_date': _iso(doc.meeting_date),
        'published': doc.published_to_chapter,
        'uploaded_by': doc.uploaded_by.get_display_name() if doc.uploaded_by_id else '',
        'uploaded_at': _iso(doc.uploaded_at),
        'file_name': os.path.basename(doc.document.name) if doc.document else '',
        'extension': os.path.splitext(doc.document.name)[1].lstrip('.').lower() if doc.document else '',
        'file': f'officer/reports/{doc.id}/file/' if doc.document else None,
    }


class OfficerActivityView(_OfficersAndAdvisors):
    web_page = 'view_all_activity'

    def get(self, request):
        c = all_activity_context()
        return Response({
            'chapter_legislation': [_legislation_json(x, False) for x in c['all_chapter_legislation']],
            'committee_legislation': [_legislation_json(x, True) for x in c['all_committee_legislation']],
            'committee_documents': [_document_json(d) for d in c['all_committee_docs']],
            'totals': {
                'chapter': {'all': c['chapter_total'], 'draft': c['draft_chapter_total'],
                            'passed': c['passed_chapter_total'], 'removed': c['removed_chapter_total']},
                'committee': {'all': c['committee_total'], 'draft': c['draft_committee_total'],
                              'passed': c['passed_committee_total'], 'removed': c['removed_committee_total']},
                'documents': c['docs_total'],
            },
            'truncated': c['activity_truncated'],
            'limit': c['activity_fetch_limit'],
            'web_path': reverse('view_all_activity'),
        })
