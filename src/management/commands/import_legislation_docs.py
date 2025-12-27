import os
import shutil
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.core.files import File
from src.models import Legislation, ParliamentUser
from datetime import datetime


class Command(BaseCommand):
    help = 'Imports legislation documents from the project legislation_docs folder and creates Legislation entries'

    def handle(self, *args, **kwargs):
        self.stdout.write('Scanning legislation_docs folder...')

        # Get the legislation_docs directory from project root
        project_root = settings.BASE_DIR
        legislation_docs_dir = os.path.join(project_root, 'legislation_docs')

        if not os.path.exists(legislation_docs_dir):
            self.stdout.write(self.style.ERROR(f'Directory not found: {legislation_docs_dir}'))
            return

        # Get the first admin user to set as posted_by
        admin_user = ParliamentUser.objects.filter(is_admin=True).first()
        if not admin_user:
            self.stdout.write(self.style.ERROR('No admin user found. Please create an admin user first.'))
            return

        # Scan for PDF and DOCX files
        allowed_extensions = ('.pdf', '.docx', '.doc')
        files_found = []

        for filename in os.listdir(legislation_docs_dir):
            if filename.lower().endswith(allowed_extensions):
                files_found.append(filename)

        if not files_found:
            self.stdout.write(self.style.WARNING('No PDF or DOCX files found in legislation_docs folder.'))
            return

        self.stdout.write(f'Found {len(files_found)} document(s)')

        created_count = 0
        skipped_count = 0

        for filename in files_found:
            # Generate a title from the filename
            title = self._generate_title_from_filename(filename)

            # Check if legislation with this title already exists
            existing = Legislation.objects.filter(title__iexact=title).first()

            if existing:
                self.stdout.write(self.style.WARNING(f'  Skipped: {filename} (already exists as "{existing.title}")'))
                skipped_count += 1
                continue

            # Create new legislation entry
            full_path = os.path.join(legislation_docs_dir, filename)

            legislation = Legislation(
                title=title,
                description=f'Imported from {filename}',
                posted_by=admin_user,
                available_at=timezone.now(),
                passed=True,
                status='passed',
                voting_closed=True,
                required_percentage='51',
                anonymous_vote=False,
                allow_abstain=True,
                vote_mode='percentage'
            )

            # Open and attach the file
            with open(full_path, 'rb') as f:
                legislation.document.save(filename, File(f), save=True)

            self.stdout.write(self.style.SUCCESS(f'  Created: "{title}" (ID: {legislation.id})'))
            self.stdout.write(f'    File: {legislation.document.url}')
            created_count += 1

        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Import complete!'))
        self.stdout.write(self.style.SUCCESS(f'  Created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'  Skipped: {skipped_count}'))
        self.stdout.write(self.style.SUCCESS(f'  Total legislation in database: {Legislation.objects.count()}'))

    def _generate_title_from_filename(self, filename):
        """Generate a readable title from the filename"""
        # Remove extension
        name = os.path.splitext(filename)[0]

        # Replace underscores and hyphens with spaces
        name = name.replace('_', ' ').replace('-', ' ')

        # Capitalize words
        name = ' '.join(word.capitalize() for word in name.split())

        return name
