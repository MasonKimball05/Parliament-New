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

from django.test import TestCase, Client
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

    def test_min_pairwise_distance_is_noticeable(self):
        """Requirement 2: every pair is clearly distinct. Min ΔE across the
        whole palette (plus founder gold) must clear a visible margin."""
        labs = [_lab(c) for c in pc.CLASS_PALETTE] + [_lab(pc.FOUNDERS_COLOR)]
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
