from ..decorators import *
from ..forms import *
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponseForbidden

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def edit_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can edit this legislation.")

    # Check if the legislation has passed. If it has, redirect to submit a new version.
    if legislation.status == 'passed':
        messages.error(request, "This legislation has passed and cannot be edited. Please submit a new version.")
        return redirect('submit_new_version', legislation_id=legislation.id)

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES, instance=legislation)
        if form.is_valid():
            form.save()
            messages.success(request, "Legislation has been updated.")
            return redirect('view_legislation_history')  # Redirect back to the history page
        else:
            messages.error(request, "Please correct the error below.")

    return render(request, 'edit_legislation.html', {'form': LegislationForm(instance=legislation)})