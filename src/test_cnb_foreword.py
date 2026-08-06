"""
v3.19.1 — the Foreword document, its feature flag, and document ordering.

THE ONE THAT MATTERS IS `ForewordFailsClosedTests`.

The Foreword is the text of a Constitution and Bylaws that **has not passed a
chapter vote**, seeded ahead of the vote so that adopting it is a flag toggle
rather than a deploy. Everything else in this file is ordinary regression
cover; that class is the thing standing between staged text and the chapter
reading unpassed governance as though it were in force.

It is worth stating why that is not merely a matter of setting `is_enabled=False`
in the seeder. `FeatureFlag.is_feature_enabled` **fails OPEN** — a name with no
row returns `True` — which is the right default for the three documents already
in force and exactly wrong here. The only thing that inverts it is membership in
`FeatureFlag.DISABLED_BY_DEFAULT`. So the safety property depends on a name in a
list in a different module from the code that relies on it, and a rename or a
tidy-up would break it silently. Hence a test that asserts the OUTCOME (a
database with no flag rows does not show the Foreword) rather than the mechanism.
"""

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Article, GoverningDocument, ParliamentUser, Section
from src.models_feature_flags import FeatureFlag


def make_user(uid='cnb-user', **kwargs):
    defaults = dict(
        name='C&B User', username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('cnb-test-pass-12345!')
    user.save()
    return user


def make_document(doc_type, *, with_article=True, **kwargs):
    doc = GoverningDocument.objects.create(
        doc_type=doc_type,
        title=kwargs.pop('title', doc_type.title()),
        **kwargs,
    )
    if with_article:
        article = Article.objects.create(document=doc, number='I', title='An Article')
        Section.objects.create(article=article, number='1', content='Section text.')
    return doc


class ForewordFailsClosedTests(TestCase):
    """
    A staged, unpassed document must not be visible by accident.

    Each test removes a different thing someone might plausibly remove during a
    refactor, and asserts the Foreword stays hidden anyway.
    """

    def setUp(self):
        cache.clear()
        self.foreword = make_document(
            'foreword', with_article=False,
            title='Foreword', preamble='Plant trees for future Betas.',
        )
        self.constitution = make_document('constitution', title='Constitution')

    def test_with_no_flag_rows_at_all_the_foreword_is_hidden(self):
        """
        ⚠️ THE CENTRAL ASSERTION OF THIS RELEASE.

        No `FeatureFlag` rows exist here — this is a database where nobody ran
        `seed_feature_flags`, which is the realistic failure (a fresh deploy, a
        restored dump, a test database). Python flag lookups fail OPEN, so the
        naive expectation is that everything shows. The Foreword must not.
        """
        self.assertEqual(FeatureFlag.objects.count(), 0)

        visible = set(GoverningDocument.enabled().values_list('doc_type', flat=True))
        self.assertNotIn(
            'foreword', visible,
            'The Foreword was visible in a database with no feature flag rows. '
            'This is unpassed governance being published by default. Check that '
            "'cnb_foreword' is still in FeatureFlag.DISABLED_BY_DEFAULT — that "
            'list is the only thing making this lookup fail closed.',
        )

    def test_documents_already_in_force_still_show_with_no_flag_rows(self):
        """
        THE NEGATIVE CONTROL, and it is what makes the test above meaningful.
        Without it, a `enabled()` that returned nothing at all would pass.

        It is also a real requirement in its own right: a database with no flag
        rows must still show the Constitution. Hiding governance is its own kind
        of failure, just a quieter one.
        """
        visible = set(GoverningDocument.enabled().values_list('doc_type', flat=True))
        self.assertIn(
            'constitution', visible,
            'The Constitution vanished in a database with no flag rows. Documents '
            'in force must fail OPEN — only the staged Foreword fails closed.',
        )

    def test_an_explicitly_disabled_flag_hides_the_foreword(self):
        FeatureFlag.objects.create(
            name='cnb_foreword', display_name='C&B — Foreword',
            description='', category='documents', is_enabled=False,
        )
        cache.clear()
        self.assertNotIn(
            'foreword',
            set(GoverningDocument.enabled().values_list('doc_type', flat=True)),
        )

    def test_enabling_the_flag_publishes_the_foreword(self):
        """The other direction — the toggle has to actually work."""
        FeatureFlag.objects.create(
            name='cnb_foreword', display_name='C&B — Foreword',
            description='', category='documents', is_enabled=True,
        )
        cache.clear()
        self.assertIn(
            'foreword',
            set(GoverningDocument.enabled().values_list('doc_type', flat=True)),
            'Enabling cnb_foreword did not publish the Foreword. The flag is '
            'the entire adoption mechanism; if this fails, passing the new C&B '
            'requires a deploy.',
        )

    def test_the_seeder_ships_the_flag_disabled(self):
        """
        Belt as well as braces: `DISABLED_BY_DEFAULT` covers the missing-row
        case, this covers the seeded case. Both have to be right — a seeder that
        shipped `is_enabled=True` would publish the Foreword on the first run of
        a command everyone runs at deploy.
        """
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_feature_flags', stdout=StringIO(), stderr=StringIO())

        flag = FeatureFlag.objects.get(name='cnb_foreword')
        self.assertFalse(
            flag.is_enabled,
            'seed_feature_flags created cnb_foreword ENABLED. The seeded text is '
            'the real, unpassed foreword — this would publish it to the chapter.',
        )

    def test_the_other_three_seed_enabled(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_feature_flags', stdout=StringIO(), stderr=StringIO())

        for name in ('cnb_constitution', 'cnb_bylaws', 'cnb_appendix'):
            with self.subTest(flag=name):
                self.assertTrue(
                    FeatureFlag.objects.get(name=name).is_enabled,
                    f'{name} seeded disabled — this hides governance in force.',
                )


class ForewordViewerTests(TestCase):
    """The member-facing surface, end to end."""

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = Client()
        self.client.force_login(self.user)
        self.foreword = make_document(
            'foreword', with_article=False,
            title='Foreword', preamble='Plant trees for future Betas.',
        )
        make_document('constitution', title='Constitution')

    def _enable_foreword(self, enabled=True):
        FeatureFlag.objects.update_or_create(
            name='cnb_foreword',
            defaults=dict(
                display_name='C&B — Foreword', description='',
                category='documents', is_enabled=enabled,
            ),
        )
        cache.clear()

    def test_the_foreword_text_is_absent_from_the_page_when_disabled(self):
        """
        Asserts on the RENDERED BODY, not on the context, because the promise
        being kept is that a member cannot read the text — and a document
        excluded from one queryset but rendered from another would satisfy a
        context-level assertion while breaking the promise. (v3.18.5's rule:
        a redaction must cover every column the template renders.)
        """
        self._enable_foreword(False)
        response = self.client.get(reverse('constitution_bylaws'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Plant trees for future Betas')

    def test_the_foreword_text_appears_when_enabled(self):
        self._enable_foreword(True)
        response = self.client.get(reverse('constitution_bylaws'))
        self.assertContains(response, 'Plant trees for future Betas')

    def test_the_prose_block_is_not_labelled_preamble(self):
        """
        A Foreword rendered through the preamble block would otherwise be
        headed "Preamble" — the wrong word in the first place a reader looks.
        """
        self._enable_foreword(True)
        response = self.client.get(reverse('constitution_bylaws'))
        body = response.content.decode()

        marker = 'Plant trees for future Betas'
        self.assertIn(marker, body)
        # The label sits immediately above the text in the same card.
        card = body[max(0, body.index(marker) - 800):body.index(marker)]
        self.assertNotIn(
            'Preamble', card,
            'The Foreword\'s text is introduced by a heading reading "Preamble". '
            'GoverningDocument.is_prose_only should be switching that label.',
        )


class DocumentOrderingTests(TestCase):
    """
    ⚠️ THIS IS A LATENT BUG FIX, NOT A FEATURE.

    `GoverningDocument` had no `Meta.ordering` and every query in
    view/officer/cnb.py is a bare `.all()`, so document order was whatever the
    database returned. It looked right only because the three rows were inserted
    in reading order and rarely updated. The Foreword makes it certain: it is
    created LAST and must render FIRST.
    """

    def setUp(self):
        cache.clear()
        # Deliberately created in the WRONG order — appendix first, foreword
        # last — because insertion order is exactly what used to leak through.
        make_document('appendix', title='Appendix', display_order=30)
        make_document('bylaws', title='Bylaws', display_order=20)
        make_document('constitution', title='Constitution', display_order=10)
        make_document('foreword', with_article=False, title='Foreword', display_order=0)

    def test_documents_come_back_in_reading_order(self):
        self.assertEqual(
            list(GoverningDocument.objects.values_list('doc_type', flat=True)),
            ['foreword', 'constitution', 'bylaws', 'appendix'],
            'Documents are not in reading order. They were inserted in reverse, '
            'which is the condition that used to determine the output.',
        )

    def test_ordering_survives_an_update(self):
        """
        The specific way the old behaviour broke: on Postgres an UPDATE can
        move a row in the default scan order. Touching the Constitution must not
        move it to the end.
        """
        doc = GoverningDocument.objects.get(doc_type='constitution')
        doc.title = 'Constitution (amended)'
        doc.save()

        self.assertEqual(
            list(GoverningDocument.objects.values_list('doc_type', flat=True)),
            ['foreword', 'constitution', 'bylaws', 'appendix'],
        )


class ForewordStructureTests(TestCase):
    """The prose-only shape, and what follows from it."""

    def setUp(self):
        cache.clear()

    def test_is_prose_only_is_true_without_articles_and_false_with(self):
        foreword = make_document('foreword', with_article=False, title='Foreword')
        constitution = make_document('constitution', title='Constitution')
        self.assertTrue(foreword.is_prose_only)
        self.assertFalse(constitution.is_prose_only)

    def test_the_foreword_has_no_amendable_sections(self):
        """
        Documents the CONSEQUENCE of the prose-only choice so it is a recorded
        decision rather than a surprise: the C&B resolution flow amends Section
        rows, and the Foreword has none, so it cannot be amended by resolution.
        Intended — a foreword is the author's note, not legislation. If that
        ever needs to change, the document has to be restructured first, and
        this test is where someone will find that out.
        """
        foreword = make_document('foreword', with_article=False, title='Foreword')
        self.assertEqual(Section.objects.filter(article__document=foreword).count(), 0)


class ForewordSeedDataTests(TestCase):
    """The seeder, and the way `--update` could have destroyed the text."""

    def setUp(self):
        cache.clear()

    def test_seed_creates_the_foreword_with_prose_and_no_articles(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_cnb_documents', stdout=StringIO(), stderr=StringIO())

        doc = GoverningDocument.objects.get(doc_type='foreword')
        self.assertEqual(doc.display_order, 0)
        self.assertEqual(doc.articles.count(), 0)
        self.assertIn('plant trees', doc.preamble.lower())

    def test_update_does_not_overwrite_edited_foreword_text(self):
        """
        ⚠️ `--update` IS DOCUMENTED AS SAFE, AND IT WAS NOT, FOR THIS DOCUMENT.

        The command's contract is that `--update` refreshes metadata and never
        overwrites edited content. That was written when content only ever lived
        in `Section.content`, and it stopped holding the moment a document
        existed whose text is its preamble. Editing the Foreword in the C&B
        manager and then running a command documented as safe would have thrown
        the edit away with no warning.
        """
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_cnb_documents', stdout=StringIO(), stderr=StringIO())

        doc = GoverningDocument.objects.get(doc_type='foreword')
        doc.preamble = 'Edited by the C&B chair after the vote.'
        doc.save()

        call_command('seed_cnb_documents', '--update', stdout=StringIO(), stderr=StringIO())

        doc.refresh_from_db()
        self.assertEqual(
            doc.preamble, 'Edited by the C&B chair after the vote.',
            '--update overwrote the edited Foreword text. For a prose-only '
            'document the preamble IS the content and needs --force.',
        )

    def test_force_does_overwrite_edited_foreword_text(self):
        """The escape hatch has to work, or the guard above is a trap."""
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_cnb_documents', stdout=StringIO(), stderr=StringIO())

        doc = GoverningDocument.objects.get(doc_type='foreword')
        doc.preamble = 'Scratch text.'
        doc.save()

        call_command(
            'seed_cnb_documents', '--update', '--force',
            stdout=StringIO(), stderr=StringIO(),
        )

        doc.refresh_from_db()
        self.assertIn('plant trees', doc.preamble.lower())

    def test_seeding_does_not_publish(self):
        """
        Seeding and publishing are separate acts, and this asserts they stay
        separate. Running `seed_cnb_documents` on prod must not put unpassed
        governance in front of the chapter.
        """
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_cnb_documents', stdout=StringIO(), stderr=StringIO())
        call_command('seed_feature_flags', stdout=StringIO(), stderr=StringIO())
        cache.clear()

        self.assertNotIn(
            'foreword',
            set(GoverningDocument.enabled().values_list('doc_type', flat=True)),
            'Running the two seed commands published the Foreword. Seeding '
            'stages the text; only the flag adopts it.',
        )


class OfficerAccessToDisabledDocumentsTests(TestCase):
    """
    A document has to be editable BEFORE it is turned on — that is the entire
    workflow the Foreword exists for. Officer management must not be gated by
    the same flags that gate the member view.
    """

    def setUp(self):
        cache.clear()
        self.foreword = make_document(
            'foreword', with_article=False, title='Foreword', preamble='Draft text.',
        )
        FeatureFlag.objects.create(
            name='cnb_foreword', display_name='C&B — Foreword',
            description='', category='documents', is_enabled=False,
        )
        cache.clear()

    def test_the_cnb_dashboard_still_lists_a_disabled_document(self):
        self.assertIn(
            'foreword',
            set(GoverningDocument.objects.values_list('doc_type', flat=True)),
            'The unfiltered manager stopped returning disabled documents. '
            'Officer management depends on it — gating that queryset would make '
            'the Foreword uneditable exactly when it needs editing.',
        )
