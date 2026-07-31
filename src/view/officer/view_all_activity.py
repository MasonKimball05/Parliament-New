from src.decorators import officer_or_advisor_required
from django.db.models import Count
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import Legislation, CommitteeLegislation, CommitteeDocument
from src.models.users import member_defer

#: Most rows fetched per family. See `view_all_reports.DOCUMENT_FETCH_LIMIT` —
#: same page shape, same reasoning, deliberately the same order of magnitude.
ACTIVITY_FETCH_LIMIT = 500

#: The three legislation statuses this page renders as tabs.
_TAB_STATUSES = ('draft', 'passed', 'removed')


def _status_totals(model):
    """
    ``{status: true_row_count}`` for `model`, in one GROUP BY.

    v3.17.7 — WHY THE PAGE DOES NOT COUNT ITS OWN LISTS.
    ---------------------------------------------------
    v3.17.5 capped `view_all_reports` and wrote the rule down: *a capped page
    must not count the capped list.* Its badges come from a separate GROUP BY
    for exactly this reason. This view was capped one commit later and did not
    get the same treatment — its eleven badges were `|length` straight over the
    capped lists, so past 500 rows the page would report exactly 500 forever.

    The second consequence is the expensive one and it is specific to this page:
    the status tabs are partitions of the **newest** 500, so an older draft
    would stop appearing in the Draft tab with no indication that anything was
    hidden. Drafts are the actionable items here.
    """
    return dict(model.objects.values_list('status').annotate(n=Count('pk')))

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

    # True totals, so the tab badges stay honest when the cap bites. Three
    # GROUP BYs for eleven numbers — see `_status_totals` for why the page
    # must not count its own lists.
    chapter_totals = _status_totals(Legislation)
    committee_totals = _status_totals(CommitteeLegislation)
    chapter_total = sum(chapter_totals.values())
    committee_total = sum(committee_totals.values())
    docs_total = CommitteeDocument.objects.count()

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

        # Totals for the badges (v3.17.7)
        'chapter_total': chapter_total,
        'committee_total': committee_total,
        'docs_total': docs_total,
        'draft_chapter_total': chapter_totals.get('draft', 0),
        'passed_chapter_total': chapter_totals.get('passed', 0),
        'removed_chapter_total': chapter_totals.get('removed', 0),
        'draft_committee_total': committee_totals.get('draft', 0),
        'passed_committee_total': committee_totals.get('passed', 0),
        'removed_committee_total': committee_totals.get('removed', 0),

        # Truncation notice — matches `view_all_reports.documents_truncated`
        'activity_truncated': (
            chapter_total > len(all_chapter_legislation)
            or committee_total > len(all_committee_legislation)
            or docs_total > len(all_committee_docs)
        ),
        'activity_fetch_limit': ACTIVITY_FETCH_LIMIT,
    }

    return render(request, 'officer/view_all_activity.html', context)
