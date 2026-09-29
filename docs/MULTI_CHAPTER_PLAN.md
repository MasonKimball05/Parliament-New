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
   - ✅ **C&B text, slice 2b, done 09-27-26:**
     - The C&B text is now chapter content in `chapter_content/<chapter>/cnb.json` or `cnb.py`, chosen by `CHAPTER_CONTENT_DIR`. Alpha Mu's file is `chapter_content/alpha_mu/cnb.py`, moved from `src/management/data/cnb_data.py`.
     - `src/chapter_content.py` loads and validates it. Every problem is listed, and nothing is written if validation fails.
     - A `.py` source is only accepted from inside the repo's `chapter_content/`, because loading one executes it.
     - `seed_cnb_documents --source <file.json>` imports another file.
     - New `export_cnb_documents` writes the database's current C&B as importable JSON. It round-trips, including deactivated sections.
     - Guard: 192 lines in 16 files → 132 in 15.
   - ✅ **Landing and Robert's Rules, slice 2c, done 09-27-26 (migration `0056`):**
     - **Identity uses `{% chapter %}`** in `landing.html`, the landing editor's placeholder and the Robert's Rules headings.
     - **Per-chapter template overrides.** `TEMPLATES['DIRS']` is now `[CHAPTER_CONTENT_DIR/templates, templates/]`, so a chapter overrides a content partial by placing a file with the same name in its own directory.
     - **Landing fallback text.** The fallback shown while the editor's fields are empty lives in `templates/landing/_default_*.html` (generic wording in the chapter's name). The editor's reset-to-default presets live in `_editor_default_*.html`. Alpha Mu's verbatim text is kept in `chapter_content/alpha_mu/templates/landing/`, so nothing changes for Alpha Mu.
     - **Contact location default.** `LandingPageContent.contact_location` now defaults from the chapter (migration `0056`, a state-only change). Alpha Mu's saved row is untouched.
     - **Guards.** `test_template_comments` and `test_csp_templates` now also scan `chapter_content/*/templates/`.
     - **Literals:** 132 lines → 100, in 11 files.
   - ✅ **Archive pages: chapter-specific and hidden (Mason, 09-27-26).** The six `templates/archive/*.html` pages moved to `chapter_content/alpha_mu/templates/archive/`, so no other chapter can load them.
     - They were never reachable anyway: `src/view/archive/` has not been routed since v3.0.0.
     - Literals: 100 lines → 91, in 7 files.
   - ✅ **Leftover literals, slice 3a, done 09-28-26:**
     - The chapter's ratified C&B PDF is chapter content: `chapter_content/<chapter>/reference_documents.json`, loaded and validated by `load_reference_documents()`. Paths must stay inside `MEDIA_ROOT`.
     - Fraternity-wide PDFs (the Code, the Kai binder, Trial by Chapter, Robert's Rules) stay in `view_document.py`. A chapter without its own C&B PDF gets a 404 for that slug, and `/constitution-bylaws/` hides the "Official PDF" link.
     - `seed_resolutions` names the chapter from config, and refuses to run on another chapter, because it seeds the original chapter's real resolution history.
     - The old static `constitution_bylaws.html` and its unrouted view: the template moved to `chapter_content/alpha_mu/templates/archive/`, and the view was deleted.
     - Literals: 91 lines → 66, in 4 files. What's left is the settings defaults (9), the song lyrics (55), the songbook PDF name (1) and one `help_text` in `models/cnb.py` (1). The last one is left alone because changing it needs a migration.
   - ✅ **Template-override guard, slice 3b, done 09-28-26:** `test_chapter_template_overrides` fails if a file in `chapter_content/*/templates/` shadows an app template that isn't on its `OVERRIDABLE` list (today: `landing/_default_*.html` and `landing/_editor_default_*.html`). So a chapter can't quietly fork `base.html` or `two_factor/verify.html`.
   - ✅ **Songs, slice 3d, done 09-28-26.** Mason: *"The ones in the songbook are default for everyone, but each chapter can have their own songs."*
     - The default lyrics moved out of `update_song_lyrics.py` (a 1,000-line dict) into **fraternity** content: `fraternity_content/beta_theta_pi/songs.json`, with categories. `FRATERNITY_CONTENT_DIR` selects it, and `src/fraternity_content.py` loads and validates it.
     - New `seed_default_songs` gives a new chapter the default songbook. It creates missing categories and songs, and never edits or deletes an existing song. It skips the 4 songs whose lyrics are still placeholders.
     - A chapter's own songs are ordinary Song rows added in the app (`/songbook/` → Add song), which already existed.
     - New `export_songs` writes the songbook back out in the same format.
     - Songs the old category map didn't cover are filed under "Other" (17 of 54). They're correct in Alpha Mu's database. Running `export_songs -o fraternity_content/beta_theta_pi/songs.json` on prod refreshes the file with the real categories.
     - Literals: 66 lines → 11, in 3 files. What's left is the settings defaults, the songbook PDF name and one `help_text`.
   - **Houses: shared, no change (Mason, 09-28-26).** They're named for the fraternity's founders, *"they will (should) be the same everywhere"*. They stay as `ParliamentUser.HOUSE_CHOICES`.
   - **Roles and committees (looked at 09-28-26): already data, with code-keyed defaults.**
     - Both are database rows. Roles are created and edited in the app (`manage_roles`), committees in the admin.
     - `Role.DEFAULT_ROLES` and `Committee.DEFAULT_COMMITTEES` are the fraternity's standard exec structure, with nothing chapter-specific. `restore_committees_and_roles` bootstraps them for a new chapter.
     - The constraint is that code keys on their **codes** (`KAI`, `EXEC`, `EDUCATION`, `VPP`, `VPE`, …, about 14 references). So a chapter can rename or add committees and roles, but must keep those codes.
     - Suggested next step, not built: a system check that warns when a required code is missing, pointing at `restore_committees_and_roles`. That would catch a new chapter that deleted "Kai Committee".
   - ✅ **Slice 2a, done 09-27-26:**
     - **PWA.** `manifest.json`, the service worker and the offline page are now rendered by `src/view/pwa.py`, at `/manifest.webmanifest` and `/service-worker.js`.
       - The offline page is embedded in the worker, so there is no offline URL.
       - The static copies were removed.
     - **Pledge classes.** The founding class is `CHAPTER['founding_year']` / `['founding_semester']`, set by the `CHAPTER_FOUNDING_*` env vars.
     - **Weak passwords.** The known-weak list derives the fraternity-name guesses from the chapter.
     - **Comments.** Leftover comments were reworded.
     - **Guard.** 213 lines in 29 files → 192 in 16.
   - ✅ **Pledge-class lettering anchor, slice 3c, done 09-28-26:**
     - Optional `CHAPTER_LETTERING_ANCHOR`, e.g. `Fall 2010 = Xi`. It pins one class to its letter, and every other class is counted from it.
     - Classes between the founders and the first letter get no letter.
     - Badge colors still follow the class index, so they don't change.
     - A malformed anchor is a `src.E001` startup error.
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
