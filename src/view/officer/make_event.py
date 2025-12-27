from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.decorators import officer_required

@login_required
@officer_required
def make_event(request):
    return render(request, 'make_event.html', {})