"""
v3.19.1 — Foreword document type + explicit ordering for governing documents.

Three changes, one of which is a latent bug fix rather than a feature:

1. `doc_type` gains a 'foreword' choice.
2. `GoverningDocument` gains `display_order`, and `Meta.ordering` starts using
   it. **The model previously had no ordering at all** and every query in
   view/officer/cnb.py is a bare `.all()`, so document order on the viewer was
   whatever the database felt like returning. It looked correct only because
   the three rows were inserted in reading order and rarely updated. A Foreword
   makes that certain to break — it is created last and must render first.
3. A data step numbering the existing rows, so the fix applies to databases
   that already have documents rather than only to fresh installs.

Reversible: the data step's reverse is a no-op (dropping the column discards
the values anyway), which keeps `migrate src 0014` working.
"""

from django.db import migrations, models


#: Gaps of 10 so a document can be slotted in without renumbering everything.
DEFAULT_ORDER = {
    'foreword': 0,
    'constitution': 10,
    'bylaws': 20,
    'appendix': 30,
}


def set_initial_display_order(apps, schema_editor):
    """
    Number the documents that already exist.

    Uses `apps.get_model` rather than a direct import — the historical model is
    the only one guaranteed to match the schema at this point in the graph.

    Anything not in the map keeps 0 and sorts first by `doc_type` alphabetically
    via the model's secondary ordering key. That is a deliberate fail-visible
    choice: a stray document type ends up somewhere obvious rather than being
    silently hidden.
    """
    GoverningDocument = apps.get_model('src', 'GoverningDocument')
    for doc_type, order in DEFAULT_ORDER.items():
        GoverningDocument.objects.filter(doc_type=doc_type).update(display_order=order)


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op: RemoveField discards the values regardless."""


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0014_legislation_drafts'),
    ]

    operations = [
        migrations.AddField(
            model_name='governingdocument',
            name='display_order',
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    'Order this document appears in the viewer and table of contents. '
                    'Foreword 0, Constitution 10, Bylaws 20, Appendix 30 — gaps left so '
                    'a document can be inserted without renumbering.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='governingdocument',
            name='doc_type',
            field=models.CharField(
                choices=[
                    ('foreword', 'Foreword'),
                    ('constitution', 'Constitution'),
                    ('bylaws', 'Bylaws'),
                    ('appendix', 'Appendix'),
                ],
                max_length=20,
                unique=True,
            ),
        ),
        migrations.AlterModelOptions(
            name='governingdocument',
            options={
                'ordering': ['display_order', 'doc_type'],
                'verbose_name': 'Governing Document',
                'verbose_name_plural': 'Governing Documents',
            },
        ),
        migrations.RunPython(set_initial_display_order, noop_reverse),
    ]
