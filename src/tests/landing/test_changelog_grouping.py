"""
v3.31.4 — Mason: "update the changelog page so entries get put into
categories, like all of 3.29.x gets put under a dropdown ... and each one
automatically adds one like that."

Before this, the index page split into exactly two buckets: a flat row of
badges for every v3+ release (v3.29.x alone contributed 32 of them — more
patch releases than most projects ever ship — so the row was unusable), and
a single "Version 2.x" catch-all collapsible for everything else.
`group_changelogs_by_minor()` replaces both with one mechanism: every
changelog file collapses into a `{major}.{minor}.x` group, and a brand new
minor series gets its own group automatically the moment its first file
exists — there is no hardcoded list of series anywhere in the function or
the template, which is the actual thing being tested here (not just "does
3.29.x have its own group today").
"""
from django.test import Client, TestCase

from src.view.changelog import group_changelogs_by_minor, parse_version


def entry(version, has_external=False):
    return {
        'version': version,
        'filename': f'{version}.md',
        'contributors': ['External Person'] if has_external else ['Mason Kimball'],
        'has_external': has_external,
    }


class GroupChangelogsByMinorTests(TestCase):
    def test_entries_in_the_same_minor_series_collapse_into_one_group(self):
        entries = [entry('v3.29.0'), entry('v3.29.1'), entry('v3.29.2')]
        groups = group_changelogs_by_minor(entries)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['label'], '3.29.x')
        self.assertEqual(groups[0]['count'], 3)

    def test_different_minor_series_produce_separate_groups(self):
        entries = [entry('v3.29.0'), entry('v3.30.0'), entry('v2.5.0')]
        groups = group_changelogs_by_minor(entries)
        labels = [g['label'] for g in groups]
        self.assertEqual(labels, ['3.30.x', '3.29.x', '2.5.x'])

    def test_a_brand_new_minor_series_gets_its_own_group_automatically(self):
        """The actual point of the feature: nothing has to be taught about a
        series that didn't exist yet. v3.32.x has never shipped in this
        repo — feeding it in produces a group anyway, with no code change."""
        entries = [entry('v3.29.0'), entry('v3.32.0')]
        groups = group_changelogs_by_minor(entries)
        labels = [g['label'] for g in groups]
        self.assertIn('3.32.x', labels)
        # And it sorts to the front, being the newest.
        self.assertEqual(labels[0], '3.32.x')

    def test_entries_within_a_group_are_sorted_newest_patch_first(self):
        entries = [entry('v3.29.1'), entry('v3.29.10'), entry('v3.29.2')]
        groups = group_changelogs_by_minor(entries)
        versions = [e['version'] for e in groups[0]['entries']]
        # Semantic order, not string order — v3.29.10 must sort above v3.29.2.
        self.assertEqual(versions, ['v3.29.10', 'v3.29.2', 'v3.29.1'])

    def test_a_group_with_any_external_contributor_is_flagged(self):
        entries = [entry('v3.29.0', has_external=False), entry('v3.29.1', has_external=True)]
        groups = group_changelogs_by_minor(entries)
        self.assertTrue(groups[0]['has_external'])

    def test_a_group_with_no_external_contributors_is_not_flagged(self):
        entries = [entry('v3.29.0'), entry('v3.29.1')]
        groups = group_changelogs_by_minor(entries)
        self.assertFalse(groups[0]['has_external'])

    def test_an_empty_list_produces_no_groups(self):
        self.assertEqual(group_changelogs_by_minor([]), [])

    def test_group_key_is_stable_and_dom_safe(self):
        """Used as an HTML id (`changelog-group-panel-{key}`) — must not
        contain a literal dot, which is a valid but awkward CSS id character."""
        entries = [entry('v3.29.0')]
        groups = group_changelogs_by_minor(entries)
        self.assertEqual(groups[0]['key'], '3-29')
        self.assertNotIn('.', groups[0]['key'])


class ChangelogPageRendersGroupsFromTheRealDirectoryTests(TestCase):
    """
    Integration check against the actual `changelogs/` directory, the same
    way `test_known_versions_come_from_the_directory`
    (test_changelog_cache.py) checks `known_versions()` — deriving the
    expectation from disk rather than hardcoding a version number, so this
    doesn't need updating every time a new changelog is added.
    """

    def test_the_page_renders_one_group_per_minor_series_on_disk(self):
        import pathlib
        SRC = pathlib.Path(__file__).resolve().parent.parent.parent
        on_disk = [p.name for p in (SRC.parent / 'changelogs').glob('v*.md')]
        expected_minors = {(parse_version(f)[0], parse_version(f)[1]) for f in on_disk}

        response = Client().get('/changelog/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        for major, minor in expected_minors:
            with self.subTest(major=major, minor=minor):
                self.assertIn(f'{major}.{minor}.x', html)

    def test_the_newest_group_panel_is_not_hidden_by_default(self):
        response = Client().get('/changelog/')
        html = response.content.decode()
        # The FIRST panel's `class="..."` attribute belongs to the newest
        # series (groups are sorted newest-first) and must not start with
        # `hidden`. Anchored on `class="changelog-group-panel ` (trailing
        # space, not hyphen) so this can't accidentally match the `id=`
        # attribute, which shares the same prefix followed by a hyphen.
        anchor = 'class="changelog-group-panel '
        idx = html.index(anchor)
        following = html[idx + len(anchor):idx + len(anchor) + 20].strip()
        self.assertFalse(following.startswith('hidden'), following)

    def test_older_group_panels_are_hidden_by_default(self):
        response = Client().get('/changelog/')
        html = response.content.decode()
        # There should be at least one hidden panel given more than one
        # minor series exists on disk (true today: 2.x and 3.x both have
        # many). The class order is fixed by the template
        # ("changelog-group-panel {% if not forloop.first %}hidden{% endif %}"),
        # so this substring is stable rather than a regex guess.
        self.assertIn('changelog-group-panel hidden', html)
