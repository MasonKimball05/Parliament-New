"""
v3.51.2 — resolution title length and type are checked before saving.

`create_resolution` and `edit_resolution` wrote `title` and `resolution_type`
straight from the POST with no check. `title` is a CharField(300) and
`resolution_type` a CharField(20) with three choices, so on PostgreSQL a
301-character title (the input has no maxlength) or a long type raised
DataError — a 500 — and a short unknown type was saved as-is. SQLite does not
enforce CharField lengths, which is why the suite never saw it.

Until v3.51.0 only the C&B Chair could create; now every brother can, so this
is reachable by any member.

Run with: python manage.py test src.tests.legislation.test_resolution_field_limits
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, Resolution


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


TITLE_MAX = Resolution._meta.get_field('title').max_length


class CreateLimitsTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('RFL-1'))

    def test_overlong_title_is_refused_not_a_500(self):
        r = self.client.post(reverse('cnb_create_resolution'), {'title': 'x' * (TITLE_MAX + 1)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'{TITLE_MAX} characters')
        self.assertFalse(Resolution.objects.exists())

    def test_title_at_the_limit_is_saved(self):
        self.client.post(reverse('cnb_create_resolution'), {'title': 'x' * TITLE_MAX})
        self.assertEqual(Resolution.objects.get().title, 'x' * TITLE_MAX)

    def test_unknown_type_is_refused(self):
        for bad in ('bogus', 'z' * 40):
            r = self.client.post(reverse('cnb_create_resolution'), {'title': f'T {bad[:5]}', 'resolution_type': bad})
            self.assertEqual(r.status_code, 200)
        self.assertFalse(Resolution.objects.exists())

    def test_every_real_type_is_accepted(self):
        for value, _label in Resolution.TYPE_CHOICES:
            self.client.post(reverse('cnb_create_resolution'), {'title': f'T {value}', 'resolution_type': value})
            self.assertEqual(Resolution.objects.get(title=f'T {value}').resolution_type, value)

    def test_input_carries_maxlength(self):
        r = self.client.get(reverse('cnb_create_resolution'))
        self.assertContains(r, f'maxlength="{TITLE_MAX}"')


class EditLimitsTests(TestCase):
    def setUp(self):
        self.author = make_user('RFL-A')
        self.res = Resolution.objects.create(title='Original', created_by=self.author)
        self.client.force_login(self.author)

    def test_overlong_title_is_not_written(self):
        r = self.client.post(reverse('cnb_edit_resolution', args=[self.res.pk]),
                             {'changed_fields': 'title', 'title': 'y' * (TITLE_MAX + 1)})
        self.assertIn(r.status_code, (200, 302))
        self.res.refresh_from_db()
        self.assertEqual(self.res.title, 'Original')

    def test_unknown_type_is_not_written(self):
        self.client.post(reverse('cnb_edit_resolution', args=[self.res.pk]),
                         {'changed_fields': 'resolution_type', 'resolution_type': 'z' * 40})
        self.res.refresh_from_db()
        self.assertEqual(self.res.resolution_type, 'amendment')

    def test_other_fields_still_save_alongside_a_bad_title(self):
        self.client.post(reverse('cnb_edit_resolution', args=[self.res.pk]),
                         {'changed_fields': 'title,authors', 'title': 'y' * (TITLE_MAX + 1), 'authors': 'Brother X'})
        self.res.refresh_from_db()
        self.assertEqual(self.res.title, 'Original')
        self.assertEqual(self.res.authors, 'Brother X')
