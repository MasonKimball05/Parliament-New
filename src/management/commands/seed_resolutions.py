from django.core.management.base import BaseCommand
from django.utils import timezone
from src.models import PassedResolution, ResolutionSectionImpact, ParliamentUser
from datetime import date


class Command(BaseCommand):
    help = 'Seeds initial passed resolutions from hardcoded template data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding initial passed resolutions...')

        # Get the first admin user to set as creator
        creator = ParliamentUser.objects.filter(is_admin=True).first()
        if not creator:
            self.stdout.write(self.style.ERROR('No admin user found. Please create an admin user first.'))
            return

        # Resolution 1: Constitution and Bylaws
        resolution1, created = PassedResolution.objects.get_or_create(
            title='Constitution and Bylaws',
            defaults={
                'description': 'Official adoption of the Alpha Mu Chapter Constitution and Bylaws, establishing the governance framework for the chapter.',
                'date_passed': date(2025, 1, 26),
                'border_color': 'green',
                'impact_summary': 'Establishes the complete governance framework for the Alpha Mu Chapter.',
                'display_order': 1,
                'is_active': True,
                'created_by': creator,
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created resolution: {resolution1.title}'))

            # Add section impacts for Constitution and Bylaws
            sections = [
                ('Constitution Article I (Name & Purpose)', 'constitution', '#const-name'),
                ('Constitution Article II (Membership)', 'constitution', '#const-membership'),
                ('Constitution Article III (Leadership)', 'constitution', '#const-leadership'),
                ('Constitution Article IV (Meetings)', 'constitution', '#const-meetings'),
                ('Constitution Article V (Committees)', 'constitution', '#const-committees'),
                ('Constitution Article VI (Amendments)', 'constitution', '#const-amendments'),
                ('Bylaws Article I (Membership)', 'bylaws', '#bylaws-membership'),
                ('Bylaws Article II (Officers)', 'bylaws', '#bylaws-officers'),
                ('Bylaws Article III (Self-Governance)', 'bylaws', '#bylaws-governance'),
                ('Bylaws Article IV (Financial)', 'bylaws', '#bylaws-financial'),
                ('Bylaws Article V (Rituals & Ceremonies)', 'bylaws', '#bylaws-rituals'),
                ('Bylaws Article VI (Board Expectations)', 'bylaws', '#bylaws-expectations'),
                ('Bylaws Article VII (Committees)', 'bylaws', '#bylaws-committees'),
            ]

            for idx, (name, section_type, anchor) in enumerate(sections):
                ResolutionSectionImpact.objects.create(
                    resolution=resolution1,
                    section_name=name,
                    section_type=section_type,
                    section_anchor=anchor,
                    display_order=idx
                )

            self.stdout.write(self.style.SUCCESS(f'  Added {len(sections)} section impacts'))
        else:
            self.stdout.write(self.style.WARNING(f'Resolution already exists: {resolution1.title}'))

        # Resolution 2: Parliamentarian
        resolution2, created = PassedResolution.objects.get_or_create(
            title='Parliamentarian Resolution',
            defaults={
                'description': 'Resolution clarifying the role and responsibilities of the Parliamentarian (Executive Vice President) in chapter meetings.',
                'date_passed': date(2025, 3, 27),
                'border_color': 'blue',
                'impact_summary': "Clarifies EVP's role in applying Robert's Rules and maintaining parliamentary procedure during meetings.",
                'display_order': 2,
                'is_active': True,
                'created_by': creator,
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created resolution: {resolution2.title}'))

            # Add section impacts for Parliamentarian
            sections = [
                ('Constitution Article III (Leadership)', 'constitution', '#const-leadership'),
                ('Bylaws Article VI (Board Expectations)', 'bylaws', '#bylaws-expectations'),
                ('EVP Duties', 'other', '', '/constitution-bylaws/officer-duties/'),  # external URL
            ]

            for idx, data in enumerate(sections):
                if len(data) == 4:
                    name, section_type, anchor, external_url = data
                    ResolutionSectionImpact.objects.create(
                        resolution=resolution2,
                        section_name=name,
                        section_type=section_type,
                        section_anchor=anchor if anchor else '',
                        external_url=external_url if external_url else '',
                        display_order=idx
                    )
                else:
                    name, section_type, anchor = data
                    ResolutionSectionImpact.objects.create(
                        resolution=resolution2,
                        section_name=name,
                        section_type=section_type,
                        section_anchor=anchor,
                        display_order=idx
                    )

            self.stdout.write(self.style.SUCCESS(f'  Added {len(sections)} section impacts'))
        else:
            self.stdout.write(self.style.WARNING(f'Resolution already exists: {resolution2.title}'))

        # Resolution 3: Sweetheart
        resolution3, created = PassedResolution.objects.get_or_create(
            title='Sweetheart Resolution',
            defaults={
                'description': 'Resolution establishing the chapter Sweetheart position, selection process, and responsibilities.',
                'date_passed': date(2025, 3, 27),
                'border_color': 'pink',
                'impact_summary': 'Establishes the Sweetheart role and selection procedures for the chapter.',
                'display_order': 3,
                'is_active': True,
                'created_by': creator,
            }
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f'Created resolution: {resolution3.title}'))

            # Add section impacts for Sweetheart
            sections = [
                ('Bylaws Article VII (Committees)', 'bylaws', '#bylaws-committees'),
                ('Brotherhood Committee Details', 'other', '', '/constitution-bylaws/committees/'),  # external URL
            ]

            for idx, data in enumerate(sections):
                if len(data) == 4:
                    name, section_type, anchor, external_url = data
                    ResolutionSectionImpact.objects.create(
                        resolution=resolution3,
                        section_name=name,
                        section_type=section_type,
                        section_anchor=anchor if anchor else '',
                        external_url=external_url if external_url else '',
                        display_order=idx
                    )
                else:
                    name, section_type, anchor = data
                    ResolutionSectionImpact.objects.create(
                        resolution=resolution3,
                        section_name=name,
                        section_type=section_type,
                        section_anchor=anchor,
                        display_order=idx
                    )

            self.stdout.write(self.style.SUCCESS(f'  Added {len(sections)} section impacts'))
        else:
            self.stdout.write(self.style.WARNING(f'Resolution already exists: {resolution3.title}'))

        self.stdout.write(self.style.SUCCESS('\nSeeding complete!'))
        self.stdout.write(self.style.SUCCESS(f'Total resolutions: {PassedResolution.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'Total section impacts: {ResolutionSectionImpact.objects.count()}'))
