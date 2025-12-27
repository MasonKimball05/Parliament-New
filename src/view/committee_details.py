from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def committee_details(request):
    """
    Display detailed information about all chapter committees.
    """
    return render(request, 'committee_details.html')
