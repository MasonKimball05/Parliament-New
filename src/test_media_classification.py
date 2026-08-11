"""
v3.19.6 — every upload directory must be classified, and the build fails if one is not.

⚠️ THIS IS THE DELIVERABLE OF THE `FileField` AUDIT, NOT THE LIST IT PRODUCED.

The list is today's answer to today's schema and will be stale the first time
someone adds a model. This test is the part that stays true.

WHY IT EXISTS
-------------
v3.19.3 found that legislation draft attachments were served by `/media/`, whose
only gate is `@login_required`, under a promise ("only you can see it until you
publish") that `/media/` cannot keep. It built an author-scoped view.

v3.19.5 found that the old route was still open, and closed it with
`PRIVATE_MEDIA_PREFIXES` — a *set*, deliberately, with a test asserting that
every entry has a replacement route. Its comment called `legislation_drafts/`
*"the first such thing in this codebase."*

It was the ninth. The 08-10 review walked the schema and found eight more, four
holding the most confidential material the application handles. Neither release
was wrong about the property; **neither enumerated the population the property
is about.** `test_the_private_set_names_only_directories_that_have_their_own_view`
guards entries that are IN the set and is structurally unable to see a directory
that was never added — which is the same blind spot, one level up, as the
template grep that closed the v3.19.3 finding prematurely.

So: the set that decides is now checked against the schema that generates it.
A new `FileField` whose `upload_to` nobody classified fails here, at the moment
it is written, and the failure message says what the decision is.

⚠️ IF THIS TEST FAILS, THE ANSWER IS NOT TO ADD THE PREFIX TO WHICHEVER SET MAKES
IT PASS. Ask the question it is asking: *may any logged-in member read this
file?* If yes, `PUBLIC_MEDIA_PREFIXES`. If no, it needs an ownership-aware view
in `src/view/serve_private_upload.py` AND an entry in `PRIVATE_MEDIA_PREFIXES` —
and adding the prefix without the view makes the files unreachable by anyone,
which looks exactly like the fix working.
"""
import os

from django.apps import apps
from django.db import models
from django.test import TestCase

from src.view.serve_media import PRIVATE_MEDIA_PREFIXES, PUBLIC_MEDIA_PREFIXES


def _upload_prefix(field):
    """
    First path segment of a field's `upload_to`, or None if it cannot be known
    statically.

    `upload_to` may be a callable (`legislation_draft_upload_path`), in which
    case the directory is chosen at save time and cannot be read off the field.
    Those are handled by `_CALLABLE_UPLOAD_PREFIXES` below rather than guessed —
    a test that guessed would eventually guess wrong and report a clean sweep.
    """
    upload_to = getattr(field, 'upload_to', '')
    if not upload_to or callable(upload_to):
        return None
    # `%Y/%m` and friends are strftime directives, not directories we classify;
    # only the first segment is ever compared by `serve_media`.
    return str(upload_to).strip('/').split('/')[0]


#: Fields whose `upload_to` is a callable, mapped to the directory that callable
#: writes into. Maintained by hand *because* it cannot be derived — and the test
#: below asserts the map is complete, so a new callable cannot be forgotten.
_CALLABLE_UPLOAD_PREFIXES = {
    ('src', 'legislationdraft', 'document'): 'legislation_drafts',
    # v3.19.6 — uuid names for the four confidential directories (migration
    # 0017). Defence in depth under the views, not a substitute for them.
    ('src', 'kaireport', 'attachment'): 'kai_reports',
    ('src', 'kaireportfieldresponse', 'file_value'): 'kai_reports',
    ('src', 'slatingapplication', 'gpa_screenshot'): 'slating',
    ('src', 'slatingapplicationresponse', 'file_value'): 'slating',
}


def _all_file_fields():
    """Every concrete FileField/ImageField in the project's own models."""
    for model in apps.get_models():
        if model._meta.app_label != 'src':
            continue
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField):
                yield model, field


class EveryUploadDirectoryIsClassified(TestCase):
    """The enumeration v3.19.3 and v3.19.5 both skipped."""

    def test_every_upload_directory_is_classified(self):
        """
        ⚠️ THE TEST THE 08-10 REVIEW WAS. Walk the schema, not the set.

        Eight directories failed this on the tree as it stood before v3.19.6:
        kai_reports, slating, excuse_documents, service_hours, bug_reports (and
        the two `custom_fields` children, which share a first segment).
        """
        unclassified = []

        for model, field in _all_file_fields():
            key = (model._meta.app_label, model._meta.model_name, field.name)
            prefix = _upload_prefix(field) or _CALLABLE_UPLOAD_PREFIXES.get(key)

            if prefix is None:
                unclassified.append(
                    f'{model.__name__}.{field.name}: upload_to is a callable and '
                    f'is not in _CALLABLE_UPLOAD_PREFIXES. Add '
                    f'{key!r} → the directory it writes into.'
                )
                continue

            if prefix in PRIVATE_MEDIA_PREFIXES or prefix in PUBLIC_MEDIA_PREFIXES:
                continue

            unclassified.append(
                f'{model.__name__}.{field.name} stores in {prefix!r}, which is in '
                f'neither PUBLIC_MEDIA_PREFIXES nor PRIVATE_MEDIA_PREFIXES. '
                f'Decide: may ANY logged-in member read this file? '
                f'If yes → PUBLIC_MEDIA_PREFIXES. If no → an ownership-aware view '
                f'in src/view/serve_private_upload.py AND PRIVATE_MEDIA_PREFIXES.'
            )

        self.assertEqual(
            unclassified, [],
            'Unclassified upload directories:\n  ' + '\n  '.join(unclassified),
        )

    def test_the_two_sets_are_disjoint(self):
        """
        A prefix in both sets would mean `/media/` refuses a directory something
        else calls public — a contradiction that would read as a working guard.
        """
        self.assertEqual(
            PRIVATE_MEDIA_PREFIXES & PUBLIC_MEDIA_PREFIXES, frozenset(),
            'A directory cannot be both public-to-members and private.',
        )

    def test_no_classified_prefix_is_unused(self):
        """
        ⚠️ THE OTHER DIRECTION, and it is the one that rots quietly.

        A prefix left in either set after its model is deleted looks like
        coverage forever. This is the same defect class as v3.19.4's
        `perf_sampled_count` (written, never read) and v3.18.7's dead cache key —
        CLAUDE.md records it twice, so it gets an assertion here rather than a
        third discovery.
        """
        live = set()
        for model, field in _all_file_fields():
            key = (model._meta.app_label, model._meta.model_name, field.name)
            prefix = _upload_prefix(field) or _CALLABLE_UPLOAD_PREFIXES.get(key)
            if prefix:
                live.add(prefix)

        stale = (PRIVATE_MEDIA_PREFIXES | PUBLIC_MEDIA_PREFIXES) - live
        self.assertEqual(
            stale, set(),
            f'Classified but no model writes there any more: {sorted(stale)}. '
            f'Remove them, or the sets read as coverage they no longer provide.',
        )

    def test_the_callable_map_has_no_stale_entries(self):
        """`_CALLABLE_UPLOAD_PREFIXES` must not outlive the fields it describes."""
        live_callables = {
            (m._meta.app_label, m._meta.model_name, f.name)
            for m, f in _all_file_fields()
            if callable(getattr(f, 'upload_to', ''))
        }
        self.assertEqual(
            set(_CALLABLE_UPLOAD_PREFIXES) - live_callables, set(),
            'Entries describe fields that no longer have a callable upload_to.',
        )


class ThePrivateDirectoriesMatchTheViewsThatServeThem(TestCase):
    """
    The set says "served somewhere else". This says where, for all of them.

    `test_serve_media.py` already asserts this for entries in the set; the
    difference here is that this class names the whole map in one place, so the
    v3.19.6 remediation can be read as a table rather than as eight routes
    scattered through `urls.py`.
    """

    #: prefix → the url name that serves it instead of `/media/`.
    EXPECTED_ROUTES = {
        'legislation_drafts': 'legislation_draft_document',
        'kai_reports': 'kai_report_attachment',
        'slating': 'slating_gpa_screenshot',
        'excuse_documents': 'excuse_document',
        'service_hours': 'service_hours_attachment',
        'bug_reports': 'bug_report_screenshot',
    }

    def test_every_private_prefix_has_a_replacement_route(self):
        from django.urls import get_resolver

        route_names = set(get_resolver().reverse_dict.keys())

        for prefix in PRIVATE_MEDIA_PREFIXES:
            self.assertIn(
                prefix, self.EXPECTED_ROUTES,
                f'{prefix!r} is refused by /media/ with no recorded replacement.',
            )
            self.assertIn(
                self.EXPECTED_ROUTES[prefix], route_names,
                f'{prefix!r} is refused by /media/ but '
                f'{self.EXPECTED_ROUTES[prefix]!r} is not routed — the files are '
                f'now unreachable by anyone, which looks exactly like a fix.',
            )

    def test_the_map_covers_the_whole_set(self):
        self.assertEqual(
            set(self.EXPECTED_ROUTES), set(PRIVATE_MEDIA_PREFIXES),
            'EXPECTED_ROUTES and PRIVATE_MEDIA_PREFIXES have drifted.',
        )


class TheClassificationIsEnforcedAtRequestTime(TestCase):
    """
    ⚠️ The sets are data; this asserts `serve_media` actually reads them.

    A test suite that only checked set membership would pass against a
    `serve_media` whose guard had been deleted.
    """

    def test_serve_media_refuses_every_private_prefix(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from src.constants import MemberType

        User = get_user_model()
        # An ordinary ACTIVE member with no special standing — deliberately the
        # weakest credential the app issues, because the finding was that this
        # user could read Kai evidence and doctors' notes.
        user = User.objects.create_user(
            username='classification-probe',
            password='x' * 20,
            name='Probe',
            user_id='990001',
            member_type=MemberType.MEMBER,
        )

        with self.settings(MEDIA_ROOT=self._tmp_media()):
            self.client.force_login(user)
            for prefix in sorted(PRIVATE_MEDIA_PREFIXES):
                resp = self.client.get(
                    reverse('serve_media', kwargs={'path': f'{prefix}/probe.pdf'}))
                self.assertEqual(
                    resp.status_code, 404,
                    f'/media/{prefix}/ must not be served by serve_media.',
                )

    def _tmp_media(self):
        import tempfile

        root = tempfile.mkdtemp(prefix='parliament-media-classification-')
        # A real file in each private directory, so a 404 proves the guard fired
        # rather than proving the file was missing — the negative control this
        # class would otherwise be missing.
        for prefix in PRIVATE_MEDIA_PREFIXES:
            os.makedirs(os.path.join(root, prefix), exist_ok=True)
            with open(os.path.join(root, prefix, 'probe.pdf'), 'wb') as fh:
                fh.write(b'%PDF-1.4 probe')
        return root
