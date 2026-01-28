from django.shortcuts import render
from src.models import CommitteeDocument, Committee, ChapterFolder, CommitteeLegislation, CommitteeVote
from django.contrib.auth.decorators import login_required
from collections import defaultdict
from src.feature_flag_decorators import require_page_enabled

@login_required
@require_page_enabled('chapter_documents')
def chapter_documents(request):
    """View for displaying all documents published to the chapter, organized by folder and committee"""
    all_documents = CommitteeDocument.objects.filter(published_to_chapter=True).select_related('committee', 'uploaded_by', 'chapter_folder')

    # Filter documents based on visibility permissions
    documents = [doc for doc in all_documents if doc.can_user_view(request.user)]

    # Get all folders
    all_folders = ChapterFolder.objects.all()

    # Organize documents by folder - include ALL folders even if empty
    folders_with_documents = []
    docs_by_committee = defaultdict(list)  # For documents without a folder

    # First, create dict of documents by folder
    docs_by_folder_id = defaultdict(list)
    for doc in documents:
        if doc.chapter_folder:
            docs_by_folder_id[doc.chapter_folder.id].append(doc)
        else:
            # Group by committee if no folder is set
            # chapter-level docs (committee=None) go under key 'chapter'
            key = doc.committee.id if doc.committee else 'chapter'
            docs_by_committee[key].append(doc)

    # Now pair each folder with its documents (empty list if no documents)
    for folder in all_folders.order_by('name'):
        folder_documents = docs_by_folder_id.get(folder.id, [])
        folders_with_documents.append((folder, folder_documents))

    # Build committee document groups for documents without folders
    committee_doc_groups = []
    # Separate chapter-level docs from committee docs
    chapter_level_docs = docs_by_committee.pop('chapter', [])
    if chapter_level_docs:
        committee_doc_groups.append({
            'committee': None,
            'committee_name': 'Chapter Documents',
            'documents': chapter_level_docs,
        })
    committee_ids_with_docs = set(docs_by_committee.keys())
    if committee_ids_with_docs:
        committees_for_docs = Committee.objects.filter(id__in=committee_ids_with_docs).order_by('name')
        for committee in committees_for_docs:
            committee_doc_groups.append({
                'committee': committee,
                'committee_name': committee.name,
                'documents': docs_by_committee[committee.id],
            })

    # Get pushed committee vote results
    pushed_votes = CommitteeLegislation.objects.filter(
        pushed_to_chapter=True,
        voting_closed=True
    ).select_related('committee', 'posted_by').order_by('-voting_ended_at')

    # Build vote tallies for pushed votes
    vote_results = {}
    for leg in pushed_votes:
        votes = CommitteeVote.objects.filter(legislation=leg)
        if leg.vote_mode == 'plurality':
            tally = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
            tally['total'] = votes.count()
        else:
            tally = {
                'yes': votes.filter(vote_choice='yes').count(),
                'no': votes.filter(vote_choice='no').count(),
                'abstain': votes.filter(vote_choice='abstain').count(),
                'total': votes.count()
            }
        vote_results[leg.id] = tally

    # Get committees that have pushed votes (documents now only shown in folders section)
    committee_ids_with_votes = set(vote.committee.id for vote in pushed_votes)
    committees = Committee.objects.filter(id__in=committee_ids_with_votes).order_by('name')

    # Organize by committee - only show votes (documents are in folders/uncategorized section)
    committees_data = []
    for committee in committees:
        committee_votes = [vote for vote in pushed_votes if vote.committee.id == committee.id]
        if committee_votes:
            committees_data.append({
                'committee': committee,
                'votes': committee_votes,
                'is_chair': committee.is_chair(request.user),
            })

    # Check if user is officer (for uploads and document management)
    is_officer = request.user.member_type == 'Officer'
    # Check if user is admin (for folder management)
    is_admin = request.user.is_admin

    # Count total content (documents + pushed votes)
    total_content = len(documents) + len(pushed_votes)

    return render(request, 'chapter_documents.html', {
        'folders_with_documents': folders_with_documents,
        'committee_doc_groups': committee_doc_groups,
        'all_folders': all_folders,
        'total_documents': len(documents),
        'total_votes': len(pushed_votes),
        'total_content': total_content,
        'is_officer': is_officer,
        'is_admin': is_admin,
        'committees_data': committees_data,
        'vote_results': vote_results,
    })
