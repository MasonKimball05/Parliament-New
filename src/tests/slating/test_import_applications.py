"""
v4.0.0 — Import slating applications from an outside form's CSV.

The President collected applications in a Google Form before using Parliament;
those members must not have to apply again. What must hold: only the period's
managers can import; nothing is written before the manager confirms; members
who already applied in Parliament are never touched; each member gets one
application (their latest response); answers land in the right form fields;
nothing is lost (unmapped answers go to review notes); and the Parliament
form's questions the outside form didn't ask are recorded as "not asked".

    python manage.py test src.tests.slating.test_import_applications
"""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from src import slating_import as si
from src.models import (
    ParliamentUser, SlatingApplication, SlatingFormField, SlatingPeriod, SlatingPosition,
)

GOOGLE_CSV = (
    'Timestamp,Email Address,What positions are you running for?,Second choice position,'
    'GPA,Why do you want to serve?,Shirt size\n'
    '10/20/2026 14:05:11,alpha@test.org,"Vice President, President",Treasurer,3.4,To help,L\n'
    '10/21/2026 09:00:00,bravo@test.org,Treasurer,,2.9,Because,M\n'
    '10/19/2026 08:00:00,nobody@test.org,President,,3.0,?,S\n'
    '10/22/2026 10:00:00,alpha@test.org,President,,3.5,Changed my mind,L\n'
    '10/22/2026 11:00:00,charlie@test.org,President,,3.9,Already applied,XL\n'
)


def member(uid, email, **kw):
    return ParliamentUser.objects.create(user_id=uid, username=uid, name=f'Member {uid}',
                                         email=email, member_type=kw.pop('member_type', 'Member'),
                                         member_status='Active', **kw)


class _Base(TestCase):

    def setUp(self):
        self.manager = member('mgr', 'mgr@test.org')
        self.period = SlatingPeriod.objects.create(name='Fall 2026', status='nominations_open',
                                                   slating_manager=self.manager)
        mk = lambda t, c, o: SlatingPosition.objects.create(period=self.period, title=t, code=c,
                                                            display_order=o)
        self.pres, self.vp, self.treas = mk('President', 'PRES', 1), mk('Vice President', 'VP', 2), \
            mk('Treasurer', 'TREAS', 3)
        self.why = SlatingFormField.objects.create(period=self.period, field_name='why',
                                                   label='Why do you want to serve?',
                                                   field_type='textarea', is_required=True)
        self.goals = SlatingFormField.objects.create(period=self.period, field_name='goals',
                                                     label='Your goals for the chapter',
                                                     field_type='textarea', display_order=2)
        self.alpha = member('alpha', 'alpha@test.org')
        self.bravo = member('bravo', 'bravo@test.org')
        self.charlie = member('charlie', 'charlie@test.org')
        self.charlies_own = SlatingApplication.objects.create(
            period=self.period, applicant=self.charlie, status='submitted',
            position_preferences={'first_choice': [self.vp.id]})
        self.url = reverse('slating_import_applications', args=[self.period.id])
        self.client.force_login(self.manager)

    def upload(self, text=GOOGLE_CSV):
        return self.client.post(self.url, {
            'step': 'upload',
            'csv_file': SimpleUploadedFile('responses.csv', text.encode(), content_type='text/csv'),
        })

    def mapping_post(self, response, **extra):
        cols = response.context['columns']
        data = {'csv_text': response.context['csv_text'], 'keep_unmapped': '1'}
        data.update({f'col_{c["index"]}': c['kind'] for c in cols})
        data.update(extra)
        return data


class GuessAndPreviewTests(_Base):

    def test_columns_are_guessed(self):
        r = self.upload()
        kinds = [c['kind'] for c in r.context['columns']]
        self.assertEqual(kinds, ['timestamp', 'applicant_email', 'pos_first', 'pos_second', 'gpa',
                                 f'field_{self.why.id}', ''])

    def test_the_page_posts_the_files_text(self):
        # v4.3.0: the upload form holds no file input (core.js resent those
        # over fetch() and dropped this step's page); the browser reads the
        # file and posts its text.
        page = self.client.get(self.url).content.decode()
        form = page[page.index('id="csv-upload-form"'):]
        form = form[:form.index('</form>')]
        self.assertNotIn('type="file"', form)
        self.assertIn('name="csv_text"', form)
        r = self.client.post(self.url, {'step': 'upload', 'csv_text': '\ufeff' + GOOGLE_CSV})
        self.assertEqual(r.context['step'], 'map')
        self.assertEqual(r.context['columns'][0]['kind'], 'timestamp')
        r = self.client.post(self.url, {'step': 'upload', 'csv_text': '  '})
        self.assertEqual(r.context['step'], 'upload')

    def test_preview_is_the_confirmation_and_writes_nothing(self):
        r = self.upload()
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        items = {i['row']: i for i in r.context['items']}
        self.assertEqual(r.context['import_count'], 2)
        self.assertEqual(items[2]['status'], 'skip')            # alpha's other form...
        self.assertIn('Combined into one application with row 5', items[2]['reason'])
        self.assertEqual(items[5]['merged_rows'], [2, 5])        # ...folded into row 5
        self.assertEqual(items[4]['reason'], 'No Parliament member matches "nobody@test.org".')
        self.assertIn('Already has an application', items[6]['reason'])
        self.assertEqual([f.label for f in r.context['not_asked']], ['Your goals for the chapter'])
        review = {q['label']: q for q in items[3]['review']}
        self.assertEqual(review['Your goals for the chapter']['state'], 'not_asked')
        self.assertEqual(review['Why do you want to serve?']['value'], 'Because')
        self.assertContains(r, 'Not asked on the outside form')
        self.assertEqual(SlatingApplication.objects.count(), 1)   # only Charlie's own

    def test_import_needs_the_confirmation_box(self):
        r = self.upload()
        r = self.client.post(self.url, self.mapping_post(r, step='import'))
        self.assertEqual(SlatingApplication.objects.count(), 1)
        self.assertContains(r, 'Tick the box')

    def test_mapping_must_name_the_applicant(self):
        r = self.upload()
        data = self.mapping_post(r, step='preview')
        data['col_1'] = ''
        r = self.client.post(self.url, data)
        self.assertContains(r, 'who each applicant is')


class ImportTests(_Base):

    def do_import(self):
        r = self.upload()
        return self.client.post(self.url, self.mapping_post(r, step='import', confirmed='1'))

    def test_creates_one_submitted_application_per_member(self):
        r = self.do_import()
        self.assertRedirects(r, reverse('slating_applications', args=[self.period.id]))
        alpha = SlatingApplication.objects.get(applicant=self.alpha)
        # Alpha's two forms (rows 2 and 5, different positions) are combined.
        self.assertEqual(alpha.position_preferences['first_choice'], [self.vp.id, self.pres.id])
        self.assertEqual(alpha.position_preferences['second_choice'], [self.treas.id])
        self.assertEqual(alpha.reported_gpa, Decimal('3.500'))     # latest GPA
        self.assertEqual(alpha.status, 'submitted')
        self.assertEqual(alpha.submitted_at.day, 20)               # first form
        self.assertIsNotNone(alpha.imported_at)
        self.assertEqual(alpha.import_not_asked, ['Your goals for the chapter'])
        why = alpha.responses.get(field=self.why).text_value
        self.assertIn('Vice President, President, Treasurer: To help', why)
        self.assertIn('President: Changed my mind', why)
        self.assertIn('Shirt size: L', alpha.review_notes)
        self.assertIn('rows 2, 5, combined', alpha.review_notes)
        bravo = SlatingApplication.objects.get(applicant=self.bravo)
        self.assertEqual(bravo.position_preferences['first_choice'], [self.treas.id])
        self.assertEqual(bravo.gpa_level, bravo.calculate_gpa_level())

    def test_existing_application_is_untouched(self):
        self.do_import()
        self.charlies_own.refresh_from_db()
        self.assertEqual(self.charlies_own.position_preferences, {'first_choice': [self.vp.id]})
        self.assertIsNone(self.charlies_own.imported_at)

    def test_detail_page_marks_it_imported(self):
        self.do_import()
        app = SlatingApplication.objects.get(applicant=self.bravo)
        r = self.client.get(reverse('slating_app_detail', args=[self.period.id, app.id]))
        self.assertContains(r, 'Imported')
        self.assertContains(r, 'Not asked on the outside form')
        self.assertContains(r, 'Your goals for the chapter')


class AccessTests(_Base):

    def test_ordinary_member_cannot_import(self):
        self.client.force_login(self.alpha)
        r = self.upload()
        self.assertRedirects(r, reverse('slating_dashboard'), fetch_redirect_response=False)
        self.assertEqual(SlatingApplication.objects.count(), 1)

    def test_site_admin_cannot_import_into_a_locked_period(self):
        admin = member('adm', 'adm@test.org', is_admin=True)
        self.client.force_login(admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)


class MatchingTests(TestCase):

    def test_titles_inside_other_titles_and_commas(self):
        period = SlatingPeriod.objects.create(name='P')
        pres = SlatingPosition.objects.create(period=period, title='President', code='PRES')
        vp = SlatingPosition.objects.create(period=period, title='Vice President', code='VP')
        ids, leftover = si.match_positions('Vice President, President', [pres, vp])
        self.assertEqual(ids, [vp.id, pres.id])
        self.assertEqual(leftover, '')
        ids, leftover = si.match_positions('VP and Social Chair', [pres, vp])
        self.assertEqual((ids, leftover), ([vp.id], 'Social Chair'))

    def test_bad_files(self):
        for text in ('', 'only,a,header\n'):
            with self.assertRaises(si.ImportError_):
                si.parse_csv(text)
        with self.assertRaises(si.ImportError_):
            si.decode(b'x' * (si.MAX_BYTES + 1))


class FillInAnswersTests(_Base):
    """A manager can complete an imported application by hand (not a member's own)."""

    def setUp(self):
        super().setUp()
        r = self.upload()
        self.client.post(self.url, self.mapping_post(r, step='import', confirmed='1'))
        self.app = SlatingApplication.objects.get(applicant=self.bravo)
        self.fill = reverse('slating_fill_imported_answers', args=[self.period.id, self.app.id])

    def test_filling_a_not_asked_question_clears_it(self):
        self.assertEqual(self.app.import_not_asked, ['Your goals for the chapter'])
        r = self.client.post(self.fill, {f'field_{self.goals.id}': 'Grow the chapter',
                                         f'field_{self.why.id}': 'Because'})
        self.assertRedirects(r, reverse('slating_app_detail', args=[self.period.id, self.app.id]))
        self.app.refresh_from_db()
        self.assertEqual(self.app.responses.get(field=self.goals).text_value, 'Grow the chapter')
        self.assertEqual(self.app.import_not_asked, [])
        self.assertIn('filled in', self.app.review_notes)
        self.assertIn('Your goals for the chapter', self.app.review_notes)

    def test_correcting_and_clearing_an_imported_answer(self):
        self.client.post(self.fill, {f'field_{self.why.id}': 'Fixed typo', f'field_{self.goals.id}': ''})
        self.assertEqual(self.app.responses.get(field=self.why).text_value, 'Fixed typo')
        self.client.post(self.fill, {f'field_{self.why.id}': '', f'field_{self.goals.id}': ''})
        self.assertFalse(self.app.responses.filter(field=self.why).exists())

    def test_members_own_application_cannot_be_edited(self):
        url = reverse('slating_fill_imported_answers', args=[self.period.id, self.charlies_own.id])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {f'field_{self.why.id}': 'x'}).status_code, 404)
        self.assertFalse(self.charlies_own.responses.exists())

    def test_non_manager_is_refused(self):
        self.client.force_login(self.alpha)
        self.client.post(self.fill, {f'field_{self.goals.id}': 'sneaky'})
        self.assertFalse(self.app.responses.filter(field=self.goals).exists())

    def test_detail_page_offers_it_to_managers(self):
        r = self.client.get(reverse('slating_app_detail', args=[self.period.id, self.app.id]))
        self.assertContains(r, 'Fill in answers')


class DefaultPositionsTests(_Base):
    """v4.0.0: VP Administration, VP Risk Management and the C&B Chair aren't slated."""

    def test_defaults_skip_the_unslated_roles_and_the_modal_agrees(self):
        fresh = SlatingPeriod.objects.create(name='Spring 2027', slating_manager=self.manager)
        self.client.post(reverse('slating_copy_default_positions', args=[fresh.id]))
        codes = set(fresh.positions.values_list('code', flat=True))
        self.assertEqual(codes, {'President', 'EVP', 'VPB', 'VPE', 'VPR', 'VPP', 'VPF'})
        page = self.client.get(reverse('slating_period_setup', args=[fresh.id]))
        for title in ('Vice President of Administration', 'Vice President of Risk Management',
                      'Constitution &amp; Bylaws Chair'):
            self.assertNotContains(page, title)
        self.assertContains(page, 'Vice President of Brotherhood')



class OneFormPerPositionTests(_Base):
    """The President's form took one submission per position (v4.0.0)."""

    CSV = (
        'Timestamp,Email Address,Position,GPA,Why do you want to serve?\n'
        '10/20/2026 10:00:00,bravo@test.org,President,3.2,Lead the chapter\n'
        '10/20/2026 10:05:00,bravo@test.org,Treasurer,3.2,Good with money\n'
        '10/20/2026 10:10:00,bravo@test.org,Vice President,3.2,Lead the chapter\n'
        '10/21/2026 09:00:00,bravo@test.org,Treasurer,3.2,Really good with money\n'
    )

    def test_forms_are_combined_and_a_resubmission_replaces(self):
        r = self.upload(self.CSV)
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        items = {i['row']: i for i in r.context['items']}
        self.assertEqual(r.context['import_count'], 1)
        self.assertIn('Replaced by a newer response for the same position in row 5', items[3]['reason'])
        self.assertEqual(items[5]['merged_rows'], [2, 4, 5])
        self.assertEqual(items[5]['position_titles']['first_choice'],
                         ['President', 'Vice President', 'Treasurer'])
        self.assertContains(r, 'combined from rows 2, 4, 5')

        r = self.upload(self.CSV)
        self.client.post(self.url, self.mapping_post(r, step='import', confirmed='1'))
        app = SlatingApplication.objects.get(applicant=self.bravo)
        self.assertEqual(app.position_preferences['first_choice'],
                         [self.pres.id, self.vp.id, self.treas.id])
        why = app.responses.get(field=self.why).text_value
        self.assertIn('President: Lead the chapter', why)
        self.assertIn('Vice President: Lead the chapter', why)
        self.assertIn('Treasurer: Really good with money', why)
        self.assertNotIn('Good with money', why.replace('Really good with money', ''))
        self.assertEqual(app.reported_gpa, Decimal('3.200'))
        self.assertEqual(SlatingApplication.objects.filter(applicant=self.bravo).count(), 1)

    def test_identical_answers_are_kept_once(self):
        csv = ('Email Address,Position,Why do you want to serve?\n'
               'bravo@test.org,President,Same reason\n'
               'bravo@test.org,Treasurer,Same reason\n')
        r = self.upload(csv)
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        self.assertEqual(item['answers'][self.why], 'Same reason')

    def test_differing_gpas_warn(self):
        csv = ('Email Address,Position,GPA\n'
               'bravo@test.org,President,3.1\n'
               'bravo@test.org,Treasurer,3.3\n')
        r = self.upload(csv)
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        self.assertEqual(item['gpa'], Decimal('3.300'))
        self.assertTrue(any('Different GPAs' in w for w in item['warnings']))
