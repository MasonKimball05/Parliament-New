"""
11-09-26 (secret ballot, after the election): SlatingVote rows no longer line
up with the SlatingBallot written in the same request.

* ``voted_at`` is dropped. It matched the ballot's timestamp to the
  microsecond, so a join on time named every voter's choices.
* The integer ``id`` becomes a random UUID. Sequential ids put votes in the
  order ballots were cast, so the Nth vote of a round was the Nth ballot.
  Existing rows get fresh random ids, and the table is then rewritten in id
  order (CLUSTER) so its physical order no longer follows casting order
  either.

Nothing points at SlatingVote, so the key can be swapped in place. The
database side is raw SQL (Postgres can't cast an integer column to uuid);
the state side is the matching AlterField. Other databases (SQLite in
one infra test) use Django's table rebuild instead.
"""
import uuid

from django.db import migrations, models


POSTGRES_FORWARD = [
    'ALTER TABLE src_slatingvote ADD COLUMN new_id uuid',
    # (filled with random ids in between, see swap_to_uuid)
    'ALTER TABLE src_slatingvote DROP COLUMN id',
    'ALTER TABLE src_slatingvote RENAME COLUMN new_id TO id',
    'ALTER TABLE src_slatingvote ALTER COLUMN id SET NOT NULL',
    'ALTER TABLE src_slatingvote ADD CONSTRAINT src_slatingvote_pkey PRIMARY KEY (id)',
    'CLUSTER src_slatingvote USING src_slatingvote_pkey',
]
# Backwards: fresh sequential ids (the original order is gone for good, which
# is the point).
POSTGRES_BACKWARD = [
    'ALTER TABLE src_slatingvote ADD COLUMN old_id bigserial',
    'ALTER TABLE src_slatingvote DROP COLUMN id',
    'ALTER TABLE src_slatingvote RENAME COLUMN old_id TO id',
    'ALTER TABLE src_slatingvote ADD CONSTRAINT src_slatingvote_pkey PRIMARY KEY (id)',
]


def _uuid_field(model):
    field = models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)
    field.set_attributes_from_name('id')
    field.model = model
    return field


def _auto_field(model):
    field = models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')
    field.set_attributes_from_name('id')
    field.model = model
    return field


def swap_to_uuid(apps, schema_editor):
    Vote = apps.get_model('src', 'SlatingVote')
    if schema_editor.connection.vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(POSTGRES_FORWARD[0])
            cursor.execute('SELECT id FROM src_slatingvote')
            for (old_id,) in cursor.fetchall():
                cursor.execute('UPDATE src_slatingvote SET new_id = %s WHERE id = %s', [str(uuid.uuid4()), old_id])
            for sql in POSTGRES_FORWARD[1:]:
                cursor.execute(sql)
        return
    # SQLite and the rest: Django rebuilds the table with the new key type
    # (copying the old numbers across as text), then each row gets a random id.
    schema_editor.alter_field(Vote, Vote._meta.get_field('id'), _uuid_field(Vote))
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT id FROM src_slatingvote')
        for (old_id,) in cursor.fetchall():
            cursor.execute('UPDATE src_slatingvote SET id = %s WHERE id = %s', [uuid.uuid4().hex, old_id])


def swap_to_sequence(apps, schema_editor):
    Vote = apps.get_model('src', 'SlatingVote')
    if schema_editor.connection.vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            for sql in POSTGRES_BACKWARD:
                cursor.execute(sql)
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT id FROM src_slatingvote')
        for n, (old_id,) in enumerate(cursor.fetchall(), start=1):
            cursor.execute('UPDATE src_slatingvote SET id = %s WHERE id = %s', [str(n), old_id])
    schema_editor.alter_field(Vote, _uuid_field(Vote), _auto_field(Vote))


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0077_groupme_message_delete'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='slatingvote',
            options={'ordering': ['vote_hash']},
        ),
        migrations.RemoveField(
            model_name='slatingvote',
            name='voted_at',
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(swap_to_uuid, swap_to_sequence),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='slatingvote',
                    name='id',
                    field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
                ),
            ],
        ),
    ]
