from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, get_object_or_404
from ..decorators import *
from ..models import *
from django.contrib import messages
from django.http import HttpResponseForbidden

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
@log_function_call
def reopen_legislation(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Prevent reopening if already passed
    if legislation.status == 'passed':
        messages.error(request, "This legislation has already passed and cannot be reopened.")
        return redirect('view_legislation_history')  # Redirect to the history page after the message

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can reopen this legislation.")

    legislation.voting_closed = False  # Reopen the voting
    legislation.save()

    messages.success(request, "Legislation has been reopened.")
    return redirect('view_legislation_history')