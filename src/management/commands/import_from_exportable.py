import os
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from src.models import Legislation, ParliamentUser
from datetime import datetime


class Command(BaseCommand):
    help = 'Imports legislation documents from exportable_media/legislation_docs folder (for production-ready files)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--copy',
            action='store_true',
            help='Copy files to media folder instead of leaving them in exportable_media',
        )

    def handle(self, *args, **kwargs):
        copy_files = kwargs.get('copy', False)

        self.stdout.write('Scanning exportable_media/legislation_docs folder...')

        # Get the exportable_media/legislation_docs directory
        project_root = settings.BASE_DIR
        exportable_docs_dir = os.path.join(project_root, 'exportable_media', 'legislation_docs')

        if not os.path.exists(exportable_docs_dir):
            self.stdout.write(self.style.ERROR(f'Directory not found: {exportable_docs_dir}'))
            self.stdout.write(self.style.WARNING('Creating exportable_media/legislation_docs folder...'))
            os.makedirs(exportable_docs_dir, exist_ok=True)
            self.stdout.write(self.style.SUCCESS('Folder created. Add PDF/DOCX files and run this command again.'))
            return

        # Get the first admin user to set as posted_by
        admin_user = ParliamentUser.objects.filter(is_admin=True).first()
        if not admin_user:
            self.stdout.write(self.style.ERROR('No admin user found. Please create an admin user first.'))
            return

        # Scan for PDF and DOCX files
        allowed_extensions = ('.pdf', '.docx', '.doc')
        files_found = []

        for filename in os.listdir(exportable_docs_dir):
            if filename.lower().endswith(allowed_extensions):
                files_found.append(filename)

        if not files_found:
            self.stdout.write(self.style.WARNING('No PDF or DOCX files found in exportable_media/legislation_docs folder.'))
            return

        self.stdout.write(f'Found {len(files_found)} document(s)')
        if copy_files:
            self.stdout.write(self.style.WARNING('Files will be COPIED to media folder'))
        else:
            self.stdout.write(self.style.SUCCESS('Files will remain in exportable_media (recommended for production)'))

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

            full_path = os.path.join(exportable_docs_dir, filename)

            if copy_files:
                # Copy file to media folder
                from django.core.files import File
                legislation = Legislation(
                    title=title,
                    description=f'Imported from exportable_media: {filename}',
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

                with open(full_path, 'rb') as f:
                    legislation.document.save(filename, File(f), save=True)

                self.stdout.write(self.style.SUCCESS(f'  Created & Copied: "{title}" (ID: {legislation.id})'))
                self.stdout.write(f'    Copied to: {legislation.document.url}')
            else:
                # Leave file in exportable_media, just create the database entry
                # The DualLocationStorage will find it in exportable_media
                relative_path = f'legislation_docs/{filename}'

                legislation = Legislation.objects.create(
                    title=title,
                    description=f'Imported from exportable_media: {filename}',
                    document=relative_path,  # Will be found by DualLocationStorage
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

                self.stdout.write(self.style.SUCCESS(f'  Created: "{title}" (ID: {legislation.id})'))
                self.stdout.write(f'    Source: exportable_media/{relative_path}')

            created_count += 1

        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Import complete!'))
        self.stdout.write(self.style.SUCCESS(f'  Created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'  Skipped: {skipped_count}'))
        self.stdout.write(self.style.SUCCESS(f'  Total legislation in database: {Legislation.objects.count()}'))

        if not copy_files:
            self.stdout.write('\n' + self.style.WARNING('NOTE: Files remain in exportable_media folder.'))
            self.stdout.write(self.style.WARNING('The DualLocationStorage will find them when serving.'))
            self.stdout.write(self.style.WARNING('Deploy exportable_media to production for these files to work.'))

    def _generate_title_from_filename(self, filename):
        """Generate a readable title from the filename"""
        # Remove extension
        name = os.path.splitext(filename)[0]

        # Replace underscores and hyphens with spaces
        name = name.replace('_', ' ').replace('-', ' ')

        # Capitalize words
        name = ' '.join(word.capitalize() for word in name.split())

        return name
