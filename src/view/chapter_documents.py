from django.shortcuts import render
from src.models import CommitteeDocument, Committee
from django.contrib.auth.decorators import login_required
from collections import defaultdict

@login_required
def chapter_documents(request):
    """View for displaying all documents published to the chapter, organized by committee"""
    documents = CommitteeDocument.objects.filter(published_to_chapter=True).select_related('committee', 'uploaded_by')

    # Organize documents by committee
    documents_by_committee = defaultdict(list)
    for doc in documents:
        documents_by_committee[doc.committee].append(doc)

    # Sort committees by name
    sorted_committees = sorted(documents_by_committee.items(), key=lambda x: x[0].name)

    return render(request, 'chapter_documents.html', {
        'documents_by_committee': sorted_committees,
        'total_documents': documents.count()
    })
