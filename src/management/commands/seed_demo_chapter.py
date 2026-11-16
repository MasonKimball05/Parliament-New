"""
Fill a scratch database with a made-up chapter, for App Store screenshots and
for trying the app without real members' data (11-16-26).

    manage.py seed_demo_chapter            # create (or rebuild) the demo chapter
    manage.py seed_demo_chapter --reset    # remove it again

Everything is fictional: invented names, @example.com emails, 555-01xx phone
numbers (the range set aside for fiction) and original song lyrics. Dates are
relative to today, so the calendar always has a week ahead to show.

It signs in as ``demo`` (a member with an officer role, so the officer tools
show up too) with a fresh password printed once. That account is exempt from
2FA so the simulator can sign straight in.

**Refuses to run on a database with real members.** A demo member's user id
starts with ``DEMO-``; if any active member without that prefix exists, it
stops, unless ``--alongside`` is given AND DEBUG is on (a developer's local
copy). Production has DEBUG off, so it can never seed there.

Nothing is sent: rows are written directly (notifications go out from the
views, not on save), events have no GroupMe groups and their reminders are
off, announcements don't email.
"""
import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

PREFIX = 'DEMO-'
DEMO_USERNAME = 'demo'
SONG_CATEGORIES = ['Chapter Songs (demo)', 'Serenades (demo)']
CHANNEL_NAMES = ['Chapter', 'Social Committee', 'Philanthropy']
COMMITTEE_CODES = ['SOCDEMO', 'PHILDEMO']

# (first, last, pledge class, year, major, house)
MEMBERS = [
    ('Jordan', 'Ellis', 'Fall 2023', 2027, 'Economics', 'Smith'),
    ('Marcus', 'Bell', 'Fall 2023', 2027, 'Biology', 'Duncan'),
    ('Ethan', 'Park', 'Spring 2024', 2027, 'Computer Science', 'Knox'),
    ('Caleb', 'Reyes', 'Fall 2024', 2028, 'Accounting', 'Marshall'),
    ('Noah', 'Whitfield', 'Fall 2022', 2026, 'History', 'Linton'),
    ('Owen', 'Castillo', 'Fall 2024', 2028, 'Nursing', 'Hardin'),
    ('Isaac', 'Turner', 'Spring 2025', 2028, 'Journalism', 'Ryan'),
    ('Luke', 'Sandoval', 'Fall 2022', 2026, 'Finance', 'Gordon'),
    ('Henry', 'Brooks', 'Fall 2023', 2027, 'Music', 'Smith'),
    ('Samuel', 'Okafor', 'Spring 2024', 2027, 'Pre-Law', 'Duncan'),
    ('Wyatt', 'Hale', 'Fall 2024', 2028, 'Exercise Science', 'Knox'),
    ('Daniel', 'Moreno', 'Fall 2023', 2027, 'Marketing', 'Marshall'),
    ('Gabriel', 'Foster', 'Spring 2025', 2028, 'Theology', 'Linton'),
    ('Aaron', 'Lindqvist', 'Fall 2022', 2026, 'Engineering Physics', 'Hardin'),
    ('Elijah', 'Grant', 'Fall 2024', 2028, 'Political Science', 'Ryan'),
    ('Miles', 'Donovan', 'Spring 2024', 2027, 'Psychology', 'Gordon'),
    ('Julian', 'Ashford', 'Fall 2023', 2027, 'Chemistry', 'Smith'),
    ('Thomas', 'Vance', 'Fall 2025', 2029, 'Undeclared', 'Duncan'),
    ('Levi', 'Carmichael', 'Fall 2025', 2029, 'Business', 'Knox'),
    ('Adrian', 'Nakamura', 'Fall 2025', 2029, 'Graphic Design', 'Marshall'),
]
PLEDGES = [
    ('Ryan', 'Holloway', 'Fall 2026', 2030, 'Biology', 'Linton'),
    ('Cole', 'Batista', 'Fall 2026', 2030, 'Economics', 'Hardin'),
    ('Dylan', 'Frost', 'Fall 2026', 2030, 'Undeclared', 'Ryan'),
]
# role code, role name, index into MEMBERS (the demo account is the Secretary)
OFFICERS = [
    ('PRES', 'President', 0),
    ('VP', 'Vice President', 1),
    ('TREAS', 'Treasurer', 3),
    ('SOCIAL', 'Social Chair', 5),
    ('PHIL', 'Philanthropy Chair', 6),
]
DEMO_ROLE = ('SEC', 'Secretary')

SONGS = [
    ('Hymn of the Lamplight', SONG_CATEGORIES[0], (
        'When evening falls on College Hill\n'
        'And lamps are lit along the way,\n'
        'We gather where the old oaks stand\n'
        'To keep the vows of yesterday.\n\n'
        'Raise high the light, my brothers all,\n'
        'Through every year and every hall;\n'
        'Though roads may part and seasons turn,\n'
        'Still may the lamplight burn.')),
    ('The Long Table', SONG_CATEGORIES[0], (
        'Pull up a chair, there’s room for one more,\n'
        'The kettle’s on and the door’s unlocked;\n'
        'We’ve stories to tell from the seasons before\n'
        'And a hundred more on the clock.\n\n'
        'So here’s to the table, long and worn,\n'
        'To the friends we found and the ones yet born.')),
    ('Starlight Serenade', SONG_CATEGORIES[1], (
        'Beneath your window, soft and low,\n'
        'We sing the only song we know:\n'
        'The stars are out, the night is kind,\n'
        'And you’re the one we had in mind.')),
    ('Homecoming Round', SONG_CATEGORIES[1], (
        'Ring the bell and light the hall,\n'
        'Home again, home again, one and all;\n'
        'Sing it once and sing it round,\n'
        'Home again to the old home ground.')),
]

CHAT = [
    (1, 'Reminder that chapter is at 7 tonight. Business casual.', 0),
    (4, 'Is the projector working again or should I bring my laptop?', 0),
    (0, 'Bring it just in case.', 0),
    (5, 'Social calendar for the rest of the semester is posted, check the announcement', 0),
    (9, 'The 5K sign-up sheet is open on the calendar. We need 6 more volunteers for the water station.', 0),
    (2, 'Put me down', 0),
    (11, 'Same, and I can drive two people', 0),
    (13, 'Who has the keys to the storage closet?', 0),
    (8, 'Front desk has a spare', 0),
    (6, 'Thanks to everyone who came out Saturday, we raised $2,140 for the food bank 🎉', 0),
    (1, 'Huge. Great job Isaac', 0),
    (5, 'Theme for the formal is “Winter Lodge”. Tickets go on sale Monday.', 1),
    (12, 'Can dates be from off campus?', 1),
    (5, 'Yes, just add their name on the form by Friday', 1),
    (6, 'Route map for the 5K is attached to the event. Volunteers meet at 7:15.', 2),
    (10, 'I’ll bring the cones from the rec center', 2),
]


def _password(n=16):
    alphabet = ''.join(c for c in string.ascii_letters + string.digits if c not in 'Il1O0o')
    return ''.join(secrets.choice(alphabet) for _ in range(n))


class Command(BaseCommand):
    help = 'Create (or --reset) a made-up chapter for screenshots and demos. Refuses on a real database.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Remove the demo chapter instead.')
        parser.add_argument('--alongside', action='store_true',
                            help='Seed next to existing members (only with DEBUG on).')

    def handle(self, *args, **options):
        from src.models import ParliamentUser
        real = (ParliamentUser.objects.filter(is_active=True)
                .exclude(user_id__startswith=PREFIX).exclude(username=DEMO_USERNAME))
        if real.exists() and not (options['alongside'] and settings.DEBUG):
            raise CommandError(
                f'This database has {real.count()} real member account(s), so it is not seeding a demo '
                'chapter here. Use a scratch database (DB_NAME=...). On a local copy with DEBUG on, '
                '--alongside seeds next to them.')
        with transaction.atomic():
            removed = self.remove()
            if options['reset']:
                self.stdout.write(self.style.SUCCESS(f'Removed the demo chapter ({removed} members).'))
                return
            password = self.create()
        self.stdout.write(self.style.SUCCESS(
            f'Demo chapter ready: {len(MEMBERS) + len(PLEDGES) + 1} members, events for the next three '
            'weeks, announcements, chat, songs, a vote open now and a passed one.'))
        self.stdout.write(f'Sign in as {DEMO_USERNAME} / {password}  (shown once)')

    # ── removal ───────────────────────────────────────────────────────────

    def remove(self):
        from src.models import (
            Announcement, ChatChannel, Committee, Event, Legislation, ParliamentUser, Quote, Song, SongCategory,
        )
        users = ParliamentUser.objects.filter(user_id__startswith=PREFIX)
        n = users.count()
        Event.objects.filter(created_by__in=users).delete()
        Announcement.objects.filter(posted_by__in=users).delete()
        Legislation.objects.filter(posted_by__in=users).delete()
        Quote.objects.filter(quoted_member__in=users).delete()
        Song.objects.filter(created_by__in=users).delete()
        SongCategory.objects.filter(name__in=SONG_CATEGORIES).delete()
        ChatChannel.objects.filter(created_by__in=users, name__in=CHANNEL_NAMES).delete()
        Committee.objects.filter(code__in=COMMITTEE_CODES).delete()
        users.delete()
        return n

    # ── creation ──────────────────────────────────────────────────────────

    def user(self, n, first, last, member_type='Member', **extra):
        from src.models import ParliamentUser
        handle = f'{first}.{last}'.lower()
        u = ParliamentUser(
            user_id=f'{PREFIX}{n:03d}', username=extra.pop('username', handle), name=f'{first} {last}',
            member_type=member_type, member_status='Active', email=f'{handle}@example.com',
            phone_number=f'205-555-01{n % 100:02d}', onboarding_complete=True, role_number=str(9000 + n),
            **extra)
        u.set_unusable_password()
        u.save()
        return u

    def create(self):
        from src.models import (
            Announcement, ChatChannel, ChatMessage, Committee, Event, Legislation, Quote, Role, Song,
            SongCategory,
        )
        from src.models.users import TwoFactorRequirement
        now = timezone.now()

        members = [self.user(i + 1, f, l, pledge_class=pc, graduation_year=y, majors=[m], house=h)
                   for i, (f, l, pc, y, m, h) in enumerate(MEMBERS)]
        for i, (f, l, pc, y, m, h) in enumerate(PLEDGES):     # for the directory and education screens
            self.user(50 + i, f, l, 'Pledge', pledge_class=pc, graduation_year=y, majors=[m], house=h)
        demo = self.user(99, 'Alex', 'Rivera', 'Officer', username=DEMO_USERNAME, pledge_class='Fall 2023',
                         graduation_year=2027, majors=['Political Science'], house='Gordon',
                         about_me='Chapter secretary. Ask me about minutes, the calendar or the songbook.')
        password = _password()
        demo.set_password(password)
        demo.save()
        TwoFactorRequirement.objects.update_or_create(
            user=demo, defaults={'requirement': 'exempt', 'reason': 'Demo chapter (seed_demo_chapter)'})

        for code, name, i in OFFICERS:
            role, _ = Role.objects.get_or_create(code=code, defaults={'name': name})
            members[i].roles.add(role)
            members[i].member_type = 'Officer'
            members[i].save(update_fields=['member_type'])
        sec, _ = Role.objects.get_or_create(code=DEMO_ROLE[0], defaults={'name': DEMO_ROLE[1]})
        demo.roles.add(sec)

        social, _ = Committee.objects.get_or_create(code=COMMITTEE_CODES[0], defaults={'name': 'Social Committee'})
        phil, _ = Committee.objects.get_or_create(code=COMMITTEE_CODES[1], defaults={'name': 'Philanthropy Committee'})
        social.chairs.add(members[5])
        social.members.add(members[5], members[12], members[16], demo)
        phil.chairs.add(members[6])
        phil.members.add(members[6], members[9], members[2], members[10], demo)

        # Events: the next three weeks, plus last week's chapter meeting.
        def at(days, hour, minute=0):
            d = (now + timedelta(days=days)).astimezone(timezone.get_current_timezone())
            return d.replace(hour=hour, minute=minute, second=0, microsecond=0)

        days_to_sunday = (6 - now.astimezone(timezone.get_current_timezone()).weekday()) % 7 or 7
        events = [
            ('Chapter Meeting', 'Weekly chapter meeting. Business casual.', at(days_to_sunday - 7, 19),
             'Chapter Room', {'requires_attendance': True}),
            ('Chapter Meeting', 'Weekly chapter meeting. Business casual.', at(days_to_sunday, 19),
             'Chapter Room', {'requires_attendance': True}),
            ('Chapter Meeting', 'Weekly chapter meeting. Business casual.', at(days_to_sunday + 7, 19),
             'Chapter Room', {'requires_attendance': True}),
            ('Chapter Meeting', 'Weekly chapter meeting. Business casual.', at(days_to_sunday + 14, 19),
             'Chapter Room', {'requires_attendance': True}),
            ('Study Hours', 'Quiet study in the library, second floor.', at(2, 18), 'University Library',
             {'is_study_hours': True, 'duration_minutes': 120}),
            ('Turkey Trot 5K', 'Our fall philanthropy run for the local food bank. Volunteers meet at 7:15.',
             at(5, 8), 'Campus Green', {'requires_signup': True, 'max_signups': 20}),
            ('Brotherhood Dinner', 'Family-style dinner with the alumni advisors.', at(9, 18, 30),
             'Chapter House', {}),
            ('Winter Formal', 'Theme: Winter Lodge. Tickets on sale Monday.', at(16, 19), 'Riverside Lodge', {}),
        ]
        for title, desc, when, where, extra in events:
            Event.objects.create(title=title, description=desc, date_time=when, location=where,
                                 created_by=members[0], groupme_reminded_at=now, **extra)

        announcements = [
            ('Social calendar for the rest of the semester',
             'The full social calendar is on the calendar tab: Brotherhood Dinner, the Winter Formal and a '
             'study break before finals. Formal tickets go on sale Monday; add your date’s name on the form '
             'by Friday.', members[5], 1),
            ('Turkey Trot volunteers needed',
             'We still need six volunteers for the water station at the 5K on Saturday. Sign up on the event '
             'in the calendar. Volunteers meet on the Campus Green at 7:15 a.m.', members[6], 2),
            ('Food bank drive total',
             'Thank you to everyone who helped on Saturday. Together we raised $2,140 and 600 pounds of food '
             'for the community food bank.', members[6], 4),
            ('Minutes from chapter are posted',
             'Last Sunday’s minutes are posted under Documents. Please read them before the next meeting.',
             demo, 6),
        ]
        for title, body, who, days_ago in announcements:
            a = Announcement.objects.create(title=title, content=body, posted_by=who)
            Announcement.objects.filter(pk=a.pk).update(posted_at=now - timedelta(days=days_ago, hours=3))

        # Chat
        chapter = ChatChannel.objects.create(name='Chapter', description='The whole chapter.',
                                             channel_type='custom', access_type='open', created_by=members[0])
        social_ch = ChatChannel.objects.create(name='Social Committee', channel_type='committee',
                                               access_type='committee', committee=social, created_by=members[5])
        phil_ch = ChatChannel.objects.create(name='Philanthropy', channel_type='committee',
                                             access_type='committee', committee=phil, created_by=members[6])
        channels = [chapter, social_ch, phil_ch]
        for i, (who, text, ch) in enumerate(CHAT):
            m = ChatMessage.objects.create(channel=channels[ch], committee=channels[ch].committee,
                                           sender=members[who], message=text)
            ChatMessage.objects.filter(pk=m.pk).update(
                created_at=now - timedelta(hours=(len(CHAT) - i) * 2))

        # Songbook
        cats = {}
        for order, name in enumerate(SONG_CATEGORIES):
            cats[name] = SongCategory.objects.create(name=name, display_order=order)
        for title, cat, lyrics in SONGS:
            Song.objects.create(title=title, category=cats[cat], lyrics=lyrics, created_by=members[8])

        # Votes: one open now, one passed last week.
        Legislation.objects.create(
            title='Fund new chapter room furniture',
            description='Spend up to $1,800 from the house improvement fund on six tables and thirty '
                        'stackable chairs for the chapter room, as quoted by the Treasurer.',
            posted_by=members[3], status='active', available_at=now - timedelta(hours=2),
            voting_starts_at=now - timedelta(hours=2), voting_ends_at=now + timedelta(days=2),
            required_percentage='60', allow_abstain=True)
        Legislation.objects.create(
            title='Adopt the spring service hours requirement',
            description='Each member completes ten service hours each semester, logged in Parliament.',
            posted_by=members[1], status='passed', passed=True, voting_closed=True,
            available_at=now - timedelta(days=9), voting_starts_at=now - timedelta(days=9),
            voting_ends_at=now - timedelta(days=7), voting_ended_at=now - timedelta(days=7),
            required_percentage='60', historical_yes_votes=19, historical_no_votes=3,
            historical_abstain_votes=1)

        for who, text, context in [
            (2, 'I didn’t lose the keys, I just don’t know where they are.', 'Storage closet, again'),
            (8, 'That’s not a wrong note, it’s jazz.', 'Serenade practice'),
            (11, 'Five minutes means five minutes in chapter time.', 'Before every meeting'),
        ]:
            Quote.objects.create(quoted_member=members[who], text=text, context=context, submitted_by=demo)

        return password
