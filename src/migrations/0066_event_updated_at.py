"""
v4.0.0 — Event.updated_at.

Added nullable, backfilled to created_at (so no existing event looks edited
and subscribed calendars don't all refresh at once), then made auto_now.
"""
from django.db import migrations, models
from django.db.models import F


def backfill(apps, schema_editor):
    Event = apps.get_model('src', 'Event')
    Event.objects.filter(updated_at__isnull=True).update(updated_at=F('created_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0065_mobile_app'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='updated_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='event',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
