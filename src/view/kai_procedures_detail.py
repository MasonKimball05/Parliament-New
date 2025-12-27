from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def kai_procedures_detail(request):
    """
    Display detailed information about Kai Committee procedures and processes.
    """
    return render(request, 'kai_procedures_detail.html')
