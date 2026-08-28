import logging
import os

from django import forms
import magic  # python-magic for MIME type detection

from src.models import Song, SongCategory

logger = logging.getLogger(__name__)


class SongForm(forms.ModelForm):
    """Form for creating and editing songs in the songbook"""

    class Meta:
        model = Song
        fields = ['title', 'category', 'lyrics', 'audio_file']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter song title'
            }),
            'category': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'lyrics': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono',
                'rows': 15,
                'placeholder': 'Enter song lyrics...\n\nVerse 1:\nFirst line of verse...\nSecond line...\n\nChorus:\nChorus lyrics...'
            }),
            'audio_file': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'accept': '.mp3,.wav,.m4a,.ogg,.flac'
            }),
        }
        labels = {
            'title': 'Song Title',
            'category': 'Category',
            'lyrics': 'Lyrics',
            'audio_file': 'Audio File (Optional)',
        }
        help_texts = {
            'lyrics': 'Enter the full lyrics. Line breaks will be preserved.',
            'audio_file': 'Upload an audio recording (MP3, WAV, M4A, OGG, or FLAC). Max 50MB.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = SongCategory.objects.all()
        self.fields['category'].required = False
        self.fields['audio_file'].required = False

    def clean_audio_file(self):
        file = self.cleaned_data.get('audio_file')
        if file:
            # Check file size (50 MB max for audio)
            if file.size > 50 * 1024 * 1024:
                raise forms.ValidationError('Audio file size must not exceed 50 MB.')

            # Check file extension
            allowed_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac']
            ext = os.path.splitext(file.name)[1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError(
                    f'Invalid file type. Allowed types: MP3, WAV, M4A, OGG, FLAC.'
                )

            # Check MIME type
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)

                allowed_mimes = [
                    'audio/mpeg',       # MP3
                    'audio/mp3',        # MP3 alternate
                    'audio/wav',        # WAV
                    'audio/x-wav',      # WAV alternate
                    'audio/mp4',        # M4A
                    'audio/x-m4a',      # M4A alternate
                    'audio/ogg',        # OGG
                    'audio/flac',       # FLAC
                    'audio/x-flac',     # FLAC alternate
                ]

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid audio file type. Expected audio file, but got {mime}.'
                    )
            except Exception as e:
                logger.warning(f"Audio file MIME check failed: {e}")
                # Allow if we can't check MIME but extension is valid
                pass

        return file


class SongCategoryForm(forms.ModelForm):
    """Form for creating and editing song categories"""

    class Meta:
        model = SongCategory
        fields = ['name', 'color', 'description', 'display_order']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Category name (e.g., Hymns)'
            }),
            'color': forms.Select(
                choices=[
                    ('blue', 'Blue'),
                    ('green', 'Green'),
                    ('red', 'Red'),
                    ('yellow', 'Yellow'),
                    ('purple', 'Purple'),
                    ('pink', 'Pink'),
                    ('gray', 'Gray'),
                ],
                attrs={
                    'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
                }
            ),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'rows': 2,
                'placeholder': 'Optional description'
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 0
            }),
        }
        help_texts = {
            'display_order': 'Lower numbers appear first in the list.',
        }
