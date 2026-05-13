from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import PassedResolution

@login_required
def passed_resolutions(request):
    """
    Display all passed resolutions and their impact on Constitution & Bylaws.
    """
    resolutions = PassedResolution.objects.filter(is_active=True).prefetch_related('section_impacts')

    context = {
        'resolutions': resolutions,
    }
    return render(request, 'passed_resolutions.html', context)
