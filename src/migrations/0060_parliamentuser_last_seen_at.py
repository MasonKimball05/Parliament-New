# v3.47.0 — ParliamentUser.last_seen_at, with a one-time backfill.

from django.db import migrations, models
from django.db.models import Max


def backfill(apps, schema_editor):
    """
    Start every member from the best evidence already on file: the newest of
    their tracked sessions' `last_activity` and their `last_login`. Without
    this, everyone shows "Never" until their next visit. Session rows are
    pruned after 30 days and on logout, so `last_login` covers the rest.
    """
    User = apps.get_model('src', 'ParliamentUser')
    UserSession = apps.get_model('src', 'UserSession')
    latest_session = dict(
        UserSession.objects.values('user_id')
        .annotate(latest=Max('last_activity'))
        .values_list('user_id', 'latest')
    )
    for user in User.objects.only('pk', 'last_login').iterator():
        candidates = [t for t in (user.last_login, latest_session.get(user.pk)) if t]
        if candidates:
            User.objects.filter(pk=user.pk).update(last_seen_at=max(candidates))


class Migration(migrations.Migration):

    dependencies = [
        ("src", "0059_pledge_phase"),
    ]

    operations = [
        migrations.AddField(
            model_name="parliamentuser",
            name="last_seen_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Last time this member used the site while signed in (to within 5 minutes).",
                null=True,
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
