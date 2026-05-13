from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def slating_elections_detail(request):
    """
    Display detailed information about officer slating and election procedures.
    """
    return render(request, 'slating_elections_detail.html')
