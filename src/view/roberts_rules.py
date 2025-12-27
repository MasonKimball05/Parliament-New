from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def roberts_rules(request):
    """
    Display Robert's Rules of Order with navigation and search functionality.
    """
    return render(request, 'roberts_rules.html')
