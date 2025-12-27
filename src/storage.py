import os
from django.core.files.storage import FileSystemStorage
from django.conf import settings


class DualLocationStorage(FileSystemStorage):
    """
    Custom storage that checks both media and exportable_media folders.

    When retrieving a file:
    1. First checks the regular media folder
    2. If not found, checks the exportable_media folder
    3. Returns the first location where the file exists

    When saving a file:
    - Always saves to the regular media folder
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Define the exportable_media location
        self.exportable_location = os.path.join(settings.BASE_DIR, 'exportable_media')

    def path(self, name):
        """
        Return the filesystem path where the file can be retrieved.
        Checks both media and exportable_media locations.
        """
        # First check the regular media location
        regular_path = super().path(name)
        if os.path.exists(regular_path):
            return regular_path

        # If not found, check exportable_media
        exportable_path = os.path.join(self.exportable_location, name)
        if os.path.exists(exportable_path):
            return exportable_path

        # If not found in either location, return the regular path
        # (this maintains normal behavior for new files)
        return regular_path

    def exists(self, name):
        """
        Check if a file exists in either media or exportable_media.
        """
        # Check regular media location
        if super().exists(name):
            return True

        # Check exportable_media location
        exportable_path = os.path.join(self.exportable_location, name)
        return os.path.exists(exportable_path)

    def url(self, name):
        """
        Return the URL where the file can be accessed.
        Uses the regular URL for both locations since Django will serve from either.
        """
        return super().url(name)
