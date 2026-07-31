from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import Legislation, CommitteeLegislation, CommitteeDocument
from src.models.users import member_defer

#: Most rows fetched per family. See `view_all_reports.DOCUMENT_FETCH_LIMIT` —
#: same page shape, same reasoning, deliberately the same order of magnitude.
ACTIVITY_FETCH_LIMIT = 500

@login_required
@officer_or_advisor_required
def view_all_activity(request):
    """View all recent member activity for officers"""

    # v3.17.5: this is `view_all_reports`' twin, with the same defect and the
    # same fix. Each family was one base queryset plus three `.filter(status=…)`
    # derivatives, and the template renders ALL FOUR — so each family cost four
    # SELECTs over the same table for the same rows. The widened
    # `test_url_smoke` fixtures caught it as `3× src_legislation` and
    # `3× src_committeelegislation` on one page; it was invisible before because
    # the sweep had no CommitteeLegislation rows to repeat over.
    #
    # Fetch once per family, partition in Python. Capped for the same reason
    # `view_all_reports` is: the tabs are client-side, so every tab's rows have
    # to be in the one response and pagination does not fit — and these tables
    # are append-only.
    all_chapter_legislation = list(
        Legislation.objects.select_related('posted_by')
        .defer(*member_defer('posted_by'))
        .order_by('-created_at')[:ACTIVITY_FETCH_LIMIT]
    )
    all_committee_legislation = list(
        CommitteeLegislation.objects.select_related('committee', 'posted_by')
        .defer(*member_defer('posted_by'))
        .order_by('-created_at')[:ACTIVITY_FETCH_LIMIT]
    )
    all_committee_docs = list(
        CommitteeDocument.objects.select_related('committee', 'uploaded_by')
        .defer(*member_defer('uploaded_by'))
        .order_by('-uploaded_at')[:ACTIVITY_FETCH_LIMIT]
    )

    def _by_status(rows, status):
        return [row for row in rows if row.status == status]

    draft_chapter_leg = _by_status(all_chapter_legislation, 'draft')
    passed_chapter_leg = _by_status(all_chapter_legislation, 'passed')
    removed_chapter_leg = _by_status(all_chapter_legislation, 'removed')

    draft_committee_leg = _by_status(all_committee_legislation, 'draft')
    passed_committee_leg = _by_status(all_committee_legislation, 'passed')
    removed_committee_leg = _by_status(all_committee_legislation, 'removed')

    context = {
        'all_chapter_legislation': all_chapter_legislation,
        'all_committee_legislation': all_committee_legislation,
        'all_committee_docs': all_committee_docs,
        'draft_chapter_leg': draft_chapter_leg,
        'passed_chapter_leg': passed_chapter_leg,
        'removed_chapter_leg': removed_chapter_leg,
        'draft_committee_leg': draft_committee_leg,
        'passed_committee_leg': passed_committee_leg,
        'removed_committee_leg': removed_committee_leg,
    }

    return render(request, 'officer/view_all_activity.html', context)
