"""
The three form builders in the app (11-06-26, src/mobile/views_form_builder.py)
and the shared field editing the website's pages now call
(src/view/form_field_builder.py).

These check each builder's permissions (the web decorators' rules), the
checks the page's form makes in the browser, what is saved per field type,
reorder, remove and restore with their built-in rules, slating's default
fields and warning, and that the three web pages still behave as before.

    python manage.py test src.tests.mobile.test_form_builder
"""
import json

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    Committee, KaiFormField, ParliamentUser, Role, ServiceFormField, SlatingActivity, SlatingFormField,
    SlatingPeriod,
)
from src.tests.mobile.sess_helpers import bearer, make_device

API = '/api/mobile/v1/'
PW = 'form-builder-pw-1!'


def member(uid, **kw):
    u = ParliamentUser.objects.create(user_id=uid, username=uid, name=f'Member {uid}',
                                      member_type=kw.pop('member_type', 'Member'), member_status='Active', **kw)
    u.set_password(PW)
    u.save()
    return u


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.tokens = {}
        self.admin = member('fb-admin', is_admin=True)
        self.plain = member('fb-plain')

    def call(self, method, path, body=None, user=None):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        kw = bearer(self.tokens[user.pk])
        if body is None:
            return getattr(self.api, method)(API + path, **kw)
        return getattr(self.api, method)(API + path, json.dumps(body), content_type='application/json', **kw)

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c

    def assertRefused(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get('error')), (status, code), r.content)


# ── Kai ─────────────────────────────────────────────────────────────────────

class _KaiBase(_Base):

    def setUp(self):
        super().setUp()
        self.chair = member('fb-kai-chair', member_type='Officer')
        self.committee = Committee.objects.create(name='Kai', code='KAIFB', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.title = KaiFormField.objects.create(field_name='title', label='Title', field_type='text',
                                                 is_builtin=True, is_required=True, display_order=0)
        self.notes = KaiFormField.objects.create(field_name='notes', label='Notes', field_type='textarea',
                                                 display_order=1)
        self.praise = KaiFormField.objects.create(field_name='praise', label='Praise', field_type='text',
                                                  form_type='commendation', display_order=0)
        self.gone = KaiFormField.objects.create(field_name='gone', label='Gone', field_type='text',
                                                is_active=False, display_order=2)
        self.base = 'kai/form-builder/discipline/'


class KaiPermissionTests(_KaiBase):

    def test_chairs_and_site_admins_only(self):
        self.assertEqual(self.call('get', self.base, user=self.chair).status_code, 200)
        self.assertEqual(self.call('get', self.base, user=self.admin).status_code, 200)
        self.assertRefused(self.call('get', self.base, user=self.plain), 403, 'forbidden')
        r = self.call('post', self.base, {'field_type': 'text', 'label': 'X', 'field_name': 'x'}, user=self.plain)
        self.assertRefused(r, 403, 'forbidden')
        self.assertFalse(KaiFormField.objects.filter(field_name='x').exists())
        r = self.call('post', f'{self.base}fields/{self.notes.id}/remove/', {}, user=self.plain)
        self.assertRefused(r, 403, 'forbidden')

    def test_without_a_kai_committee_only_a_site_admin(self):
        self.committee.delete()
        self.assertEqual(self.call('get', self.base, user=self.admin).status_code, 200)
        self.assertRefused(self.call('get', self.base, user=self.chair), 403, 'forbidden')

    def test_unknown_form_is_not_found(self):
        self.assertEqual(self.call('get', 'kai/form-builder/other/', user=self.chair).status_code, 404)


class KaiBuilderTests(_KaiBase):

    def test_lists_one_form_in_the_pages_order(self):
        d = self.call('get', self.base, user=self.chair).json()
        self.assertEqual([f['field_name'] for f in d['fields']], ['title', 'notes'])
        self.assertEqual([f['field_name'] for f in d['inactive']], ['gone'])
        title = d['fields'][0]
        self.assertTrue(title['is_builtin'] and title['label_locked'])
        self.assertFalse(title['can_remove'])
        self.assertTrue(d['fields'][1]['can_remove'])
        self.assertEqual(d['option_types'], ['select', 'multiselect', 'radio'])
        self.assertIn('member_select', [t['value'] for t in d['field_types']])
        self.assertTrue(d['features']['placeholder'])
        self.assertEqual(d['web'], reverse('kai_form_builder'))
        c = self.call('get', 'kai/form-builder/commendation/', user=self.chair).json()
        self.assertEqual([f['field_name'] for f in c['fields']], ['praise'])
        self.assertEqual(c['web'], reverse('kai_commendation_form_builder'))

    def test_add_a_choice_field(self):
        r = self.call('post', 'kai/form-builder/commendation/', {
            'field_type': 'select', 'label': 'Kind', 'field_name': 'Kind Of', 'options': [' Big ', '', 'Small one'],
            'is_required': True, 'help_text': 'Pick one', 'section': 'About', 'placeholder': 'Choose',
        }, user=self.chair)
        self.assertEqual(r.status_code, 201, r.content)
        f = KaiFormField.objects.get(field_name='kind_of')
        self.assertEqual((f.form_type, f.display_order, f.created_by, f.is_builtin), ('commendation', 1, self.chair,
                                                                                     False))
        self.assertEqual(f.options, [{'value': 'big', 'label': 'Big'}, {'value': 'small_one', 'label': 'Small one'}])
        self.assertTrue(f.is_required)
        self.assertEqual((f.help_text, f.section, f.placeholder), ('Pick one', 'About', 'Choose'))
        self.assertEqual(r.json()['field']['options'], ['Big', 'Small one'])
        self.assertEqual(r.json()['message'], 'Field "Kind" added successfully.')

    def test_add_checks_what_the_browser_does(self):
        def add(**kw):
            body = {'field_type': 'text', 'label': 'Thing', 'field_name': 'thing'}
            body.update(kw)
            return self.call('post', self.base, body, user=self.chair)
        self.assertRefused(add(label='  '), 400, 'invalid_input')
        self.assertRefused(add(field_name=''), 400, 'invalid_input')
        self.assertRefused(add(field_name='bad-name!'), 400, 'invalid_input')
        self.assertRefused(add(field_type='gpa'), 400, 'invalid_input')            # not on Kai's menu
        self.assertRefused(add(field_type='radio', options=[]), 400, 'invalid_input')
        self.assertRefused(add(label='x' * 201), 400, 'invalid_input')
        self.assertRefused(add(is_required='yes'), 400, 'invalid_input')
        self.assertRefused(add(field_type='select', options=[1, 2]), 400, 'invalid_input')
        # Names are unique across both Kai forms.
        r = add(field_name='praise')
        self.assertRefused(r, 400, 'duplicate')
        self.assertIn('already exists', r.json()['detail'])
        self.assertEqual(KaiFormField.objects.filter(field_name='thing').count(), 0)
        self.assertEqual(add(field_name='thing_2').status_code, 201)

    def test_edit_keeps_name_and_type_and_a_builtin_label(self):
        r = self.call('post', f'{self.base}fields/{self.title.id}/', {
            'label': 'Renamed', 'field_type': 'select', 'help_text': 'Short', 'is_required': False,
        }, user=self.chair)
        self.assertEqual(r.status_code, 200, r.content)
        self.title.refresh_from_db()
        self.assertEqual((self.title.label, self.title.field_type, self.title.help_text, self.title.is_required),
                         ('Title', 'text', 'Short', False))
        self.call('post', f'{self.base}fields/{self.notes.id}/', {'label': 'More notes'}, user=self.chair)
        self.notes.refresh_from_db()
        self.assertEqual(self.notes.label, 'More notes')
        # A field of the other form isn't this form's.
        r = self.call('post', f'{self.base}fields/{self.praise.id}/', {'label': 'X'}, user=self.chair)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.call('get', f'{self.base}fields/{self.notes.id}/', user=self.chair).json()['field'][
            'label'], 'More notes')

    def test_edit_a_file_field_keeps_its_settings(self):
        f = KaiFormField.objects.create(field_name='proof', label='Proof', field_type='file',
                                        allowed_file_types=['application/pdf'], max_file_size_mb=25)
        self.call('post', f'{self.base}fields/{f.id}/', {'label': 'Evidence'}, user=self.chair)
        f.refresh_from_db()
        self.assertEqual((f.label, f.allowed_file_types, f.max_file_size_mb), ('Evidence', ['application/pdf'], 25))

    def test_remove_and_restore(self):
        r = self.call('post', f'{self.base}fields/{self.title.id}/remove/', {}, user=self.chair)
        self.assertRefused(r, 400, 'builtin')
        self.assertIn('built-in field and cannot be removed', r.json()['detail'])
        r = self.call('post', f'{self.base}fields/{self.notes.id}/remove/', {}, user=self.chair)
        self.assertEqual(r.json()['message'], 'Field "Notes" removed.')
        self.notes.refresh_from_db()
        self.assertFalse(self.notes.is_active)
        self.assertIn('notes', [f['field_name'] for f in r.json()['inactive']])
        r = self.call('post', f'{self.base}fields/{self.notes.id}/toggle/', {}, user=self.chair)
        self.assertEqual(r.json()['message'], 'Field "Notes" activated.')
        self.notes.refresh_from_db()
        self.assertTrue(self.notes.is_active)
        # A built-in field only ever comes back on.
        r = self.call('post', f'{self.base}fields/{self.title.id}/toggle/', {}, user=self.chair)
        self.assertRefused(r, 400, 'builtin')
        self.title.refresh_from_db()
        self.assertTrue(self.title.is_active)

    def test_reorder_stays_inside_the_form(self):
        r = self.call('post', f'{self.base}reorder/', {'order': [self.notes.id, self.title.id]}, user=self.chair)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([f['field_name'] for f in r.json()['fields']], ['notes', 'title'])
        for bad in ([self.notes.id, self.praise.id], [self.notes.id, self.notes.id], 'x', ['1'], [True]):
            r = self.call('post', f'{self.base}reorder/', {'order': bad}, user=self.chair)
            self.assertRefused(r, 400, 'invalid_input')
        self.praise.refresh_from_db()
        self.assertEqual(self.praise.display_order, 0)


class KaiWebStillWorksTests(_KaiBase):

    def test_the_page_and_its_reorder(self):
        web = self.web(self.chair)
        self.assertContains(web.get(reverse('kai_form_builder')), 'Notes')
        r = web.post(reverse('kai_form_builder'), {'action': 'delete_field', 'field_id': self.title.id}, follow=True)
        self.assertIn('cannot be removed', ' '.join(str(m) for m in get_messages(r.wsgi_request)))
        web.post(reverse('kai_form_builder'), {'action': 'update_field', 'field_id': self.notes.id,
                                                'label': 'Edited', 'is_required': 'on'})
        self.notes.refresh_from_db()
        self.assertEqual((self.notes.label, self.notes.is_required), ('Edited', True))
        r = web.post(reverse('kai_api_reorder_fields'), {'order': json.dumps([self.notes.id, self.title.id])})
        self.assertEqual(r.json()['status'], 'success')
        self.notes.refresh_from_db()
        self.assertEqual(self.notes.display_order, 0)
        self.assertEqual(self.web(self.plain).post(reverse('kai_api_reorder_fields'), {'order': '[]'}).status_code,
                         403)


# ── Service hours ───────────────────────────────────────────────────────────

class _ServiceBase(_Base):

    def setUp(self):
        super().setUp()
        self.vpp = member('fb-vpp', member_type='Officer')
        self.vpp.roles.add(Role.objects.create(name='VP Programming', code='VPP'))
        self.base = 'officer/service-hours/form-builder/'


class ServiceBuilderTests(_ServiceBase):

    def test_vpp_and_admins_only(self):
        self.assertRefused(self.call('get', self.base, user=self.plain), 403, 'forbidden')
        self.assertEqual(self.call('get', self.base, user=self.admin).status_code, 200)

    def test_seeds_the_built_in_fields_as_the_page_does(self):
        d = self.call('get', self.base, user=self.vpp).json()
        self.assertEqual([f['field_name'] for f in d['fields']],
                         ['period', 'hours', 'service_date', 'organization', 'description', 'attachment'])
        self.assertTrue(all(f['is_builtin'] and not f['can_remove'] and not f['label_locked'] for f in d['fields']))
        self.assertEqual(d['option_types'], ['select', 'multiselect', 'radio', 'checkbox'])

    def test_add_edit_remove(self):
        self.call('get', self.base, user=self.vpp)
        r = self.call('post', self.base, {'field_type': 'checkbox', 'label': 'Kinds', 'field_name': 'kinds',
                                          'options': ['Indoor', 'Outdoor']}, user=self.vpp)
        self.assertEqual(r.status_code, 201, r.content)
        f = ServiceFormField.objects.get(field_name='kinds')
        self.assertEqual((f.options, f.display_order, f.created_by), (['Indoor', 'Outdoor'], 6, self.vpp))
        # The built-in label can change here (only Kai locks it).
        hours = ServiceFormField.objects.get(field_name='hours')
        self.call('post', f'{self.base}fields/{hours.id}/', {'label': 'Hours served', 'is_required': True},
                  user=self.vpp)
        hours.refresh_from_db()
        self.assertEqual(hours.label, 'Hours served')
        r = self.call('post', f'{self.base}fields/{hours.id}/remove/', {}, user=self.vpp)
        self.assertRefused(r, 400, 'builtin')
        self.assertEqual(r.json()['detail'], 'Built-in fields cannot be deleted.')
        r = self.call('post', f'{self.base}fields/{f.id}/remove/', {}, user=self.vpp)
        self.assertEqual([x['field_name'] for x in r.json()['inactive']], ['kinds'])
        r = self.call('post', f'{self.base}reorder/', {'order': [hours.id]}, user=self.vpp)
        self.assertEqual(r.status_code, 200)

    def test_web_page_still_works(self):
        web = self.web(self.vpp)
        self.assertEqual(web.get(reverse('service_form_builder')).status_code, 200)
        web.post(reverse('service_form_builder'), {'action': 'add_field', 'field_name': 'Who Led', 'label': 'Leader',
                                                    'field_type': 'select', 'options': 'A\nB'})
        f = ServiceFormField.objects.get(field_name='who_led')
        self.assertEqual(f.options, ['A', 'B'])
        r = web.post(reverse('service_form_builder'),
                     {'action': 'delete_field', 'field_id': ServiceFormField.objects.get(field_name='hours').id},
                     follow=True)
        self.assertIn('Built-in fields cannot be deleted.', [str(m) for m in get_messages(r.wsgi_request)])
        web.post(reverse('service_form_builder'), {'action': 'delete_field', 'field_id': f.id})
        web.post(reverse('service_form_builder'), {'action': 'toggle_field', 'field_id': f.id})
        f.refresh_from_db()
        self.assertTrue(f.is_active)


# ── Slating ─────────────────────────────────────────────────────────────────

class _SlatingBase(_Base):

    def setUp(self):
        super().setUp()
        self.manager = member('fb-mgr')
        self.committee = Committee.objects.create(name='Slating', code='SLATEFB', is_active=True)
        # Locked: a manager is set, so a site admin alone may not.
        self.period = SlatingPeriod.objects.create(name='Fall', status='setup', slating_manager=self.manager,
                                                   slating_committee=self.committee)
        self.open = SlatingPeriod.objects.create(name='Spring', status='setup',
                                                 slating_committee=Committee.objects.create(
                                                     name='Open', code='SLOPENFB', is_active=True))
        self.why = SlatingFormField.objects.create(period=self.period, field_name='why', label='Why?',
                                                   field_type='textarea', display_order=0)
        self.cv = SlatingFormField.objects.create(period=self.period, field_name='cv', label='CV',
                                                  field_type='file', display_order=1,
                                                  allowed_file_types=['application/pdf'], max_file_size_mb=5,
                                                  is_confidential=True)
        self.base = f'slating/periods/{self.period.id}/form-builder/'


class SlatingBuilderTests(_SlatingBase):

    def test_the_periods_managers_only(self):
        self.assertEqual(self.call('get', self.base, user=self.manager).status_code, 200)
        self.assertRefused(self.call('get', self.base, user=self.plain), 403, 'forbidden')
        self.assertRefused(self.call('get', self.base, user=self.admin), 403, 'forbidden')   # locked
        r = self.call('get', f'slating/periods/{self.open.id}/form-builder/', user=self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call('get', 'slating/periods/99999/form-builder/', user=self.admin).status_code, 404)

    def test_the_page_and_its_warning(self):
        d = self.call('get', self.base, user=self.manager).json()
        self.assertEqual([f['field_name'] for f in d['fields']], ['why', 'cv'])
        self.assertIsNone(d['warning'])
        self.assertEqual(d['features'], {'placeholder': False, 'show_in_review': True, 'defaults': True})
        types = [t['value'] for t in d['field_types']]
        self.assertIn('position_preference', types)
        self.assertNotIn('phone', types)
        self.assertEqual(d['period']['name'], 'Fall')
        self.period.status = 'nominations_open'
        self.period.save()
        d = self.call('get', self.base, user=self.manager).json()
        self.assertIn('fully edited during setup', d['warning'])

    def test_add_and_edit_log_activity(self):
        r = self.call('post', self.base, {'field_type': 'radio', 'label': 'Year', 'field_name': 'year',
                                          'options': ['Junior', 'Senior'], 'show_in_review': False,
                                          'placeholder': 'ignored'}, user=self.manager)
        self.assertEqual(r.status_code, 201, r.content)
        f = SlatingFormField.objects.get(period=self.period, field_name='year')
        self.assertEqual((f.options, f.show_in_review, f.display_order, f.placeholder),
                         (['Junior', 'Senior'], False, 2, ''))
        self.assertTrue(SlatingActivity.objects.filter(period=self.period, action='form_field_added').exists())
        self.call('post', f'{self.base}fields/{self.cv.id}/', {'label': 'Résumé', 'show_in_review': True},
                  user=self.manager)
        self.cv.refresh_from_db()
        self.assertEqual((self.cv.label, self.cv.allowed_file_types, self.cv.max_file_size_mb,
                          self.cv.is_confidential), ('Résumé', ['application/pdf'], 5, True))
        self.assertTrue(SlatingActivity.objects.filter(period=self.period, action='form_field_modified').exists())
        # Slating's names: letters and underscores only; per period, so another period may reuse one.
        r = self.call('post', self.base, {'field_type': 'text', 'label': 'Two', 'field_name': 'q2'},
                      user=self.manager)
        self.assertRefused(r, 400, 'invalid_input')
        r = self.call('post', self.base, {'field_type': 'phone', 'label': 'Phone', 'field_name': 'phone'},
                      user=self.manager)
        self.assertRefused(r, 400, 'invalid_input')
        self.assertRefused(self.call('post', self.base, {'field_type': 'text', 'label': 'W', 'field_name': 'why'},
                                     user=self.manager), 400, 'duplicate')
        r = self.call('post', f'slating/periods/{self.open.id}/form-builder/',
                      {'field_type': 'text', 'label': 'W', 'field_name': 'why'}, user=self.admin)
        self.assertEqual(r.status_code, 201)

    def test_remove_restore_reorder_and_another_periods_field(self):
        r = self.call('post', f'{self.base}fields/{self.why.id}/remove/', {}, user=self.manager)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([f['field_name'] for f in r.json()['inactive']], ['why'])
        self.assertRefused(self.call('post', f'{self.base}fields/{self.why.id}/remove/', {}, user=self.manager),
                           400, 'invalid_input')
        self.call('post', f'{self.base}fields/{self.why.id}/toggle/', {}, user=self.manager)
        r = self.call('post', f'{self.base}reorder/', {'order': [self.cv.id, self.why.id]}, user=self.manager)
        self.assertEqual([f['field_name'] for f in r.json()['fields']], ['cv', 'why'])
        other = SlatingFormField.objects.create(period=self.open, field_name='x', label='X', field_type='text')
        self.assertEqual(self.call('post', f'{self.base}fields/{other.id}/remove/', {},
                                   user=self.manager).status_code, 404)
        self.assertRefused(self.call('post', f'{self.base}reorder/', {'order': [other.id]}, user=self.manager),
                           400, 'invalid_input')

    def test_default_fields(self):
        path = f'slating/periods/{self.open.id}/form-builder/defaults/'
        r = self.call('post', path, {}, user=self.admin)
        self.assertEqual((r.json()['level'], r.json()['message']), ('success', 'Added 8 default fields to the form.'))
        self.assertEqual(len(r.json()['fields']), 8)
        r = self.call('post', path, {}, user=self.admin)
        self.assertEqual(r.json()['level'], 'warning')
        self.assertEqual(SlatingFormField.objects.filter(period=self.open).count(), 8)


class SlatingWebStillWorksTests(_SlatingBase):

    def test_page_actions_and_its_api(self):
        web = self.web(self.manager)
        url = reverse('slating_form_builder', args=[self.period.id])
        self.assertEqual(web.get(url).status_code, 200)
        web.post(url, {'action': 'add_field', 'field_name': 'goal', 'label': 'Goal', 'field_type': 'select',
                       'options': 'A\nB', 'show_in_review': 'on'})
        goal = SlatingFormField.objects.get(period=self.period, field_name='goal')
        self.assertEqual((goal.options, goal.display_order, goal.show_in_review), (['A', 'B'], 2, True))
        web.post(url, {'action': 'update_field', 'field_id': goal.id, 'label': 'Goals', 'options': 'C'})
        goal.refresh_from_db()
        # No show_in_review in the POST reads as on, as it always has.
        self.assertEqual((goal.label, goal.options, goal.show_in_review), ('Goals', ['C'], True))
        web.post(url, {'action': 'delete_field', 'field_id': goal.id})
        goal.refresh_from_db()
        self.assertFalse(goal.is_active)
        web.post(url, {'action': 'toggle_field', 'field_id': goal.id})
        goal.refresh_from_db()
        self.assertTrue(goal.is_active)
        r = web.post(reverse('slating_api_toggle_field', args=[self.period.id, goal.id]))
        self.assertEqual(r.json(), {'status': 'success', 'is_active': False})
        r = web.post(reverse('slating_api_reorder_fields', args=[self.period.id]),
                     json.dumps({'field_order': [self.cv.id, self.why.id]}), content_type='application/json')
        self.assertEqual(r.json()['status'], 'success')
        self.cv.refresh_from_db()
        self.assertEqual(self.cv.display_order, 0)
        r = web.post(url, {'action': 'add_default_fields'}, follow=True)
        self.assertIn('already has fields', ' '.join(str(m) for m in get_messages(r.wsgi_request)))


class WebEditKeepsHiddenSettingsTests(_SlatingBase):
    """11-06-26: the website's edit form has no "confidential" or size inputs; an edit keeps them."""

    def test_confidential_and_size_survive_a_web_edit(self):
        f = SlatingFormField.objects.create(period=self.period, field_name='transcript', label='Transcript',
                                            field_type='file', is_confidential=True, max_file_size_mb=25,
                                            display_order=9)
        web = self.web(self.manager)
        web.post(reverse('slating_form_builder', args=[self.period.id]),
                 {'action': 'update_field', 'field_id': f.id, 'label': 'Transcript (PDF)'})
        f.refresh_from_db()
        self.assertEqual((f.label, f.is_confidential, f.max_file_size_mb), ('Transcript (PDF)', True, 25))
