from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def manage_event(request):
    return render(request, 'manage_event.html', {})