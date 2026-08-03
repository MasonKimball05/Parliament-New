# Generated for v3.18.2.
#
# One new model, `KaiBreakGlassGrant`, and nothing else — no field changes, no
# data migration, no backfill. `CreateModel` on an empty table takes no lock of
# consequence on PostgreSQL and is instant.
#
# WHY THE MODEL EXISTS (short version — the long one is its docstring):
# `_get_kai_access()` used to open with `if user.is_admin or ...: return
# {everything: True}`, so one boolean on the user row granted every Kai
# permission including both party-identity flags, with no `KaiMemberPermission`
# anywhere. That contradicted the standing v3.16.2 rule ("being an admin is an
# operational role, not a grant of judicial access") and contradicted
# `_is_kai_chair`'s own argument, added ten lines above it one release earlier.
#
# `is_admin` no longer grants Kai access. This model is the way back in when
# it is genuinely needed: time-boxed, reason-required, audited at both ends,
# grantable only from a shell via `manage.py kai_break_glass`, and visible as a
# banner while it is live.
#
# ⚠️ DO NOT REGISTER THIS MODEL IN `/admin/`. An editable admin for it would
# let an admin grant themselves the access it exists to withhold — precisely
# the `KaiMemberPermissionAdmin` edge v3.16.2 removed. It is deliberately
# absent from `admin_extra.py` and `admin_sections.py`, and that gap is
# intentional in the same way the seven Kai models' is.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0012_kai_case_number_unique_and_recusal_reason'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='KaiBreakGlassGrant',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('reason', models.TextField(
                    help_text='Why this grant was necessary. Required — it is the audit trail.',
                )),
                ('granted_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(
                    help_text='After this moment the grant confers nothing.',
                )),
                ('revoked_at', models.DateTimeField(
                    blank=True, null=True,
                    help_text='Set when revoked early. A revoked grant is inert immediately.',
                )),
                ('granted_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='kai_break_glass_grants_issued',
                    to=settings.AUTH_USER_MODEL,
                    help_text='Who ran the command. Null if granted from a system shell.',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kai_break_glass_grants',
                    to=settings.AUTH_USER_MODEL,
                    help_text='The admin receiving temporary full Kai access.',
                )),
            ],
            options={
                'verbose_name': 'Kai Break-Glass Grant',
                'verbose_name_plural': 'Kai Break-Glass Grants',
                'ordering': ['-granted_at'],
            },
        ),
        migrations.AddIndex(
            model_name='kaibreakglassgrant',
            index=models.Index(
                fields=['user', 'expires_at'], name='kai_bg_user_expiry_idx',
            ),
        ),
    ]
