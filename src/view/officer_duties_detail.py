from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def officer_duties_detail(request):
    """
    Display detailed information about officer duties and responsibilities.
    """
    return render(request, 'officer_duties_detail.html')
