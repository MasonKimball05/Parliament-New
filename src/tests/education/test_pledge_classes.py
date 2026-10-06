"""
v3.15.0 — pledge-class registry (src/pledge_classes.py) + directory badge.

Locks in the two hard requirements Mason set for the class colors:
  1. every class color is UNIQUE (no repeats within the palette), and
  2. every pair is NOTICEABLY different (min pairwise ΔE well above the
     ~10 "clearly different to the eye" threshold),
plus the sequence math (Founders F22, Alpha S23, ...), free-text
normalization, and the auto-fill-greek save behavior.
"""
import colorsys
import math
from datetime import date
from io import StringIO

from django.core.management import call_command

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from src.models import ParliamentUser
from src import pledge_classes as pc


def _lab(hex_color):
    r = int(hex_color[1:3], 16) / 255
    g = int(hex_color[3:5], 16) / 255
    b = int(hex_color[5:7], 16) / 255

    def lin(c):
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
    r, g, b = lin(r), lin(g), lin(b)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    xn, yn, zn = 0.95047, 1.0, 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x / xn), f(y / yn), f(z / zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


class PaletteGuaranteesTests(TestCase):
    def test_all_colors_unique(self):
        """Requirement 1: no two classes share a color across the palette
        (and the founder gold isn't duplicated in it either)."""
        self.assertEqual(len(pc.CLASS_PALETTE), len(set(pc.CLASS_PALETTE)))
        self.assertNotIn(pc.FOUNDERS_COLOR, pc.CLASS_PALETTE)
        self.assertNotIn(pc.ORIGINAL_FOUNDERS_COLOR, pc.CLASS_PALETTE)

    def test_min_pairwise_distance_is_noticeable(self):
        """Requirement 2: every pair is clearly distinct. Min ΔE across the
        whole palette (plus founder gold) must clear a visible margin."""
        labs = [_lab(c) for c in pc.CLASS_PALETTE] + [
            _lab(pc.FOUNDERS_COLOR), _lab(pc.ORIGINAL_FOUNDERS_COLOR)]
        mind = min(math.dist(labs[i], labs[j])
                   for i in range(len(labs)) for j in range(i + 1, len(labs)))
        self.assertGreater(mind, 10.0,
                           f'min pairwise ΔE {mind:.1f} — colors too similar')

    def test_color_for_index_is_stable_and_bounded(self):
        # Founders always gold; a later class is stable and in-palette.
        self.assertEqual(pc.color_for_index(0), pc.FOUNDERS_COLOR)
        self.assertEqual(pc.color_for_index(3), pc.CLASS_PALETTE[2])
        # Wrap only past the palette length (the documented "ran out" case).
        self.assertEqual(pc.color_for_index(1 + len(pc.CLASS_PALETTE)),
                         pc.CLASS_PALETTE[0])


class SequenceTests(TestCase):
    TODAY = date(2026, 7, 19)

    def test_founders_then_alpha(self):
        classes = pc.all_classes(self.TODAY)
        self.assertEqual(classes[0]['label'], 'Fall 2022')
        self.assertEqual(classes[0]['greek'], 'Founder')
        self.assertTrue(classes[0]['is_founders'])
        self.assertEqual(classes[1]['label'], 'Spring 2023')
        self.assertEqual(classes[1]['greek'], 'Alpha')
        self.assertEqual(classes[2]['label'], 'Fall 2023')
        self.assertEqual(classes[2]['greek'], 'Beta')

    def test_july_boundary_includes_upcoming_fall(self):
        # July → the fall class of the current year is already selectable
        labels = [c['label'] for c in pc.all_classes(self.TODAY)]
        self.assertIn('Fall 2026', labels)
        # ...but not in the spring before it
        spring_labels = [c['label'] for c in pc.all_classes(date(2026, 3, 1))]
        self.assertNotIn('Fall 2026', spring_labels)


class NormalizationTests(TestCase):
    TODAY = date(2026, 7, 19)

    def _n(self, text):
        c = pc.normalize(text, self.TODAY)
        return c['label'] if c else None

    def test_various_shorthand(self):
        self.assertEqual(self._n('fall 2022'), 'Fall 2022')
        self.assertEqual(self._n('Founders'), 'Fall 2022')
        self.assertEqual(self._n('beta'), 'Fall 2023')
        self.assertEqual(self._n('sp2024'), 'Spring 2024')
        self.assertEqual(self._n('Fa 23'), 'Fall 2023')
        self.assertEqual(self._n("spring '25"), 'Spring 2025')
        self.assertIsNone(self._n('not a class'))

    def test_apply_to_fields_autofills_greek(self):
        # Typed semester → canonical label + registry greek (typo/casing fixed)
        self.assertEqual(pc.apply_to_fields('fall 2023', '', self.TODAY),
                         ('Fall 2023', 'Beta'))
        # Typed only a greek name → resolves both
        self.assertEqual(pc.apply_to_fields('', 'Gamma', self.TODAY),
                         ('Spring 2024', 'Gamma'))
        # Unrecognized → preserved verbatim (legacy freedom)
        self.assertEqual(pc.apply_to_fields('Summer 1999', 'Weird', self.TODAY),
                         ('Summer 1999', 'Weird'))


class DirectoryBadgeApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.viewer = ParliamentUser.objects.create_user(
            user_id='pcv', name='Viewer', username='pcv', member_type='Member')
        self.client.force_login(self.viewer)

    def test_card_includes_resolved_badge(self):
        member = ParliamentUser.objects.create_user(
            user_id='pcm', name='Class Member', username='pcm',
            member_type='Member')
        member.pledge_class = 'Spring 2023'
        member.pledge_class_greek = 'Alpha'
        member.save()
        resp = self.client.get(reverse('profile_card', kwargs={'user_id': 'pcm'}))
        self.assertEqual(resp.status_code, 200)
        badge = resp.json()['pledge_class_badge']
        self.assertEqual(badge['greek'], 'Alpha')
        self.assertEqual(badge['color'], pc.color_for_index(1))
        self.assertFalse(badge['is_founders'])

    def test_founder_badge_flagged(self):
        m = ParliamentUser.objects.create_user(
            user_id='pcf', name='Founder Member', username='pcf',
            member_type='Member')
        m.pledge_class, m.pledge_class_greek = 'Fall 2022', 'Founder'
        m.save()
        resp = self.client.get(reverse('profile_card', kwargs={'user_id': 'pcf'}))
        self.assertTrue(resp.json()['pledge_class_badge']['is_founders'])

    def test_unrecognized_class_has_no_badge(self):
        m = ParliamentUser.objects.create_user(
            user_id='pcu', name='Legacy Member', username='pcu',
            member_type='Member')
        m.pledge_class, m.pledge_class_greek = 'Whenever', ''
        m.save()
        resp = self.client.get(reverse('profile_card', kwargs={'user_id': 'pcu'}))
        self.assertIsNone(resp.json()['pledge_class_badge'])


class PledgeClassFoundingFromChapterConfigTests(TestCase):
    """09-27-26 — multi-chapter phase 2: the founding semester comes from
    settings.CHAPTER (founding_year / founding_semester), not a constant."""

    def test_default_is_alpha_mus_fall_2022(self):
        classes = pc.all_classes(date(2023, 3, 1))
        self.assertEqual([c['label'] for c in classes], ['Fall 2022', 'Spring 2023'])
        self.assertEqual(classes[1]['greek'], 'Alpha')

    def test_a_spring_founded_chapter_letters_from_the_next_fall(self):
        from django.conf import settings
        from django.test import override_settings
        cfg = {**settings.CHAPTER, 'founding_year': 2019, 'founding_semester': 'Spring'}
        with override_settings(CHAPTER=cfg):
            classes = pc.all_classes(date(2020, 3, 1))
        self.assertEqual([c['label'] for c in classes], ['Spring 2019', 'Fall 2019', 'Spring 2020'])
        self.assertEqual([c['greek'] for c in classes], ['Founder', 'Alpha', 'Beta'])
        self.assertTrue(classes[0]['is_founders'])

    def test_a_bad_founding_semester_is_an_error(self):
        from django.conf import settings
        from django.test import override_settings
        with override_settings(CHAPTER={**settings.CHAPTER, 'founding_semester': 'Autumn'}):
            with self.assertRaises(ValueError):
                pc.all_classes(date(2023, 3, 1))


class LetteringAnchorTests(TestCase):
    """09-28-26 — CHAPTER['lettering_anchor'] for chapters whose lettering
    doesn't start the semester after founding."""
    TODAY = date(2026, 9, 28)

    def _classes(self, **cfg):
        from django.conf import settings
        with override_settings(CHAPTER={**settings.CHAPTER, **cfg}):
            return {c['label']: c['greek'] for c in pc.all_classes(self.TODAY)}

    def test_default_anchor_is_unchanged(self):
        default = self._classes()
        explicit = self._classes(lettering_anchor='Spring 2023 = Alpha')
        self.assertEqual(default, explicit)
        self.assertEqual(default['Spring 2023'], 'Alpha')

    def test_anchor_shifts_the_sequence(self):
        c = self._classes(lettering_anchor='Fall 2023 = Xi')
        self.assertEqual(c['Fall 2022'], 'Founder')
        self.assertEqual(c['Fall 2023'], 'Xi')
        self.assertEqual(c['Spring 2024'], 'Omicron')
        self.assertEqual(c['Spring 2023'], 'Nu')

    def test_classes_before_lettering_began_have_no_letter(self):
        c = self._classes(lettering_anchor='Fall 2024 = Alpha')
        self.assertEqual(c['Spring 2023'], '')
        self.assertEqual(c['Spring 2024'], '')
        self.assertEqual(c['Fall 2024'], 'Alpha')
        self.assertEqual(c['Spring 2025'], 'Beta')

    def test_doubled_names_parse_both_ways(self):
        self.assertEqual(pc._greek_position('Alpha Beta'), 25)
        self.assertEqual(pc._greek_for_position(25), 'Alpha Beta')
        c = self._classes(lettering_anchor='spring 2023 = omega')
        self.assertEqual(c['Fall 2023'], 'Alpha Alpha')

    def test_colors_follow_the_index_not_the_letter(self):
        from django.conf import settings
        with override_settings(CHAPTER={**settings.CHAPTER, 'lettering_anchor': 'Fall 2023 = Xi'}):
            shifted = [c['color'] for c in pc.all_classes(self.TODAY)]
        self.assertEqual(shifted, [c['color'] for c in pc.all_classes(self.TODAY)])

    def test_malformed_anchor_is_a_startup_error(self):
        from django.conf import settings
        from src.checks_platform import chapter_config_is_valid
        for bad in ('Alpha', 'Summer 2023 = Alpha', 'Fall 2023 = Alfa', 'Fall 23 = Alpha'):
            with self.subTest(bad=bad), override_settings(CHAPTER={**settings.CHAPTER, 'lettering_anchor': bad}):
                self.assertEqual([e.id for e in chapter_config_is_valid(None)], ['src.E001'])


class OriginalFoundersTests(TestCase):
    """The 1800s founders (roll #1–#43) get a Beta Blue badge of their own."""

    def test_badge_is_beta_blue(self):
        for args in (('Original Founders', ''), ('1879', 'Original Founder')):
            badge = pc.badge_context(*args)
            self.assertEqual(badge['color'], '#003da5')
            self.assertEqual(badge['greek'], 'Original Founder')
            self.assertFalse(badge['is_founders'])

    def test_not_in_the_semester_sequence(self):
        greeks = [c['greek'] for c in pc.all_classes(date(2026, 10, 1))]
        self.assertNotIn(pc.ORIGINAL_FOUNDERS_GREEK, greeks)
        # Saving the form leaves them alone rather than "fixing" them
        self.assertEqual(pc.apply_to_fields('1879', 'Original Founder'),
                         ('1879', 'Original Founder'))

    def _member(self, uid, roll, pledge_class=''):
        m = ParliamentUser.objects.create_user(
            user_id=uid, name=f'Member {uid}', username=uid,
            member_type='Member')
        m.role_number, m.pledge_class = roll, pledge_class
        m.save()
        return m

    def test_command_sets_semester_for_rolls_1_to_43_only(self):
        f1 = self._member('of1', '1')
        f43 = self._member('of43', '43', 'Fall 1879')
        f43.pledge_class_greek = 'Alpha'
        f43.save()
        later = self._member('of44', '44')
        clash = self._member('of5', '5', 'Spring 2023')

        call_command('mark_original_founders', stdout=StringIO())  # dry run
        f1.refresh_from_db()
        self.assertEqual(f1.pledge_class, '')

        call_command('mark_original_founders', '--apply', stdout=StringIO())
        for m in (f1, f43, later, clash):
            m.refresh_from_db()
        self.assertEqual(f1.pledge_class, 'Original Founders')
        self.assertEqual(f1.pledge_class_greek, '')
        # Semester replaced; greek field untouched
        self.assertEqual((f43.pledge_class, f43.pledge_class_greek),
                         ('Original Founders', 'Alpha'))
        self.assertEqual(later.pledge_class, '')
        self.assertEqual(clash.pledge_class, 'Spring 2023')
        self.assertEqual(pc.badge_context(f43.pledge_class, f43.pledge_class_greek)['color'],
                         pc.ORIGINAL_FOUNDERS_COLOR)


class OriginalFounderNotSelfServeTests(TestCase):
    """Members can't give themselves the Original Founder badge from their
    own profile, by either field, in any casing, via form or AJAX."""

    def setUp(self):
        self.member = ParliamentUser.objects.create_user(
            user_id='ofm', name='Modern Member', username='ofm',
            member_type='Member')
        self.member.pledge_class, self.member.pledge_class_greek = 'Spring 2023', 'Alpha'
        self.member.save()
        self.client = Client()
        self.client.force_login(self.member)

    def _post(self, ajax=False, **fields):
        data = {'extended_profile_submit': '1', 'about_me': 'changed'}
        data.update(fields)
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.client.post(reverse('profile'), data, **headers)

    def assertUnchanged(self):
        self.member.refresh_from_db()
        self.assertEqual((self.member.pledge_class, self.member.pledge_class_greek),
                         ('Spring 2023', 'Alpha'))
        self.assertNotEqual(self.member.about_me, 'changed')

    def test_greek_field_rejected(self):
        self._post(pledge_class='1879', pledge_class_greek='Original Founder')
        self.assertUnchanged()

    def test_class_field_rejected_any_case(self):
        self._post(pledge_class='  ORIGINAL founders ')
        self.assertUnchanged()

    def test_ajax_rejected_with_error(self):
        resp = self._post(ajax=True, pledge_class_greek='original founder')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])
        self.assertUnchanged()

    def test_ordinary_class_change_still_saves(self):
        self._post(pledge_class='Fall 2023')
        self.member.refresh_from_db()
        self.assertEqual(self.member.pledge_class_greek, 'Beta')


class DirectoryOriginalFoundersTests(TestCase):
    """Founders are hidden by default, and only an extra opt-in after
    'Show Alumni' reveals them, in their own section."""

    def setUp(self):
        self.viewer = ParliamentUser.objects.create_user(
            user_id='dv', name='Viewer', username='dv', member_type='Member')
        self.client = Client()
        self.client.force_login(self.viewer)
        self.alum = ParliamentUser.objects.create_user(
            user_id='da', name='Regular Alum', username='da',
            member_type='Member')
        self.founder = ParliamentUser.objects.create_user(
            user_id='df', name='Founding Father', username='df',
            member_type='Member')
        self.alum.member_status = 'Alumni'
        self.alum.save()
        self.founder.member_status = 'Alumni'
        self.founder.pledge_class = 'Original Founders'
        self.founder.save()

    def _get(self, **params):
        return self.client.get(reverse('member_directory'), params)

    def test_hidden_by_default(self):
        self.assertNotContains(self._get(), 'Founding Father')

    def test_show_alumni_alone_still_hides_founders(self):
        resp = self._get(show_alumni='1')
        self.assertContains(resp, 'Regular Alum')
        self.assertNotContains(resp, 'Founding Father')
        self.assertContains(resp, 'Show Original Founders')

    def test_founders_flag_needs_alumni_shown(self):
        self.assertNotContains(self._get(show_founders='1'), 'Founding Father')

    def test_both_flags_show_founders_section(self):
        resp = self._get(show_alumni='1', show_founders='1')
        self.assertEqual([m.name for m in resp.context['original_founders']],
                         ['Founding Father'])
        self.assertNotIn(self.founder, resp.context['alumni'])
        self.assertContains(resp, 'Hide Original Founders')
