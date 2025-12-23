"""
Views for managing chapter document folders
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import ChapterFolder
from src.decorators import admin_required


@admin_required
def create_folder(request):
    """Allow officers to create custom folders for chapter documents"""
    if request.method == 'POST':
        folder_name = request.POST.get('folder_name', '').strip()
        folder_description = request.POST.get('folder_description', '').strip()

        if not folder_name:
            messages.error(request, 'Folder name is required.')
            return redirect('chapter_documents')

        # Check if folder already exists
        if ChapterFolder.objects.filter(name=folder_name).exists():
            messages.error(request, f'A folder named "{folder_name}" already exists.')
            return redirect('chapter_documents')

        # Create the folder
        folder = ChapterFolder.objects.create(
            name=folder_name,
            description=folder_description,
            created_by=request.user
        )

        messages.success(request, f'Folder "{folder_name}" created successfully!')
        return redirect('manage_chapter_documents')

    return redirect('manage_chapter_documents')


@admin_required
def delete_folder(request, folder_id):
    """Allow officers to delete custom folders"""
    folder = get_object_or_404(ChapterFolder, id=folder_id)

    if request.method == 'POST':
        folder_name = folder.name
        # Documents in the folder will have their chapter_folder set to NULL (SET_NULL)
        folder.delete()
        messages.success(request, f'Folder "{folder_name}" deleted successfully. Documents moved to "Uncategorized".')
        return redirect('manage_chapter_documents')

    messages.error(request, 'Invalid request.')
    return redirect('manage_chapter_documents')
