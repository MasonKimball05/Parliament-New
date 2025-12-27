from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def advisors_detail(request):
    """
    Display detailed information about chapter advisors and their roles.
    """
    return render(request, 'advisors_detail.html')
