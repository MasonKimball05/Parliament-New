from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.forms import CommitteeDocumentForm
from src.decorators import officer_required

@login_required
@officer_required
def upload_report(request):
    """View for officers to upload committee reports and documents"""
    if request.method == 'POST':
        form = CommitteeDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            document = form.save(commit=False)
            document.uploaded_by = request.user
            document.save()
            messages.success(request, f'Document "{document.title}" has been uploaded successfully!')
            return redirect('officer_home')
        else:
            messages.error(request, 'There was an error uploading the document. Please check the form and try again.')
    else:
        form = CommitteeDocumentForm()

    context = {
        'form': form,
    }

    return render(request, 'officer/upload_report.html', context)
