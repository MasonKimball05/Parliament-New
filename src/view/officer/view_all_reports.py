from src.decorators import officer_or_advisor_required
from django.db.models import Count
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import CommitteeDocument
from src.models.users import member_defer

#: Most documents rendered on one page.
#:
#: v3.17.5 — this page cannot be paginated in the ordinary way: the six tabs are
#: client-side (`document_list.html` is included six times and JS switches
#: between them), so every tab's rows must be in the response. A cap is the
#: shape that fits. 500 is comfortably above a chapter's realistic archive while
#: still being a ceiling, which is the point — `CommitteeDocument` is
#: append-only and grows for the life of the chapter.
DOCUMENT_FETCH_LIMIT = 500


@login_required
@officer_or_advisor_required
def view_all_reports(request):
    """View all committee documents for officers, including unpublished ones"""

    # Get all committee documents, including those not published to chapter
    # v3.17.4: this fetched the same table six times — once for `all_documents`
    # and once per `document_type` filter, each a separate SELECT when the
    # template iterated it. Fetched once and partitioned in Python.
    #
    # v3.17.5: ...but that rewrite also turned a lazy queryset into `list()`
    # with no ceiling, so the whole table was materialized on every load. The
    # v3.17.4 comment said "the page renders every document anyway, so there is
    # nothing to save by filtering in SQL" — true of the *filtering*, not of the
    # *fetch*. Bounded now.
    all_documents = list(
        CommitteeDocument.objects.select_related('committee', 'uploaded_by')
        .defer(*member_defer('uploaded_by'))
        .order_by('-uploaded_at')[:DOCUMENT_FETCH_LIMIT]
    )

    # True per-type totals in one GROUP BY, so the tab badges stay honest when
    # the cap bites. Counting the fetched list instead would silently under-
    # report — a capped page that lies about how much it is hiding is worse
    # than an uncapped one.
    type_totals = dict(
        CommitteeDocument.objects.values_list('document_type')
        .annotate(n=Count('pk'))
    )
    total_documents = sum(type_totals.values())

    # Group by document type for easier viewing
    _by_type = {}
    for doc in all_documents:
        _by_type.setdefault(doc.document_type, []).append(doc)
    reports = _by_type.get('report', [])
    minutes = _by_type.get('minutes', [])
    agendas = _by_type.get('agenda', [])
    policies = _by_type.get('policy', [])
    general_docs = _by_type.get('general', [])

    context = {
        'all_documents': all_documents,
        'reports': reports,
        'minutes': minutes,
        'agendas': agendas,
        'policies': policies,
        'general_docs': general_docs,
        'type_totals': type_totals,
        'total_documents': total_documents,
        'documents_truncated': total_documents > len(all_documents),
        'document_fetch_limit': DOCUMENT_FETCH_LIMIT,
    }

    return render(request, 'officer/view_all_reports.html', context)
