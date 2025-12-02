from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def officer_home(request):
    return render(request, 'officer_home.html')