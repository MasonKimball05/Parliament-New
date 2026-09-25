"""
Chapter identity — who this deployment is for (multi-chapter phase 1, 09-25-26).

WHY: the chapter's name, school, domain, crest and colours were literals in
~200 template lines and ~55 Python lines ("Alpha Mu Parliament", "Beta Theta
Pi - Samford Chapter", am-parliament.org, am-coat-of-arms.png, #003DA5 …).
Supporting a second chapter — whether as its own deployment or as a tenant of
a shared one — starts with those facts having exactly one home.

HOW TO USE
  Python:     from src.chapter import get_chapter
              get_chapter().site_name        # 'Alpha Mu Parliament'
  Templates:  {% chapter "site_name" %}      # builtin tag, no {% load %};
                                             # works in render_to_string emails
              {% chapter "crest_url" %}      # static URL of the crest
              {% chapter "full_name" as n %} # assign form

WHERE THE VALUES COME FROM
  `settings.CHAPTER` (built from CHAPTER_* env vars, Alpha Mu defaults).
  `get_chapter(request=None)` takes the request on purpose: once the app is
  multi-tenant it will resolve the chapter from the request (subdomain /
  tenant) instead of settings, and no call site has to change.

`src/tests/guards/test_chapter_identity_literals.py` counts the literals that
are left, per file, so they can only go down.
"""
from dataclasses import dataclass, fields
from functools import lru_cache

from django.conf import settings


@dataclass(frozen=True)
class ChapterIdentity:
    fraternity: str            # 'Beta Theta Pi'
    chapter_name: str          # 'Alpha Mu'
    school: str                # 'Samford University'
    school_short: str          # 'Samford'
    city: str                  # 'Birmingham'
    state: str                 # 'Alabama'
    site_name: str             # 'Alpha Mu Parliament' — email headers/sign-offs
    domain: str                # 'am-parliament.org'
    crest: str                 # static path, 'images/am-coat-of-arms.png'
    primary_color: str         # '#003DA5'
    secondary_color: str       # '#FFC72C'
    motto: str                 # 'Virtue Stands Alone'
    school_email_domain: str   # 'samford.edu' — hint on email forms

    # --- derived -----------------------------------------------------------
    @property
    def full_name(self):
        """'Alpha Mu Chapter of Beta Theta Pi'"""
        return f'{self.chapter_name} Chapter of {self.fraternity}'

    @property
    def school_chapter_name(self):
        """'Beta Theta Pi - Samford Chapter' (how Kai letters sign off)"""
        return f'{self.fraternity} - {self.school_short} Chapter'

    @property
    def formal_name(self):
        """'The Samford Chapter, the Alpha Mu of Beta Theta Pi' (C&B / resolution wording)"""
        return f'The {self.school_short} Chapter, the {self.chapter_name} of {self.fraternity}'

    @property
    def long_name(self):
        """'Beta Theta Pi — Alpha Mu at Samford University'"""
        return f'{self.fraternity} — {self.chapter_name} at {self.school}'

    @property
    def location(self):
        """'Birmingham, Alabama'"""
        return ', '.join(p for p in (self.city, self.state) if p)

    @property
    def signoff(self):
        """'— Alpha Mu Parliament' (plain-text email sign-off)"""
        return f'— {self.site_name}'

    @property
    def crest_url(self):
        from django.templatetags.static import static
        return static(self.crest)

    @property
    def crest_alt(self):
        return f'{self.chapter_name} Coat of Arms'

    @property
    def site_url(self):
        return getattr(settings, 'SITE_URL', '') or f'https://{self.domain}'

    @property
    def noreply_email(self):
        return f'noreply@{self.domain}'

    @property
    def calendar_prodid(self):
        """iCal PRODID. Unchanged for Alpha Mu (subscribers key on it)."""
        return f'-//Parliament Chapter Calendar//{self.domain}//'

    def calendar_uid(self, event_id):
        """iCal UID for an event. Must stay stable, or subscribed calendars duplicate events."""
        return f'event-{event_id}@{self.domain}'


FIELD_NAMES = frozenset(f.name for f in fields(ChapterIdentity))
PUBLIC_ATTRS = FIELD_NAMES | {
    'full_name', 'formal_name', 'school_chapter_name', 'long_name', 'location', 'signoff',
    'crest_url', 'crest_alt', 'site_url', 'noreply_email', 'calendar_prodid',
}


@lru_cache(maxsize=1)
def _from_settings(frozen_items):
    return ChapterIdentity(**dict(frozen_items))


def get_chapter(request=None):
    """The chapter this request/deployment belongs to. See module docstring."""
    cfg = getattr(settings, 'CHAPTER', {}) or {}
    unknown = set(cfg) - FIELD_NAMES
    if unknown:
        raise ValueError(f'settings.CHAPTER has unknown keys: {sorted(unknown)}')
    return _from_settings(tuple(sorted(cfg.items())))
