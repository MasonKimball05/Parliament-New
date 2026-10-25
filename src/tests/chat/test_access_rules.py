"""
One rule set for who can read / write a chat channel.

`has_access`, `can_read`, `can_write` and the batched `access_map` used to
disagree: `can_read` (the WebSocket consumer and the committee-chat views)
ignored member-type / chairs-only / officers-only / alumni-only grants and let
every admin and every committee member in regardless of access type, while
`has_access`'s uncached role-grant branches skipped the `expires_at` check that
its cached twin (the chat index) applied. These tests pin one answer per case.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from src.models import ChatChannel, ChatChannelPermission, Committee, ParliamentUser


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid,
                  member_type='Member', member_status='Active')
    fields.update(extra)
    return ParliamentUser.objects.create(**fields)


PAST = object()    # sentinel: expired grant
FUTURE = object()  # sentinel: grant expiring later


class ChatAccessRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {
            'plain': make_user('ar-plain'),
            'admin': make_user('ar-admin', is_admin=True),
            'officer': make_user('ar-officer', member_type='Officer'),
            'pledge': make_user('ar-pledge', member_type='Pledge'),
            'alumni': make_user('ar-alumni', member_status='Alumni'),
            'chair': make_user('ar-chair'),
            'cmember': make_user('ar-cmember'),
            'cchair': make_user('ar-cchair'),
        }
        cls.committee = Committee.objects.create(name='Kai', code='ARK')
        cls.committee.members.add(cls.users['cmember'])
        cls.committee.chairs.add(cls.users['cchair'])
        other = Committee.objects.create(name='Social', code='ARS')
        other.chairs.add(cls.users['chair'])

    def channel(self, access_type, committee=False, active=True, read_only=False):
        return ChatChannel.objects.create(
            name=f'ch-{access_type}', channel_type='committee' if committee else 'custom',
            access_type=access_type, committee=self.committee if committee else None,
            is_active=active, is_read_only=read_only)

    def grant(self, channel, expires=None, **fields):
        if expires is PAST:
            fields['expires_at'] = timezone.now() - timedelta(hours=1)
        elif expires is FUTURE:
            fields['expires_at'] = timezone.now() + timedelta(hours=1)
        if 'user' in fields:
            fields['user'] = self.users[fields['user']]
        return ChatChannelPermission.objects.create(channel=channel, **fields)

    # (label, channel kwargs, list of grants, {user: expected read})
    READ_CASES = [
        ('open', dict(access_type='open'), [],
         {'plain': True, 'admin': True, 'pledge': True}),
        ('open inactive', dict(access_type='open', active=False), [],
         {'plain': False, 'admin': False}),
        ('restricted no grants', dict(access_type='restricted'), [],
         {'plain': False, 'admin': False, 'officer': False}),
        ('restricted user grant', dict(access_type='restricted'),
         [dict(user='plain')], {'plain': True, 'pledge': False, 'admin': False}),
        ('restricted user grant future', dict(access_type='restricted'),
         [dict(user='plain', expires=FUTURE)], {'plain': True}),
        ('restricted user grant expired', dict(access_type='restricted'),
         [dict(user='plain', expires=PAST)], {'plain': False}),
        ('restricted user grant read off', dict(access_type='restricted'),
         [dict(user='plain', can_read=False)], {'plain': False}),
        ('restricted member type', dict(access_type='restricted'),
         [dict(member_type='Pledge')], {'pledge': True, 'plain': False, 'admin': False}),
        ('restricted member type expired', dict(access_type='restricted'),
         [dict(member_type='Pledge', expires=PAST)], {'pledge': False}),
        ('restricted member type read off', dict(access_type='restricted'),
         [dict(member_type='Pledge', can_read=False)], {'pledge': False}),
        ('restricted chairs only', dict(access_type='restricted'),
         [dict(chairs_only=True)], {'chair': True, 'cchair': True, 'plain': False}),
        ('restricted chairs only expired', dict(access_type='restricted'),
         [dict(chairs_only=True, expires=PAST)], {'chair': False}),
        # is_officer is True for admins, so an officers-only grant admits them.
        ('restricted officers only', dict(access_type='restricted'),
         [dict(officers_only=True)], {'officer': True, 'admin': True, 'plain': False}),
        ('restricted officers only expired', dict(access_type='restricted'),
         [dict(officers_only=True, expires=PAST)], {'officer': False, 'admin': False}),
        ('restricted alumni only', dict(access_type='restricted'),
         [dict(alumni_only=True)], {'alumni': True, 'plain': False}),
        ('restricted alumni only expired', dict(access_type='restricted'),
         [dict(alumni_only=True, expires=PAST)], {'alumni': False}),
        ('restricted inactive', dict(access_type='restricted', active=False),
         [dict(user='plain'), dict(member_type='Member')], {'plain': False}),
        # Kai-shaped committee channel: members + explicit user guests only.
        ('committee', dict(access_type='committee', committee=True), [],
         {'cmember': True, 'cchair': False, 'plain': False, 'admin': False,
          'officer': False}),
        ('committee guest', dict(access_type='committee', committee=True),
         [dict(user='plain')], {'plain': True, 'pledge': False}),
        ('committee guest expired', dict(access_type='committee', committee=True),
         [dict(user='plain', expires=PAST)], {'plain': False}),
        ('committee role grants ignored', dict(access_type='committee', committee=True),
         [dict(member_type='Member'), dict(chairs_only=True), dict(officers_only=True),
          dict(alumni_only=True)],
         {'plain': False, 'chair': False, 'officer': False, 'alumni': False,
          'admin': False}),
        ('committee inactive', dict(access_type='committee', committee=True, active=False),
         [], {'cmember': False}),
        # A committee-linked channel set to restricted follows the restricted
        # rules; committee membership alone does not admit (narrower answer).
        ('restricted linked to committee', dict(access_type='restricted', committee=True),
         [], {'cmember': False}),
    ]

    def test_read_rules(self):
        for label, ch_kwargs, grants, expected in self.READ_CASES:
            channel = self.channel(**ch_kwargs)
            for g in grants:
                self.grant(channel, **dict(g))
            for who, want in expected.items():
                user = ParliamentUser.objects.get(pk=self.users[who].pk)
                with self.subTest(case=label, user=who):
                    self.assertEqual(channel.has_access(user), want, 'has_access')
                    self.assertEqual(channel.can_read(user), want, 'can_read')
                    self.assertEqual(
                        ChatChannel.access_map([channel], user)[channel.pk], want,
                        'access_map')

    def test_admin_override_only_widens_for_admins(self):
        channel = self.channel('committee', committee=True)
        self.assertTrue(channel.has_access(self.users['admin'], admin_override=True))
        self.assertFalse(channel.has_access(self.users['plain'], admin_override=True))
        inactive = self.channel('restricted', active=False)
        self.assertFalse(inactive.has_access(self.users['admin'], admin_override=True))

    # (label, channel kwargs, grants, {user: expected write})
    WRITE_CASES = [
        ('open', dict(access_type='open'), [], {'plain': True}),
        ('open read only', dict(access_type='open', read_only=True), [],
         {'plain': False, 'admin': False}),
        ('restricted user', dict(access_type='restricted'),
         [dict(user='plain')], {'plain': True, 'admin': False}),
        ('restricted user write off', dict(access_type='restricted'),
         [dict(user='plain', can_write=False)], {'plain': False}),
        ('restricted user write only', dict(access_type='restricted'),
         [dict(user='plain', can_read=False, can_write=True)], {'plain': False}),
        ('restricted user expired', dict(access_type='restricted'),
         [dict(user='plain', expires=PAST)], {'plain': False}),
        ('restricted member type', dict(access_type='restricted'),
         [dict(member_type='Pledge')], {'pledge': True, 'plain': False}),
        ('restricted member type write off', dict(access_type='restricted'),
         [dict(member_type='Pledge', can_write=False)], {'pledge': False}),
        ('restricted officers only', dict(access_type='restricted'),
         [dict(officers_only=True)], {'officer': True}),
        ('restricted alumni expired', dict(access_type='restricted'),
         [dict(alumni_only=True, expires=PAST)], {'alumni': False}),
        ('committee', dict(access_type='committee', committee=True), [],
         {'cmember': True, 'admin': False, 'plain': False}),
        ('committee guest write off', dict(access_type='committee', committee=True),
         [dict(user='plain', can_write=False)], {'plain': False}),
        ('committee read only', dict(access_type='committee', committee=True,
                                     read_only=True), [], {'cmember': False}),
        ('inactive', dict(access_type='open', active=False), [], {'plain': False}),
    ]

    def test_write_rules(self):
        for label, ch_kwargs, grants, expected in self.WRITE_CASES:
            channel = self.channel(**ch_kwargs)
            for g in grants:
                self.grant(channel, **dict(g))
            for who, want in expected.items():
                user = ParliamentUser.objects.get(pk=self.users[who].pk)
                with self.subTest(case=label, user=who):
                    self.assertEqual(channel.can_write(user), want)


class GlobalSearchUsesAccessRuleTests(TestCase):
    """Global search had its own copy of the rule (no expiry, no can_read)."""

    def setUp(self):
        from django.test import Client
        self.guest = make_user('gs-guest')
        self.chair = make_user('gs-chair')
        committee = Committee.objects.create(name='Kai', code='GSK')
        committee.chairs.add(self.chair)
        self.kai = ChatChannel.objects.create(
            name='Zebra Kai', channel_type='committee', access_type='committee',
            committee=committee)
        self.restricted = ChatChannel.objects.create(
            name='Zebra Exec', channel_type='custom', access_type='restricted')
        ChatChannelPermission.objects.create(
            channel=self.restricted, user=self.guest,
            expires_at=timezone.now() - timedelta(hours=1))
        ChatChannelPermission.objects.create(
            channel=self.kai, user=self.guest, can_read=False, can_write=True)
        self.client = Client()

    def found(self, user):
        from django.urls import reverse
        self.client.force_login(user)
        response = self.client.get(reverse('global_search'), {'q': 'Zebra'})
        self.assertEqual(response.status_code, 200)
        return set(c.pk for c in response.context['results'].get('chat_channels', []))

    def test_expired_or_unreadable_grants_do_not_surface_channels(self):
        self.assertEqual(self.found(self.guest), set())

    def test_search_matches_has_access(self):
        for user in (self.guest, self.chair):
            want = {c.pk for c in (self.kai, self.restricted) if c.has_access(user)}
            self.assertEqual(self.found(user), want)
