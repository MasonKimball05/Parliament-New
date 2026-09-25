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

## Decisions so far (Mason, 09-25-26)

- **Scope: Beta Theta Pi chapters only**, for now. Kai, the songs, the exec roles and the houses can stay as shared Beta concepts, configurable per chapter where needed.
- **One person belongs to exactly one chapter.** A **transfer** is requested by the member (or the old chapter) and must be **approved by the receiving chapter**, which can also decline.
  - This needs both chapters in one system, which **rules out option C (separate deployments)** as the end state.
  - C can still be a short pilot, but a transfer between two separate deployments would be an export and re-import, not a button.
- **The platform-owner pin is one account, not "user 73".** ✅ Done: `is_platform_owner()`, `PLATFORM_OWNER_USER_ID` / `PLATFORM_OWNER_EMAIL`, and the `src.W004` check.
- **Still open:** A versus B, and the operator-visibility model. Proposed below.

## A versus B: what running each looks like

| | A. Shared schema, `chapter` FK | B. Schema per chapter (`django-tenants`) |
|---|---|---|
| **New chapter** | Insert a `Chapter` row, then run a seeding command | Create a tenant (a new Postgres schema), which runs **every migration** into it; then seed |
| **Deploying a release** | `migrate` once | `migrate_schemas`: every migration × every chapter. Fine at 10 chapters, slow at 200 |
| **Local dev and tests** | SQLite still works; the pre-push hook is unchanged | Postgres required locally and in the hook, and tests are slower (schema setup) |
| **Where users live** | One `ParliamentUser` table with a `chapter` FK | Either per schema (a transfer copies the person between schemas), or in the shared schema with a chapter FK. The shared-schema version is a hybrid that brings back A's filtering for users |
| **Transfer** | Change `user.chapter` after approval. Their old votes and attendance stay with the old chapter by FK | Copy the account into the new schema, deactivate the old one, and keep a link. Two histories, one person |
| **Isolation** | A forgotten `.filter(chapter=…)` leaks data. Needs scoped managers plus a guard test that enumerates every query path | Enforced by Postgres: a query simply cannot see another chapter's tables |
| **Kai** | Leak-prone. 34 "the one" lookups become "the one for this chapter" | Isolated automatically; lookups stay as they are |
| **Cross-chapter features** (transfers, province and national dashboards) | Easy, one query | Harder: loop over schemas, or copy data into the shared schema |
| **Backups and restores** | One database. Restoring a single chapter is awkward | Per-schema `pg_dump -n` makes a one-chapter restore easy |
| **Hosting** | One app, one database | Same, plus wildcard DNS/TLS for subdomains; A needs this too if you use subdomains |

**With your answer to (3), I now lean toward A, done carefully.** Transfers make users and cross-chapter awareness first-class, and that is A's strength and B's awkward spot. The isolation risk in A is real but manageable with three pieces:

1. A `ChapterScopedManager` that **raises** when it is queried with no chapter in context. Forgetting the filter then crashes a test instead of leaking data.
2. A guard test that enumerates every ROOT model and fails if it lacks the manager. This is the same "enumerate the population" pattern as `test_singleton_rows`.
3. Keeping Kai on its own chapter-checked access path (`_get_kai_access` already exists), plus a cross-chapter Kai test in the style of the admin confidentiality tests.

## Transfer design (sketch)

- **`ChapterTransfer`** fields: `member`, `from_chapter`, `to_chapter`, `requested_by`, `status` (pending / approved / declined / cancelled), `reason`, decision fields (`decided_by`, `decided_at`, `note`).
- **Who can request:** the member, or an officer of the old chapter. **Who approves:** an officer of the receiving chapter (probably the President or the VP handling membership). Declining needs a note.
- **On approval, in one transaction:**
  - move the membership;
  - end the member's roles and committee seats in the old chapter;
  - write an `ActivityLog` entry in **both** chapters.
  - **Nothing historical moves:** votes, attendance, service hours and Kai records stay with the chapter where they happened.
- **Kai:** the receiving chapter **never** sees the old chapter's Kai records about the member. This fits the confidentiality boundary. Whether the old chapter may attach a note, and whether an open Kai case blocks a transfer, is a decision for later.
- **Rules to decide:**
  - Can a pledge transfer?
  - Can a member with dues or fines outstanding transfer?
  - Should a request expire after N days?

## Operator (you) visibility: what similar products do

Products like this (multi-tenant software holding sensitive member data: HR systems, school platforms, church management software) usually settle on these rules:

- **Default: the operator sees metadata, not content.** You would see:
  - that a chapter exists, its member *count*, its storage and activity volume;
  - system health and error rates;
  - billing, if you ever charge.
  You would not see a chapter's members, votes, minutes or Kai data just by being the host.
- **Support access is granted per incident and time-limited.** A chapter admin clicks "Grant support access for 24h", or you request it and they approve. Everything you do during that window is logged and **visible to that chapter's admins**. You already have this pattern in `KaiBreakGlassGrant`; this generalises it.
- **Impersonation is off by default.** If it exists, it needs the chapter's approval, shows a visible banner, and appears in the chapter's own audit log. You have impersonation today, so it would need re-scoping.
- **Kai is never included in support access.** Access to Kai data stays with in-app grants, exactly as the admin confidentiality boundary says today.
- **A written data-handling policy** ("what the operator can and can't see") shown to chapters at signup. It is cheap to write, and it's what convinces a skeptical chapter President.
- **Per-chapter data export and deletion.** A chapter can download its data, and deleting a chapter removes it. That is standard, and it makes trust easier.
- **Platform-level roles kept separate from chapter roles.** `is_platform_owner` is yours; a chapter's "admin" means admin *of that chapter* only. Django `/admin/` becomes platform-only, and chapter admins use in-app screens.

Everything in this list matches how Parliament already treats confidentiality. The same boundary moves up one level, from admin-versus-Kai to operator-versus-chapter.

## Things that become newly risky with more than one chapter

- **Hardcoded user id `'73'`.** This covers `bug_admin_required`, the feedback admin, `PROTECTED_ADMIN_USER_ID` in `manage_members.py`, and three `home*.html` templates.
  - These are intentional, and CLAUDE.md says not to re-flag them. **But once there is a second chapter they become a real problem:** that chapter's own member `73` would pass every one of those checks.
  - ✅ **Fixed 09-25-26:** everything goes through `src.permissions.is_platform_owner()` and `{% if user|is_platform_owner %}`. The id comes from `PLATFORM_OWNER_USER_ID` (default `'73'`), and an optional `PLATFORM_OWNER_EMAIL` second factor must also match. `src.W004` warns when a deployment with its own `CHAPTER_DOMAIN` is still on the default. A guard test forbids the `user_id == '73'` literal from coming back.
- **Bug and feedback reports fall back to your personal email** (`bug_report.py:351`, `feedback.py:367`). That is fine for Alpha Mu, but another chapter's bug reports would reach you.
- **Timezone.** `TIME_ZONE` is Central, and five places hard-code `America/Chicago` directly, as do the 3 AM crontabs.

## Decisions still needed

1. **A versus B.** I lean A, given single membership plus transfers; see the table above.
2. **The operator-visibility model.** Adopt the proposal above as written, or adjust it.
3. **Transfer rules:** pledges, outstanding dues, request expiry, and whether an open Kai case blocks a transfer.
