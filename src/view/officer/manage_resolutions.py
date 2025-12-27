from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from src.models import PassedResolution, ResolutionSectionImpact
from src.forms import PassedResolutionForm, ResolutionSectionImpactForm
from src.decorators import admin_required


@login_required
@admin_required
def manage_resolutions(request):
    """List all passed resolutions with admin controls"""
    resolutions = PassedResolution.objects.all().prefetch_related('section_impacts')

    context = {
        'resolutions': resolutions,
    }
    return render(request, 'officer/manage_resolutions.html', context)


@login_required
@admin_required
def create_resolution(request):
    """Create a new passed resolution"""
    if request.method == 'POST':
        form = PassedResolutionForm(request.POST, request.FILES)
        if form.is_valid():
            resolution = form.save(commit=False)
            resolution.created_by = request.user
            resolution.save()
            messages.success(request, f'Resolution "{resolution.title}" created successfully!')
            return redirect('manage_section_impacts', resolution_id=resolution.id)
    else:
        form = PassedResolutionForm()

    context = {
        'form': form,
        'action': 'Create',
    }
    return render(request, 'officer/resolution_form.html', context)


@login_required
@admin_required
def edit_resolution(request, resolution_id):
    """Edit an existing passed resolution"""
    resolution = get_object_or_404(PassedResolution, id=resolution_id)

    if request.method == 'POST':
        form = PassedResolutionForm(request.POST, request.FILES, instance=resolution)
        if form.is_valid():
            form.save()
            messages.success(request, f'Resolution "{resolution.title}" updated successfully!')
            return redirect('manage_resolutions')
    else:
        form = PassedResolutionForm(instance=resolution)

    context = {
        'form': form,
        'resolution': resolution,
        'action': 'Edit',
    }
    return render(request, 'officer/resolution_form.html', context)


@login_required
@admin_required
def delete_resolution(request, resolution_id):
    """Delete a passed resolution"""
    resolution = get_object_or_404(PassedResolution, id=resolution_id)

    if request.method == 'POST':
        title = resolution.title
        resolution.delete()
        messages.success(request, f'Resolution "{title}" deleted successfully!')
        return redirect('manage_resolutions')

    context = {
        'resolution': resolution,
    }
    return render(request, 'officer/confirm_delete_resolution.html', context)


@login_required
@admin_required
def manage_section_impacts(request, resolution_id):
    """Manage section impacts for a resolution"""
    resolution = get_object_or_404(PassedResolution, id=resolution_id)
    section_impacts = resolution.section_impacts.all()

    context = {
        'resolution': resolution,
        'section_impacts': section_impacts,
    }
    return render(request, 'officer/manage_section_impacts.html', context)


@login_required
@admin_required
def add_section_impact(request, resolution_id):
    """Add a new section impact to a resolution"""
    resolution = get_object_or_404(PassedResolution, id=resolution_id)

    if request.method == 'POST':
        form = ResolutionSectionImpactForm(request.POST)
        if form.is_valid():
            section_impact = form.save(commit=False)
            section_impact.resolution = resolution
            section_impact.save()
            messages.success(request, f'Section impact "{section_impact.section_name}" added!')
            return redirect('manage_section_impacts', resolution_id=resolution.id)
    else:
        form = ResolutionSectionImpactForm()

    context = {
        'form': form,
        'resolution': resolution,
        'action': 'Add',
    }
    return render(request, 'officer/section_impact_form.html', context)


@login_required
@admin_required
def edit_section_impact(request, impact_id):
    """Edit an existing section impact"""
    section_impact = get_object_or_404(ResolutionSectionImpact, id=impact_id)
    resolution = section_impact.resolution

    if request.method == 'POST':
        form = ResolutionSectionImpactForm(request.POST, instance=section_impact)
        if form.is_valid():
            form.save()
            messages.success(request, f'Section impact "{section_impact.section_name}" updated!')
            return redirect('manage_section_impacts', resolution_id=resolution.id)
    else:
        form = ResolutionSectionImpactForm(instance=section_impact)

    context = {
        'form': form,
        'resolution': resolution,
        'section_impact': section_impact,
        'action': 'Edit',
    }
    return render(request, 'officer/section_impact_form.html', context)


@login_required
@admin_required
def delete_section_impact(request, impact_id):
    """Delete a section impact"""
    section_impact = get_object_or_404(ResolutionSectionImpact, id=impact_id)
    resolution = section_impact.resolution

    if request.method == 'POST':
        name = section_impact.section_name
        section_impact.delete()
        messages.success(request, f'Section impact "{name}" deleted!')
        return redirect('manage_section_impacts', resolution_id=resolution.id)

    context = {
        'section_impact': section_impact,
        'resolution': resolution,
    }
    return render(request, 'officer/confirm_delete_section_impact.html', context)
