from ..decorators import *
from django.contrib import messages
from ..forms import *
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

@login_required
@officer_required
@log_function_call
def upload_legislation(request):

    if request.method == 'POST':
        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            legislation = form.save(commit=False)
            legislation.posted_by = request.user
            legislation.save()
            return redirect('vote')
        else:
            messages.error(request, "There was an error with your submission.")
    else:
        form = LegislationForm()

    return render(request, 'vote.html', {'form': form})