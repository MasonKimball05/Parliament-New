from ..models import *
from ..decorators import *
from ..forms import *
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def submit_new_version(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can submit a new version.")

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            new_legislation = form.save(commit=False)
            new_legislation.posted_by = request.user
            # Optionally: mark this as a new version of the old legislation
            new_legislation.previous_version = legislation  # Assuming you have a previous_version field
            new_legislation.save()

            messages.success(request, "New version of the legislation has been submitted.")
            return redirect('view_legislation_history')  # Redirect to the history page or wherever appropriate
        else:
            messages.error(request, "Please correct the error below.")
    else:
        # Prepopulate the form with the old legislation data
        form = LegislationForm(instance=legislation)

    return render(request, 'submit_new_version.html', {'form': form, 'legislation': legislation})