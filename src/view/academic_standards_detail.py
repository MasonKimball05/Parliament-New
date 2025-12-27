from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def academic_standards_detail(request):
    """
    Display detailed information about academic standards and GPA requirements.
    """
    return render(request, 'academic_standards_detail.html')
