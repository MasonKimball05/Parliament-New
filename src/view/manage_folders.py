"""
Views for managing chapter document folders
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import ChapterFolder
from src.decorators import admin_required


class FolderRefused(Exception):
    """Why ``create_chapter_folder`` didn't make the folder (the page's error message)."""


def create_chapter_folder(user, name, description=''):
    """
    Make a chapter document folder (``create_folder``, shared with the app's
    src/mobile/views_document_manage.py). Names are required, unique and
    fit the column.
    Raises ``FolderRefused``.
    """
    folder_name = (name or '').strip()
    folder_description = (description or '').strip()

    if not folder_name:
        raise FolderRefused('Folder name is required.')
    if len(folder_name) > ChapterFolder._meta.get_field('name').max_length:
        raise FolderRefused('That folder name is too long (100 characters at most).')

    # Check if folder already exists
    if ChapterFolder.objects.filter(name=folder_name).exists():
        raise FolderRefused(f'A folder named "{folder_name}" already exists.')

    return ChapterFolder.objects.create(
        name=folder_name,
        description=folder_description,
        created_by=user
    )


def delete_chapter_folder(folder):
    """Delete a folder; its documents move to no folder (SET_NULL). Returns its name."""
    folder_name = folder.name
    folder.delete()
    return folder_name


@admin_required
def create_folder(request):
    """Allow officers to create custom folders for chapter documents"""
    if request.method == 'POST':
        try:
            folder = create_chapter_folder(request.user, request.POST.get('folder_name', ''),
                                           request.POST.get('folder_description', ''))
        except FolderRefused as e:
            messages.error(request, str(e))
            return redirect('chapter_documents')

        messages.success(request, f'Folder "{folder.name}" created successfully!')
        return redirect('manage_chapter_documents')

    return redirect('manage_chapter_documents')


@admin_required
def delete_folder(request, folder_id):
    """Allow officers to delete custom folders"""
    folder = get_object_or_404(ChapterFolder, id=folder_id)

    if request.method == 'POST':
        # Documents in the folder will have their chapter_folder set to NULL (SET_NULL)
        folder_name = delete_chapter_folder(folder)
        messages.success(request, f'Folder "{folder_name}" deleted successfully. Documents moved to "Uncategorized".')
        return redirect('manage_chapter_documents')

    messages.error(request, 'Invalid request.')
    return redirect('manage_chapter_documents')
