# Multi-chapter support: inventory, options, plan

*Started 09-25-26 on the `multi-chapter` branch. The branching and guard rules are in `docs/MULTI_CHAPTER_BRANCHING.md`.*

## Where the code stands (measured 09-25-26)

- **150 models.** Classified by how each one would reach a chapter:

  | Class | Count | Meaning |
  |---|---|---|
  | ROOT | 38 | Needs its own chapter link, e.g. `Committee`, `Event`, `Legislation`, `KaiReport`, `ParliamentUser` |
  | CHILD | 64 | Reaches a ROOT through a foreign key, e.g. `Vote → Legislation` |
  | USER-SCOPED | 29 | Hangs off one user |
  | GLOBAL | 11 | Platform-wide, e.g. IP black/whitelists, CSP reports, honeypot |
  | UNSURE | 8 | `Role`, `FeatureFlag`, `PageToggle`, `SiteSetting`, `SystemLockdown`, `RoleKnowledgeBase`, `APIAccessLog`, `NotificationLog` |

- **Uniqueness that is global today and would collide between chapters:**
  - `Committee.name` / `.code` / `.committee_id`
  - `Role.name` / `.code`
  - `ParliamentUser.username` / `.email` / `.role_number`
  - `GoverningDocument.doc_type`
  - `ChapterFolder.name`, `DocumentTag.name`
  - `PledgePageRestriction.url_name`
  - `KaiFormField.field_name`, `ServiceFormField.field_name`
  - `SongCategory.name`
  - `FeatureFlag.name`, `PageToggle.url_name`, `SiteSetting.key`
  - **Kai case and commendation numbers** (`KAI-YYYY-NNN`, `COM-YYYY-NNN`), which come from one global sequence.
- **"The one" lookups:**
  - 34 of the form `Committee.objects.get(is_kai_committee=True)`, plus similar lookups for the education, recruitment, slating, exec and chapter committees.
  - About 21 Role-by-code lookups.
  - 2 `get_instance()` singletons: `LandingPageContent` and `SystemLockdown`.
- **Global state that has no chapter in it:**
  - 24 Celery beat schedules, all in one timezone. Several loop over "all active users".
  - About 16 cache keys with no chapter in the key, e.g. `feature_flag:{name}`, `landing_page_content`, `system_lockdown_instance`.
- **Hardcoded identity:** 324 lines in 76 files named Alpha Mu, Beta Theta Pi, Samford, am-parliament.org or the crest. **Phase 1 (below) brought this to 213 lines in 29 files.**
- **Beta-specific concepts, not just names:** Kai, the songbook and "Wooglin", the ten default exec roles (VPP, VPRM, …), the eight houses, `FOUNDING_YEAR = 2022` and the pledge-class lettering.

## The architecture choice (yours to make)

**A. Shared schema: a `chapter` foreign key on every ROOT model**
- ✅ One database and one deploy. Cross-chapter features are easy: a province dashboard, member transfers, national reporting.
- ✅ Works on SQLite, so your local tests and pre-push hook stay as they are.
- ❌ Every query must filter by chapter, forever. A forgotten `.filter(chapter=…)` leaks one chapter's data to another. Kai case data is the worst place for that to happen, and there are already 34 Kai "the one" lookups to convert.
- ❌ The biggest code change: 38 ROOT models, 13+ uniqueness constraints to rescope, and scoped managers everywhere.

**B. Schema per chapter (`django-tenants`, PostgreSQL schemas)**
- ✅ Each chapter's tables live in their own schema, so isolation is enforced by the database rather than by remembering a filter. The Kai confidentiality boundary extends naturally, including to Django admin.
- ✅ Almost no model changes. The unique constraints stay as they are, per schema.
- ❌ **PostgreSQL only.** Local dev, the test suite and the pre-push hook would all need Postgres instead of SQLite (CI already uses Postgres).
- ❌ Migrations run once per schema. Celery tasks have to loop over tenants. Cross-chapter features need extra work.
- ❌ One person in two chapters means two accounts.

**C. A separate deployment per chapter (same code, own server, database and domain)**
- ✅ Total isolation, and almost no code change beyond what phase 1 and phase 2 already do.
- ✅ The fastest way to get a second chapter onto it, which is what proves people will buy in.
- ❌ Operations cost grows with every chapter: servers, updates, backups, Celery and certificates each time. There are no cross-chapter features.

**My recommendation:**
- Do phase 1 and phase 2, which every option needs anyway.
- Pilot the second chapter as option C.
- Decide between A and B when you know how many chapters there will be and whether cross-chapter features matter.
- If it becomes truly multi-tenant, **I'd lean toward B.** The reason is Kai: database-level isolation is a much stronger guarantee than a filter that has to be right in every query. The cost is giving up SQLite locally.

## Phases

1. **Chapter identity in one place. ✅ Done on this branch (09-25-26).**
   - New `src/chapter.py` holds it: `get_chapter()` in Python, `{% chapter "field" %}` in templates. The tag is a builtin, so it also works in emails.
   - Values come from `settings.CHAPTER` (`CHAPTER_*` env vars). The defaults are Alpha Mu's, so behaviour is unchanged.
   - Migrated: emails, Kai letters, calendar feed identifiers (unchanged values), the crest image (38 places) and its alt text (26), motto, school-email hints, C&B and resolution wording, songbook headings, and the developer API examples.
   - A guard test, `test_chapter_identity_literals`, counts the literals left per file. The counts can only go down.
2. **Chapter content into the database or config.** This is what's left in the guard's list:
   - C&B text: `cnb_data.py` becomes a per-chapter import rather than hardcoded seed data.
   - The landing, archive and Robert's Rules pages.
   - Songs.
   - Default roles, committees and houses as editable data.
   - `FOUNDING_YEAR` and pledge-class lettering.
   - `manifest.json`, the service worker and the offline page, served by a view so they can use the chapter's name and crest.
3. **Pilot chapter** as its own deployment (option C). Write down everything that was painful.
4. **Tenancy (A or B),** if the pilot says it's worth it.

## Things that become newly risky with more than one chapter

- **Hardcoded user id `'73'`.** This covers `bug_admin_required`, the feedback admin, `PROTECTED_ADMIN_USER_ID` in `manage_members.py`, and three `home*.html` templates.
  - These are intentional, and CLAUDE.md says not to re-flag them. **But once there is a second chapter they become a real problem:** that chapter's own member `73` would pass every one of those checks.
  - The pin-by-id intent can be kept by reading the id from a setting per deployment, e.g. `PLATFORM_ADMIN_USER_ID`, with `'73'` as the default for Alpha Mu. **Not changed yet — your call.**
- **Bug and feedback reports fall back to your personal email** (`bug_report.py:351`, `feedback.py:367`). That is fine for Alpha Mu, but another chapter's bug reports would reach you.
- **Timezone.** `TIME_ZONE` is Central, and five places hard-code `America/Chicago` directly, as do the 3 AM crontabs.

## Decisions needed from you

1. **Hosting model:** are you the platform host for many chapters, or is this a codebase other chapters deploy themselves? That decides A/B versus C.
2. **Beta only, or any Greek organisation?** Kai, the songs, the exec roles and the houses are Beta concepts. "Beta chapters only" keeps the scope sane.
3. **Can one person belong to more than one chapter,** for transfers or alumni? This matters a lot for A versus B.
4. **Platform operator versus chapter admin:** what can you, as host, see in another chapter? The admin confidentiality rule suggests "nothing Kai", enforced the same way it is today.
5. **The `'73'` pins:** should they move to a per-deployment setting?
