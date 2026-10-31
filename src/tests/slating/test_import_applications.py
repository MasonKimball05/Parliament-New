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
    return ParliamentUser.objects.create(user_id=uid, username=uid, name=kw.pop('name', f'Member {uid}'),
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
        files = response.context['files']
        data = {'csv_text': [f['text'] for f in files], 'csv_name': [f['name'] for f in files],
                'keep_unmapped': '1'}
        data.update({f'col_{c["index"]}': c['kind'] for c in cols})
        data.update({f'colhead_{c["index"]}': c['header'] for c in cols})   # as the page posts them
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
        self.assertIn('data-csv-target', form)   # the script adds csv_name/csv_text per file
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
        self.assertEqual(items[4]['reason'], 'No Parliament member matches "nobody@test.org". Choose who it belongs to.')
        self.assertIn('Already applied in Parliament', items[6]['reason'])
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
        self.assertIn('row 2, row 5, combined', alpha.review_notes)
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
        self.assertIn('Replaced by a newer response for the same position (row 5)', items[3]['reason'])
        self.assertEqual(items[5]['merged_rows'], [2, 4, 5])
        self.assertEqual(items[5]['position_titles']['first_choice'],
                         ['President', 'Vice President', 'Treasurer'])
        self.assertContains(r, 'combined from row 2, row 4, row 5')

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


class TimestampTests(TestCase):
    """v4.3.0: Google Forms' own export ends in a 12-hour time and a zone."""

    def test_google_forms_export(self):
        from datetime import datetime, timezone as dt_timezone
        utc = datetime(2026, 10, 6, 14, 31, 59, tzinfo=dt_timezone.utc)
        self.assertEqual(si.parse_timestamp('2026/10/06 9:31:59 AM EST'), utc)
        self.assertEqual(si.parse_timestamp('2026/10/06 10:31:59 AM EDT'), utc)
        self.assertEqual(si.parse_timestamp('2026/10/06 9:31:59 AM GMT-5'), utc)
        self.assertEqual(si.parse_timestamp('10/06/2026 2:31:59 PM UTC'), utc)
        self.assertIsNotNone(si.parse_timestamp('2026/10/06 9:31:59 AM'))
        self.assertIsNone(si.parse_timestamp('2026/10/06 9:31:59 AM XYZ'))
        self.assertIsNone(si.parse_timestamp('sometime'))


class FixOnPreviewTests(_Base):
    """v4.3.0: positions, submitted time and GPA can be corrected on the preview."""

    CSV = ('Timestamp,Email Address,Position,GPA\n'
           'yesterday,bravo@test.org,Recruitment brotherhood,abc\n')

    def preview(self, **extra):
        r = self.upload(self.CSV)
        self.base = self.mapping_post(r, step='preview')
        r = self.client.post(self.url, self.base)
        self.item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        return r

    def fixed_post(self, step, **extra):
        pk = self.bravo.pk
        data = dict(self.base, step=step)
        fix = self.item['fix']
        data[f'was_{pk}_submitted'] = fix['was_submitted']
        data[f'fix_{pk}_submitted'] = fix['submitted']
        data[f'was_{pk}_gpa'] = fix['was_gpa']
        data[f'fix_{pk}_gpa'] = fix['gpa']
        for p in fix['positions']:
            data[f'was_{pk}_pos_{p["id"]}'] = p['was']
            data[f'fix_{pk}_pos_{p["id"]}'] = p['tier']
        data.update({k.replace('PK', str(pk)): v for k, v in extra.items()})
        return data

    def test_the_problems_show_with_controls(self):
        r = self.preview()
        self.assertTrue(any(w.startswith("Couldn't read the timestamp") for w in self.item['warnings']))
        self.assertTrue(any(w.startswith('Unrecognised position text') for w in self.item['warnings']))
        self.assertIn('No positions matched.', self.item['warnings'])
        self.assertContains(r, f'name="fix_{self.bravo.pk}_pos_{self.pres.id}"')
        self.assertContains(r, f'name="fix_{self.bravo.pk}_submitted"')

    def test_unchanged_controls_change_nothing(self):
        self.preview()
        r = self.client.post(self.url, self.fixed_post('preview'))
        item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        self.assertEqual(item['fixed'], [])
        self.assertIn('No positions matched.', item['warnings'])

    def test_fixes_clear_the_warnings_and_are_imported(self):
        self.preview()
        extra = {f'fix_PK_pos_{self.treas.id}': 'first_choice',
                 f'fix_PK_pos_{self.pres.id}': 'do_not_want',
                 'fix_PK_submitted': '2026-10-06T09:31', 'fix_PK_gpa': '3.25'}
        r = self.client.post(self.url, self.fixed_post('preview', **extra))
        item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        self.assertEqual(item['fixed'], ['positions', 'submitted time', 'GPA'])
        self.assertEqual(item['warnings'], [])
        self.assertContains(r, 'Fixed by hand: positions, submitted time, GPA')

        self.client.post(self.url, self.fixed_post('import', confirmed='1', **extra))
        app = SlatingApplication.objects.get(applicant=self.bravo)
        self.assertEqual(app.position_preferences['first_choice'], [self.treas.id])
        self.assertEqual(app.position_preferences['do_not_want'], [self.pres.id])
        self.assertEqual(app.reported_gpa, Decimal('3.250'))
        from django.utils import timezone
        self.assertEqual(timezone.localtime(app.submitted_at).strftime('%Y-%m-%d %H:%M'), '2026-10-06 09:31')
        self.assertIn('Set by hand during the import: positions, submitted time, GPA.', app.review_notes)

    def test_a_remapped_column_is_read_again(self):
        # The controls still hold the CSV's first reading; a new mapping wins.
        self.preview()
        data = self.fixed_post('preview')
        data['col_2'] = ''   # stop reading the position column
        r = self.client.post(self.url, data)
        item = [i for i in r.context['items'] if i['status'] == 'import'][0]
        self.assertEqual(item['fixed'], [])
        self.assertFalse(any(w.startswith('Unrecognised') for w in item['warnings']))


# ── v4.3.0: tie rows to members, several files, undo an import ──────────────

NAME_CSV = (
    'Timestamp,Name,Position\n'
    '10/20/2026 14:05:11,Robert Alden Campbell,President\n'
    '10/20/2026 15:00:00,Sawyer W. Guffey,Treasurer\n'
    '10/20/2026 16:00:00,Bobby Campbell,Vice President\n'
    '10/20/2026 17:00:00,Planky John Pledge,Treasurer\n'
)


class TieRowsToMembersTests(_Base):
    """Mason (10-30-26): rows whose name didn't match were skipped with no way to fix them."""

    def setUp(self):
        super().setUp()
        self.robert = member('rcampbell', 'rc@test.org', name='Robert Campbell', preferred_name='Bobby')
        self.sawyer = member('sguffey', 'sg@test.org', name='Sawyer Guffey')
        self.john = member('jpledge', 'jp@test.org', name='John Pledge')

    def preview(self, **extra):
        r = self.upload(NAME_CSV)
        data = self.mapping_post(r, step='preview')
        data.update(extra)
        return self.client.post(self.url, data)

    def test_middle_names_initials_and_preferred_names_match(self):
        items = {i['row']: i for i in self.preview().context['items']}
        self.assertEqual(items[2]['applicant'], self.robert)        # middle name dropped...
        self.assertTrue(any('by first and last name' in w for w in items[5]['warnings'] + items[2]['warnings']))
        self.assertEqual(items[3]['applicant'], self.sawyer)        # ...and an initial
        self.assertEqual(items[4]['applicant'], self.robert)        # preferred name + last
        self.assertEqual(items[5]['status'], 'skip')
        self.assertIn('Choose who it belongs to', items[5]['reason'])
        self.assertIn(self.john, items[5]['suggestions'])           # same last name

    def test_a_row_can_be_tied_to_a_member_or_left_out(self):
        r = self.preview(who_5=str(self.john.pk), who_3='skip')
        items = {i['row']: i for i in r.context['items']}
        self.assertEqual(items[5]['applicant'], self.john)
        self.assertEqual(items[5]['status'], 'import')
        self.assertEqual(items[3]['reason'], 'Left out by hand.')
        self.assertContains(r, f'<option value="{self.john.pk}" selected')

        r = self.upload(NAME_CSV)
        data = self.mapping_post(r, step='import', confirmed='1', who_5=str(self.john.pk), who_3='skip')
        self.client.post(self.url, data)
        app = SlatingApplication.objects.get(applicant=self.john)
        self.assertIn('"Planky John Pledge" tied to this member by hand', app.review_notes)
        self.assertFalse(SlatingApplication.objects.filter(applicant=self.sawyer).exists())

    def test_ambiguous_email_offers_the_choices(self):
        r = self.upload()
        items = {i['row']: i for i in self.client.post(
            self.url, self.mapping_post(r, step='preview', who_4=str(self.bravo.pk))).context['items']}
        # "nobody@test.org" tied to Bravo, so Bravo's two rows (different positions) combine.
        self.assertEqual(items[4]['reason'].split(' with ')[0], 'Combined into one application')


class SeveralFilesTests(_Base):
    PRES = 'Timestamp,Email Address,Position,Why do you want to serve?\n10/20/2026 14:05:11,alpha@test.org,President,Lead\n'
    TREAS = ('Email address,Timestamp,Position,Why do you want to serve?,Budget idea\n'
             'ALPHA@test.org,10/21/2026 09:00:00,Treasurer,Money,Dues app\n'
             'bravo@test.org,10/21/2026 10:00:00,Treasurer,Count,\n')

    def post_files(self, step='upload', **extra):
        data = {'step': step, 'csv_name': ['President.csv', 'Treasurer.csv'],
                'csv_text': [self.PRES, self.TREAS]}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_columns_line_up_by_question_and_rows_say_their_file(self):
        r = self.post_files()
        self.assertEqual([c['header'] for c in r.context['columns']],
                         ['Timestamp', 'Email Address', 'Position', 'Why do you want to serve?', 'Budget idea'])
        self.assertEqual(r.context['row_count'], 3)
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        alpha = next(i for i in r.context['items'] if i['status'] == 'import' and i['applicant'] == self.alpha)
        self.assertEqual(alpha['merged_where'], ['President.csv row 2', 'Treasurer.csv row 2'])
        self.assertEqual(alpha['positions']['first_choice'], [self.pres.id, self.treas.id])
        self.assertContains(r, 'Treasurer.csv row 3')

    def test_a_file_can_be_removed_and_choices_are_kept(self):
        r = self.post_files()
        data = self.mapping_post(r, step='remove_0')
        data['col_4'] = f'field_{self.goals.id}'           # "Budget idea" → goals, by hand
        r = self.client.post(self.url, data)
        self.assertEqual([f['name'] for f in r.context['files']], ['Treasurer.csv'])
        kinds = {c['header']: c['kind'] for c in r.context['columns']}
        self.assertEqual(kinds['Budget idea'], f'field_{self.goals.id}')
        self.assertEqual(kinds['Email address'], 'applicant_email')

    def test_removing_the_last_file_goes_back_to_upload(self):
        r = self.upload()
        r = self.client.post(self.url, self.mapping_post(r, step='remove_0'))
        self.assertEqual(r.context['step'], 'upload')


class RemoveImportTests(_Base):

    def setUp(self):
        super().setUp()
        r = self.upload()
        self.client.post(self.url, self.mapping_post(r, step='import', confirmed='1'))
        self.remove_url = reverse('slating_remove_imported', args=[self.period.id])

    def test_earlier_imports_are_listed_and_a_whole_import_can_be_removed(self):
        r = self.client.get(self.url)
        batches = r.context['earlier_imports']
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]['apps']), 2)              # Alpha, Bravo
        self.assertContains(r, 'Remove this import')
        self.client.post(self.remove_url, {'batch': batches[0]['key']})
        self.assertEqual(list(SlatingApplication.objects.all()), [self.charlies_own])

    def test_one_application_and_only_ones_nothing_is_built_on(self):
        alpha = SlatingApplication.objects.get(applicant=self.alpha)
        bravo = SlatingApplication.objects.get(applicant=self.bravo)
        bravo.status = 'interviewed'
        bravo.save()
        self.client.post(self.remove_url, {'app': alpha.id})
        self.assertFalse(SlatingApplication.objects.filter(id=alpha.id).exists())
        r = self.client.post(self.remove_url, {'app': bravo.id}, follow=True)
        self.assertTrue(SlatingApplication.objects.filter(id=bravo.id).exists())
        self.assertContains(r, 'Kept')
        # A member's own application is never removed here.
        self.client.post(self.remove_url, {'app': self.charlies_own.id})
        self.assertTrue(SlatingApplication.objects.filter(id=self.charlies_own.id).exists())

    def test_reimporting_says_how(self):
        r = self.upload()
        r = self.client.post(self.url, self.mapping_post(r, step='preview'))
        alpha = next(i for i in r.context['items'] if i['applicant'] == self.alpha)
        self.assertIn('remove that application under Earlier imports', alpha['reason'])

    def test_members_and_get_are_refused(self):
        self.assertEqual(self.client.get(self.remove_url).status_code, 405)
        self.client.force_login(self.alpha)
        alpha = SlatingApplication.objects.get(applicant=self.alpha)
        self.client.post(self.remove_url, {'app': alpha.id})
        self.assertTrue(SlatingApplication.objects.filter(id=alpha.id).exists())
