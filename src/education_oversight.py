"""
The Education Oversight Committee (11-16-26).

Some terms the chapter elects no Vice President of Education. It elects an
education committee instead, and the committee elects a chair from among
its members to lead it. Down the line, even mid-term, the chapter may elect
a VP of Education again and end the oversight arrangement. Nothing here
assumes either is permanent:

* **The name follows the role.** While nobody active holds the education
  committee's role (its ``Committee.role``, the VP of Education), the
  committee is called "Education Oversight Committee". When someone takes
  the role it goes back to its usual name (remembered in a SiteSetting the
  first time it's renamed). ``sync_name`` runs whenever that role changes
  hands (src/signals.py) and when a chair election closes.
* **The chair leads it.** The committee's chair already has a chair's
  access to the education dashboard (``_get_education_access``). Becoming
  the education committee's chair also makes a plain member a "Chair"
  member type, which is what opens the officer tools (events, announcements;
  ``user_is_officer_or_chair``), and stepping down reverts that unless they
  still chair something or hold a role (``sync_chair_member_type``, from the
  ``Committee.chairs`` signal). Only the education committee does this, so
  other committees' chairs are unchanged.
* **The committee elects its chair** in the app (src/committee_chair_election.py);
  an officer can still set the chair by hand on Manage committees.
"""
import logging

logger = logging.getLogger(__name__)

OVERSIGHT_NAME = 'Education Oversight Committee'
_NAME_SETTING = 'education_committee_usual_name'


def education_committees():
    from src.models import Committee
    return Committee.objects.filter(is_education_committee=True)


def role_holder(committee):
    """The active member holding the committee's role (the VP of Education), or None."""
    from src.models import ParliamentUser
    if not committee.role_id:
        return None
    return (ParliamentUser.objects.filter(roles=committee.role_id, is_active=True, member_status='Active')
            .order_by('name').first())


def in_oversight(committee):
    """True while the education committee has no VP of Education to lead it."""
    return bool(committee.is_education_committee) and role_holder(committee) is None


def _usual_name(committee):
    from src.models_feature_flags import SiteSetting
    row = SiteSetting.objects.filter(key=_NAME_SETTING).first()
    return row.value if row and row.value else None


def _remember_usual_name(name):
    from src.models_feature_flags import SiteSetting
    SiteSetting.objects.update_or_create(
        key=_NAME_SETTING,
        defaults={'value': name, 'default_value': name,
                  'description': 'The education committee\'s name when it has a VP of Education '
                                 '(src/education_oversight.py).'})
    SiteSetting.invalidate_cache(_NAME_SETTING)


def sync_name(committee):
    """Rename the committee for oversight, or back. Returns True if the name changed."""
    if not committee.is_education_committee:
        return False
    if in_oversight(committee):
        if committee.name == OVERSIGHT_NAME:
            return False
        _remember_usual_name(committee.name)
        new = OVERSIGHT_NAME
    else:
        usual = _usual_name(committee)
        if committee.name != OVERSIGHT_NAME or not usual:
            return False
        new = usual
    from src.models import ActivityLog
    old = committee.name
    committee.name = new
    committee.save(update_fields=['name'])
    ActivityLog.log_activity(
        action_type='other',
        description=f'Committee renamed from "{old}" to "{new}" '
                    f'({"no" if new == OVERSIGHT_NAME else "a"} Vice President of Education)')
    logger.info('[education_oversight] %s → %s', old, new)
    return True


def sync_names_for_roles(role_pks):
    for committee in education_committees().filter(role__pk__in=list(role_pks)):
        sync_name(committee)


def sync_chair_member_type(user):
    """A plain member who chairs the education committee is a Chair; stepping down reverts it."""
    from src.models import Committee
    chairs_education = Committee.objects.filter(is_education_committee=True, chairs=user).exists()
    if chairs_education and user.member_type == 'Member':
        user.member_type = 'Chair'
        user.save(update_fields=['member_type'])
        logger.info('[education_oversight] %s is a Chair (education committee chair)', user.pk)
    elif (not chairs_education and user.member_type == 'Chair'
          and not user.roles.exists() and not Committee.objects.filter(chairs=user).exists()):
        user.member_type = 'Member'
        user.save(update_fields=['member_type'])
        logger.info('[education_oversight] %s is a Member again (no longer chairs anything)', user.pk)
