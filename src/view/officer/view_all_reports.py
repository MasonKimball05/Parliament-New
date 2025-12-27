from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import CommitteeDocument

@login_required
@officer_or_advisor_required
def view_all_reports(request):
    """View all committee documents for officers, including unpublished ones"""

    # Get all committee documents, including those not published to chapter
    all_documents = CommitteeDocument.objects.select_related(
        'committee', 'uploaded_by'
    ).order_by('-uploaded_at')

    # Group by document type for easier viewing
    reports = all_documents.filter(document_type='report')
    minutes = all_documents.filter(document_type='minutes')
    agendas = all_documents.filter(document_type='agenda')
    policies = all_documents.filter(document_type='policy')
    general_docs = all_documents.filter(document_type='general')

    context = {
        'all_documents': all_documents,
        'reports': reports,
        'minutes': minutes,
        'agendas': agendas,
        'policies': policies,
        'general_docs': general_docs,
    }

    return render(request, 'officer/view_all_reports.html', context)
