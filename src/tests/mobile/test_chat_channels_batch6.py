"""
Batch 6 — custom chat channels in the app (src/mobile/views_chat_channels.py):
the website's admin create / edit / delete pages through the same helpers
(src/view/chat/create_channel.py).

    python manage.py test src.tests.mobile.test_chat_channels_batch6
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import ChatChannel, ChatChannelPermission, ChatMessage, Committee
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


def perm_rows(channel):
    """A channel's permission rows, comparable across web and app."""
    return sorted(
        ChatChannelPermission.objects.filter(channel=channel).values_list(
            'user_id', 'member_type', 'chairs_only', 'officers_only', 'alumni_only',
            'can_read', 'can_write'),
        key=repr)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.admin = make_user('chan-admin', is_admin=True)
        _, self.admin_access, _ = make_device(self.admin)
        self.member = make_user('chan-member')
        _, self.member_access, _ = make_device(self.member)
        self.alum = make_user('chan-alum', member_status='Alumni')
        self.custom = ChatChannel.objects.create(
            name='Formal', description='Planning', channel_type='custom',
            access_type='restricted', created_by=self.admin)
        ChatChannelPermission.objects.create(channel=self.custom, user=self.member)
        ChatChannelPermission.objects.create(channel=self.custom, officers_only=True)
        ChatMessage.objects.create(channel=self.custom, sender=self.admin, message='hi')
        self.committee = Committee.objects.create(name='Social', code='SOC6')
        self.committee_chat = ChatChannel.objects.create(
            name='Social', channel_type='committee', access_type='committee',
            committee=self.committee)
        self.api = Client()

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.admin_access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, body or {}, content_type='application/json',
                             **bearer(access or self.admin_access))


class PermissionTests(_Base):

    def test_non_admins_are_refused_everything(self):
        paths = ['chat/channels/manage/', 'chat/channels/new/',
                 f'chat/channels/{self.custom.pk}/settings/']
        for path in paths:
            r = self.get(path, access=self.member_access)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()['error'], 'forbidden')
        for path in paths[1:] + [f'chat/channels/{self.custom.pk}/settings/delete/']:
            r = self.post(path, {'name': 'X'}, access=self.member_access)
            self.assertEqual(r.status_code, 403, path)
        self.assertTrue(ChatChannel.objects.filter(pk=self.custom.pk, name='Formal').exists())
        self.assertEqual(ChatChannel.objects.count(), 2)

    def test_the_web_pages_refuse_the_same_people(self):
        web = Client()
        web.force_login(self.member)
        self.assertEqual(web.get(reverse('create_channel')).status_code, 403)
        self.assertEqual(web.get(reverse('edit_channel', args=[self.custom.pk])).status_code, 403)

    def test_chats_flag_off(self):
        flag('chats', False)
        r = self.get('chat/channels/manage/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'feature_disabled')

    def test_unauthenticated(self):
        self.assertEqual(self.api.get(API + 'chat/channels/manage/').status_code, 401)

    def test_committee_channels_cannot_be_edited_or_deleted(self):
        r = self.get(f'chat/channels/{self.committee_chat.pk}/settings/')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'not_custom')
        r = self.post(f'chat/channels/{self.committee_chat.pk}/settings/', {'name': 'X'})
        self.assertEqual(r.status_code, 400)
        r = self.post(f'chat/channels/{self.committee_chat.pk}/settings/delete/')
        self.assertEqual(r.status_code, 400)
        self.committee_chat.refresh_from_db()
        self.assertEqual(self.committee_chat.name, 'Social')

    def test_unknown_channel_is_404(self):
        self.assertEqual(self.get('chat/channels/99999/settings/').status_code, 404)


class ListAndFormTests(_Base):

    def test_manage_lists_custom_channels_including_inactive(self):
        ChatChannel.objects.create(name='Old', channel_type='custom', access_type='open', is_active=False)
        r = self.get('chat/channels/manage/')
        self.assertEqual(r.status_code, 200)
        names = [c['name'] for c in r.json()['channels']]
        self.assertEqual(names, ['Formal', 'Old'])   # active first
        self.assertNotIn('Social', names)

    def test_create_form_matches_the_web_page(self):
        r = self.get('chat/channels/new/').json()
        web = Client()
        web.force_login(self.admin)
        ctx = web.get(reverse('create_channel')).context
        self.assertEqual([u['user_id'] for u in r['users']], [u.user_id for u in ctx['all_users']])
        self.assertNotIn('chan-alum', [u['user_id'] for u in r['users']])
        self.assertEqual([m['value'] for m in r['member_types']], [k for k, _ in ctx['member_types']])

    def test_settings_match_the_edit_page(self):
        r = self.get(f'chat/channels/{self.custom.pk}/settings/').json()
        web = Client()
        web.force_login(self.admin)
        ctx = web.get(reverse('edit_channel', args=[self.custom.pk])).context
        self.assertEqual(r['channel']['name'], 'Formal')
        self.assertEqual(r['access']['users'], ctx['current_user_permissions'])
        self.assertEqual(r['access']['member_types'], ctx['current_member_type_permissions'])
        self.assertEqual(r['access']['officers_only'], ctx['current_officers_only'])
        self.assertEqual(r['access']['chairs_only'], ctx['current_chairs_only'])
        self.assertEqual(r['access']['alumni_only'], ctx['current_alumni_only'])
        self.assertEqual(r['message_count'], 1)


class CreateTests(_Base):
    body = {'name': 'Rush', 'description': 'Recruitment', 'icon': '🎉', 'color': '#112233',
            'access_type': 'restricted', 'users': ['chan-member'], 'member_types': ['Officer'],
            'chairs_only': True, 'officers_only': False}

    def test_creates_the_same_rows_as_the_web_page(self):
        r = self.post('chat/channels/new/', self.body)
        self.assertEqual(r.status_code, 201, r.content)
        app = ChatChannel.objects.get(pk=r.json()['channel']['id'])

        web = Client()
        web.force_login(self.admin)
        web.post(reverse('create_channel'), {
            'name': 'Rush web', 'description': 'Recruitment', 'icon': '🎉', 'color': '#112233',
            'access_type': 'restricted', 'users': ['chan-member'], 'member_types': ['Officer'],
            'chairs_only': 'true'})
        site = ChatChannel.objects.get(name='Rush web')

        for field in ('description', 'icon', 'color', 'access_type', 'channel_type', 'is_active',
                      'is_read_only', 'created_by_id'):
            self.assertEqual(getattr(app, field), getattr(site, field), field)
        self.assertEqual(perm_rows(app), perm_rows(site))
        self.assertEqual(len(perm_rows(app)), 3)
        # And the access rule admits who the grants say.
        self.assertTrue(app.has_access(self.member))

    def test_open_channel_gets_no_rows(self):
        r = self.post('chat/channels/new/', {**self.body, 'access_type': 'open'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(perm_rows(ChatChannel.objects.get(name='Rush')), [])

    def test_defaults(self):
        r = self.post('chat/channels/new/', {'name': 'Plain'})
        self.assertEqual(r.status_code, 201)
        c = ChatChannel.objects.get(name='Plain')
        self.assertEqual((c.icon, c.color, c.access_type), ('💬', '#003DA5', 'restricted'))

    def test_validation(self):
        bad = [
            {'name': ''}, {'name': '   '}, {'name': 'x' * 201},
            {'name': 'A', 'access_type': 'committee'},
            {'name': 'A', 'color': 'blue'},
            {'name': 'A', 'icon': 'x' * 11},
            {'name': 'A', 'member_types': ['Wizard']},
            {'name': 'A', 'users': ['nobody']},
            {'name': 'A', 'users': ['chan-alum']},   # not offered: not active
        ]
        for body in bad:
            r = self.post('chat/channels/new/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input', body)
        self.assertEqual(ChatChannel.objects.count(), 2)


class EditTests(_Base):

    def test_saves_like_the_web_page(self):
        body = {'name': 'Formal 2', 'description': '', 'icon': '🎩', 'color': '#abcdef',
                'access_type': 'restricted', 'users': [], 'member_types': ['Advisor'],
                'chairs_only': False, 'officers_only': False, 'alumni_only': True,
                'is_active': True, 'is_read_only': True}
        r = self.post(f'chat/channels/{self.custom.pk}/settings/', body)
        self.assertEqual(r.status_code, 200, r.content)
        self.custom.refresh_from_db()
        app_rows = perm_rows(self.custom)
        self.assertEqual((self.custom.name, self.custom.icon, self.custom.is_read_only),
                         ('Formal 2', '🎩', True))

        other = ChatChannel.objects.create(name='Twin', channel_type='custom', created_by=self.admin)
        web = Client()
        web.force_login(self.admin)
        web.post(reverse('edit_channel', args=[other.pk]), {
            'name': 'Formal 2', 'description': '', 'icon': '🎩', 'color': '#abcdef',
            'access_type': 'restricted', 'member_types': ['Advisor'], 'alumni_only': 'true',
            'is_active': 'true', 'is_read_only': 'true'})
        self.assertEqual(app_rows, perm_rows(other))
        # The old grants are gone: the member no longer gets in, alumni do.
        self.assertFalse(self.custom.has_access(self.member))
        self.assertTrue(self.custom.has_access(self.alum))

    def test_omitted_fields_keep_their_values(self):
        r = self.post(f'chat/channels/{self.custom.pk}/settings/', {'description': 'New'})
        self.assertEqual(r.status_code, 200, r.content)
        self.custom.refresh_from_db()
        self.assertEqual((self.custom.name, self.custom.description, self.custom.is_active),
                         ('Formal', 'New', True))
        self.assertEqual(len(perm_rows(self.custom)), 2)
        self.assertTrue(self.custom.has_access(self.member))

    def test_deactivating_hides_it_from_chat_but_not_manage(self):
        self.post(f'chat/channels/{self.custom.pk}/settings/', {'is_active': False})
        self.custom.refresh_from_db()
        self.assertFalse(self.custom.is_active)
        names = [c['name'] for c in self.get('chat/channels/', access=self.member_access).json()['channels']]
        self.assertNotIn('Formal', names)
        self.assertIn('Formal', [c['name'] for c in self.get('chat/channels/manage/').json()['channels']])

    def test_validation(self):
        r = self.post(f'chat/channels/{self.custom.pk}/settings/', {'name': ''})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        self.custom.refresh_from_db()
        self.assertEqual(self.custom.name, 'Formal')


class DeleteTests(_Base):

    def test_deletes_channel_and_messages(self):
        r = self.post(f'chat/channels/{self.custom.pk}/settings/delete/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['detail'], 'Channel "Formal" deleted successfully!')
        self.assertFalse(ChatChannel.objects.filter(pk=self.custom.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(channel_id=self.custom.pk).exists())
        self.assertFalse(ChatChannelPermission.objects.filter(channel_id=self.custom.pk).exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.get(f'chat/channels/{self.custom.pk}/settings/delete/').status_code, 405)
        self.assertTrue(ChatChannel.objects.filter(pk=self.custom.pk).exists())
