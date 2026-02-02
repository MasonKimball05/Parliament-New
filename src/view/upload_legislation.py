from ..decorators import *
from django.contrib import messages
from django.core.exceptions import ValidationError
from ..forms import *
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from src.utils.file_validation import validate_uploaded_file
from src.notification_service import notify_all_active_members

@login_required
@officer_required
@log_function_call
def upload_legislation(request):

    if request.method == 'POST':
        # Validate uploaded file before processing form
        if 'document' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['document'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return redirect('upload_legislation')

        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            legislation = form.save(commit=False)
            legislation.posted_by = request.user
            legislation.save()

            # Send in-app notification to all active members
            try:
                notify_all_active_members(
                    'legislation_new',
                    f'New Legislation: {legislation.title}',
                    link='/vote/',
                    source_type='Legislation',
                    source_id=legislation.id,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to create legislation notifications: {e}", exc_info=True)

            return redirect('vote')
        else:
            messages.error(request, "There was an error with your submission.")
    else:
        form = LegislationForm()

    return render(request, 'vote.html', {'form': form})