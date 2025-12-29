from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeDocument
import logging
from django.views.decorators.http import require_http_methods
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file

@require_http_methods(["GET", "POST"])
@login_required
def committee_minutes(request, code):
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can record minutes.')
        return redirect('committee_detail', code=code)

    if request.method == 'POST':
        title = request.POST.get('title')
        date = request.POST.get('date')
        content = request.POST.get('content')
        document_file = request.FILES.get('document')

        # Validate uploaded file if provided
        if document_file:
            try:
                validate_uploaded_file(document_file)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'committee/minutes.html', {
                    'committee': committee,
                    'minutes': CommitteeDocument.objects.filter(
                        committee=committee,
                        document_type='minutes'
                    ).order_by('-meeting_date', '-uploaded_at')
                })

        if title and date:
            # If there's text content but no file, create a text file
            if content and not document_file:
                # Create a simple text file from the content
                text_content = f"Meeting Minutes: {title}\nDate: {date}\n\n{content}"
                text_file = ContentFile(text_content.encode('utf-8'))
                file_name = f"{title.replace(' ', '_')}_{date}.txt"

                doc = CommitteeDocument.objects.create(
                    committee=committee,
                    title=title,
                    description=content[:200] if len(content) > 200 else content,
                    uploaded_by=request.user,
                    document_type='minutes',
                    meeting_date=date
                )
                doc.document.save(file_name, text_file, save=True)
            else:
                # Use the uploaded file
                CommitteeDocument.objects.create(
                    committee=committee,
                    title=title,
                    document=document_file,
                    description=content if content else '',
                    uploaded_by=request.user,
                    document_type='minutes',
                    meeting_date=date
                )

            logger = logging.getLogger('function_calls')
            logger.info(f"{request.user.username} uploaded minutes for {committee.code}: {title}")

            messages.success(request, "Minutes recorded successfully and added to committee documents.")
            return redirect('committee_documents', code=code)
        else:
            messages.error(request, "Please provide a title and date.")

    # Get all minutes for this committee from CommitteeDocument
    minutes = CommitteeDocument.objects.filter(
        committee=committee,
        document_type='minutes'
    ).order_by('-meeting_date', '-uploaded_at')

    return render(request, 'committee/minutes.html', {
        'committee': committee,
        'minutes': minutes,
    })