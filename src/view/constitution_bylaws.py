from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def constitution_bylaws(request):
    """
    Display the chapter's Constitution and Bylaws with navigation and search functionality.
    """
    return render(request, 'constitution_bylaws.html')
