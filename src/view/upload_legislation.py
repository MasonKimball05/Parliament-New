from ..decorators import *
from django.contrib import messages
from django.core.exceptions import ValidationError
from ..forms import *
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from src.utils.file_validation import validate_uploaded_file

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
            return redirect('vote')
        else:
            messages.error(request, "There was an error with your submission.")
    else:
        form = LegislationForm()

    return render(request, 'vote.html', {'form': form})