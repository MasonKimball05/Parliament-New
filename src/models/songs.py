from django.db import models
from src.storage import DualLocationStorage


class SongCategory(models.Model):
    """Categories for organizing songs in the songbook (Hymns, Drinking Songs, etc.)"""
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=20,
        default='blue',
        help_text='Badge color: blue, green, red, yellow, purple, pink, gray'
    )
    description = models.TextField(blank=True)
    display_order = models.IntegerField(default=0, help_text='Lower numbers appear first')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = "Song Categories"

    def __str__(self):
        return self.name


class Song(models.Model):
    """Songs in the chapter songbook with lyrics and optional audio"""
    title = models.CharField(max_length=200)
    lyrics = models.TextField(help_text='Full song lyrics')
    audio_file = models.FileField(
        upload_to='songbook/audio/',
        storage=DualLocationStorage(),
        blank=True,
        null=True,
        help_text='Optional: Audio file (MP3, WAV, M4A)'
    )
    category = models.ForeignKey(
        SongCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='songs'
    )
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='songs_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide song')

    class Meta:
        ordering = ['title']

    def __str__(self):
        if self.category:
            return f"{self.title} ({self.category.name})"
        return self.title

    def has_audio(self):
        """Check if song has an audio file"""
        return bool(self.audio_file)
