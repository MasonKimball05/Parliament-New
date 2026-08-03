# Confidentiality matrix

**One row per confidential field. One column per surface that can emit it.**

## Why this file exists

Parliament makes several confidentiality promises — Kai allegations are shown
only to reviewers holding the right grant, ballots are anonymous, slating notes
are destroyed after minutes approval. Each promise is enforced separately on each
surface, and **a promise is only as strong as its weakest surface.**

The pattern that keeps happening is not that someone forgets the rule. It is that
someone applies the rule *correctly, once*, and nobody checks the sibling:

| Release | The control | The copy that was missed |
|---|---|---|
| v3.16.2 | Field-level redaction on admin detail pages | `export_as_csv` dumped every `_meta.field` regardless |
| v3.16.3 | CSV export redacted the Kai description column | The list view and export still **filtered** on it — a redaction oracle |
| v3.17.7 | `export_kai_reports_csv` redacted three fields | `bulk_actions_kai_reports` wrote the same thirteen columns raw, 1,100 lines below, reachable from a dropdown |
| v3.18.1 | Every surface above honoured the two identity flags | **The activity feed never had.** Four copies of it — case detail, the v3.18.0 timeline partial, the print view, and a cross-case panel on the list page — printed `entry.user.name`, and the author of a `created` entry *is* the submitter |
| v3.18.2 | The Kai activity feed, redacted across all five of its renderers | **The site-wide `ActivityLog` was a second activity model** — not a Kai model, not in `src/models/kai.py`, not rendered by a `templates/kai/` file, so no enumeration contained it. It stored both identities in a `TextField` called `description` and in the row's own author FK, readable by every officer and chair, one filter chip away |

Four releases, one shape. **This table is the checklist that makes the second
copy visible before it ships.** When you touch a confidential field, read across
its row.

> ## ⚠️ v3.18.1 — this file was wrong on the day it shipped, in both ways it can be
>
> The nightly review found two defects in the v3.18.0 version of this document,
> and they are the two failure modes the document itself is subject to:
>
> 1. **A wrong cell.** `KaiReport.submitted_by` → *Filter/search* read
>    "✅ `_kai_search_q`". It was not: v3.18.0 changed the reviewer list from
>    *excluding* a case the viewer is the accused on to *showing it redacted*,
>    and the search predicate still matched that row on `description` and
>    `submitted_by__name`. The box was an oracle over both.
> 2. **A missing column.** There was no *Activity* surface, so the activity
>    feed — which emits both identities, in four places — could not be wrong in
>    this table because it was not in it.
>
> **The lesson is about the artefact, not the bug.** A matrix whose columns come
> from memory inherits the blind spot it was built to remove. The surfaces below
> are now derived from a grep, and `test_kai_redaction_surfaces.py` fails if a
> Kai template renders a raw activity field. Do the same for any surface added
> here: *if the row cannot be checked by a test, it will eventually be a lie.*
>
> **And one new rule, which is the general form of defect 1:** *when a surface
> stops EXCLUDING a row and starts REDACTING it, every predicate that touches
> that row becomes a disclosure.* Exclusion protects the filters for free.
> Redaction does not.

## How to read it

| Symbol | Meaning |
|---|---|
| ✅ | Exposed, and correctly gated or redacted on this surface |
| ➖ | Not reachable on this surface at all — the strongest state |
| ⚠️ | Exposed with a gap. See the findings section. |
| n/a | Surface does not apply to this model |

**Surfaces**, left to right — this order is roughly "most looked at" to "least",
which is also roughly the order in which gaps are found:

1. **In-app** — the detail/list view a member actually uses
2. **Filter / search** — *a filter predicate is a join key.* If a user can filter
   or search on a redacted field and see which rows match, the redaction leaks.
   (v3.16.3)
3. **CSV** — the app's own export
4. **Bulk** — a second export: a bulk action, a `?export=` mode, a print view
5. **Admin** — the Django admin detail page
6. **Admin CSV** — the `export_as_csv` action. **Only `exclude` protects this.**
   A field hidden by omitting it from `fieldsets` is still exported. (v3.16.2)
7. **API** — `/api/v1/`
8. **Search** — global search
9. **Dev rows** — dev mode's SQL row inspector
10. **Case activity** — `KaiReportActivity`, the per-case feed. *Added v3.18.1,
    having been missed entirely.* A log entry carries two things that are not
    obviously fields: **its author**, and **whatever a previous release
    interpolated into its free-text `details`**. Both are identity. The Kai
    feed had four renderers and all four were raw.
11. **Audit log** — `ActivityLog`, the site-wide feed at `/activity-logs/`.
    *Added v3.18.2, having been missed by four consecutive audits.* Column 10
    was called "Activity" and silently meant one of **two** activity models;
    this is the other one. See the v3.18.2 note below.

---

## Kai — judicial / disciplinary

Governed **only** by in-app `KaiMemberPermission` grants. All seven Kai models
are deliberately unregistered from `/admin/`, so the admin columns are ➖ by
design — that gap is intentional and must not be "fixed".

| Field | In-app | Filter/search | CSV | Bulk | Admin | Admin CSV | API | Search | Dev rows | Case activity | Audit log |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `KaiReport.description` | ✅ `can_view_report_details` | ✅ `_kai_search_q` + `redacted_case_ids` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ gated on `_get_kai_access` | ➖ withheld | ➖ never in `details` | ➖ never in a description |
| `KaiReport.submitted_by` | ✅ `can_view_submitter_identity` | ✅ `_kai_search_q` + `redacted_case_ids` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ | ➖ withheld | ✅ `_redact_activity_log` — **the author of `created` IS the submitter** | ✅ `redact_kai_logs` — **the author of a `submitted` row IS the submitter**; `exclude_kai_logs` in `/admin/` + the per-member drill; `audit_search_q` on the filter |
| `KaiReport.targeted_to` | ✅ `can_view_accused_identity` | ✅ `_kai_search_q` + `redacted_case_ids` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ | ➖ withheld | ✅ `_redact_activity_log` — legacy `details` strings scrubbed at render | ✅ `redact_kai_logs` — **only the accused can write an `appeal_filed` row**; legacy descriptions scrubbed at render |
| `KaiReport.tags` | ✅ list-level, by design | ✅ ungated, by design | ✅ | ✅ | ➖ | ➖ | ➖ | ✅ | ➖ withheld | n/a | ➖ never logged |
| `KaiReport.case_number` | ✅ replaces the raw pk | ➖ | ✅ | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ withheld | ✅ in `ActivityLog` descriptions, officer-only | ✅ officer-only **by design** — a case number carries no identity, and `object_repr` stays searchable for exactly that reason |
| `KaiMemberPermission.*` | ✅ in-app only | n/a | n/a | n/a | ➖ | ➖ | ➖ | ➖ | ➖ withheld | n/a | n/a |
| `KaiBreakGlassGrant.*` | ➖ shell-only (`manage.py kai_break_glass`) | n/a | n/a | n/a | ➖ **never register** | ➖ | ➖ | ➖ | ➖ withheld | n/a | ✅ grant + revoke are logged by design, and name the *admin*, not a party |

> ## ⚠️ v3.18.2 — the eleventh surface, and why the column above it was a trap
>
> Column 10 was called **"Activity"**. There are two activity models, and it
> silently meant one of them. `ActivityLog` — the site-wide audit feed at
> `/activity-logs/`, readable by every officer and chair — carried the same two
> identities in prose: `"<Name> submitted Kai case #12"` written with
> `user=request.user` (on a submission that user *is* the reporter), and
> `"<Name> filed an appeal on …"` which only the accused can write.
>
> **This table pointed straight at it and looked away.** The `case_number` row's
> Activity cell read *"✅ in ActivityLog descriptions, officer-only"* — so
> someone examined that page, correctly decided an officer may see a case
> *number*, and did not look at the words on either side of the number.
>
> **The rule that follows, and it is the one this file most needs: enumerate the
> MODELS that can store a field's value before you enumerate the surfaces that
> render it.** Prose is storage. An audit description, a notification body, an
> email subject and a log line are all places a value comes to rest under a
> different name, and **none of them appear in a grep for the field.**
>
> Second time the miss was a *place* rather than a *rule*: `CalendarSubscription`
> escaped v3.16.0's admin coverage pass because it lived outside `src/models/`.

**`redacted_case_ids` is not a duplicate of the permission gate — it is the
second axis.** The flags answer *may this user read this field at all*; the id
list answers *is this the one row where the answer is no anyway*. The reviewer
list shows a case the viewer is the accused on as a redacted row, so for that
row alone both answers must be consulted. Any new list-shaped Kai surface that
displays-rather-than-excludes has to pass the ids too.

**The four activity renderers**, so the next person does not have to find them:
`templates/kai/manage_report.html` (case detail), `kai/partials/case_timeline.html`
(v3.18.0), `kai/print_report.html` (whole log, leaves the app),
`kai/view_reports.html` (cross-case "Recent Activity" panel, list-level
audience). All four read `display_actor` / `display_details`;
`test_no_template_renders_raw_activity_fields` fails if a fifth does not.

**`tags` is deliberately ungated and that is load-bearing.** It is a *closed
vocabulary* (`KaiReport.TAG_CHOICES`), so it carries no identity. If it is ever
loosened back to free text, `_kai_search_q` must start gating `tags__icontains`
and the list card and CSV must redact the chips — the note at
`src/models/kai.py:37` says so. Adding a tag is a code change on purpose.

---

## Ballots and anonymity

The rule that generates this section: **ask what the redacted view can be joined
against.** A timestamp, a sequence, or a row ordering is a join key.

| Field | In-app | Filter/search | CSV | Bulk | Admin | Admin CSV | API | Search | Dev rows |
|---|---|---|---|---|---|---|---|---|---|
| `Vote` on `anonymous_vote` legislation | ✅ tallies only | n/a | ➖ | ➖ | ✅ excluded in `get_queryset`, read-only | ✅ | ➖ | ➖ | ➖ withheld |
| `CommitteeVote` on anonymous legislation | ✅ tallies only | n/a | ➖ | ➖ | ✅ excluded in `get_queryset` | ✅ | ➖ | ➖ | ➖ withheld |
| `AnnouncementPollAnswer` on `is_anonymous` poll | ✅ aggregate only | n/a | ✅ respondent + timestamp dropped, **rows shuffled** | n/a | ✅ excluded in `get_queryset` | ✅ | ➖ | ➖ | ➖ withheld |
| Poll respondent list, anonymous poll | ✅ `>2` reveal threshold; half-list when closed under it | n/a | ✅ omitted | n/a | ✅ | ✅ | ➖ | ➖ | ➖ withheld |
| `SlatingVote.voted_at` | ✅ | n/a | ➖ | ➖ | ✅ `exclude` | ✅ | ➖ | ➖ | ➖ withheld |

**The shuffle is a control, not cosmetics.** `_export_poll_csv` uses
`random.SystemRandom().shuffle` because `-submitted_at` row *ordering* is itself
a join key even once the timestamp column is gone.

**Known residual, accepted:** `SlatingVote`'s sequential primary keys still leak
coarse ordering. Never add `voted_at` or id-adjacent ballot data back to
`SlatingVoteAdmin`. `SlatingBallot` (participation only) and `SlatingActivity`
(audit) are visible by design.

---

## Slating

| Field | In-app | Filter/search | CSV | Bulk | Admin | Admin CSV | API | Search | Dev rows |
|---|---|---|---|---|---|---|---|---|---|
| `SlatingInterview.notes` / `.strengths` / `.concerns` | ✅ | n/a | ➖ | ➖ | ✅ `exclude` | ✅ honours `exclude` | ➖ | ➖ | ➖ withheld |
| `SlatingApplicationResponse.{text,number,json,file}_value` | ✅ `is_confidential` | n/a | ➖ | ➖ | ✅ `exclude` | ✅ | ➖ | ➖ | ➖ withheld |
| **`SlatingApplication.review_notes`** | ✅ | **⚠️ F-1** | ➖ | ➖ | **⚠️ F-1** | **⚠️ F-1** | ➖ | ➖ | ⚠️ **not** withheld |

---

## Recruitment

| Field | In-app | Filter/search | CSV | Bulk | Admin | Admin CSV | API | Search | Dev rows |
|---|---|---|---|---|---|---|---|---|---|
| **`RecruitmentCandidateNote.body`** | ✅ `can_view_private` | **⚠️ F-2** | ➖ | ➖ | **⚠️ F-2** | **⚠️ F-2** | ➖ | ➖ | ⚠️ **not** withheld |

---

## Credentials and bearer material

These are not "confidential" in the deliberative sense — they are secrets, and
the correct state is that nobody reads them back, ever.

| Field | In-app | Admin | Admin CSV | API | Dev rows |
|---|---|---|---|---|---|
| `ParliamentUser.password` | ➖ | ➖ | ➖ | ➖ | ✅ column-redacted |
| `APIToken.key` | shown once at creation | ✅ `exclude` | ✅ | ➖ | ➖ withheld |
| `EmailVerificationToken.token` | ➖ | ✅ registered, table withheld in dev rows | ✅ | ➖ | ➖ withheld |
| `PushSubscription.p256dh` / `.auth` | ➖ | ✅ `exclude` | ✅ | ➖ | ➖ withheld |
| `WebAuthnCredential.credential_id` / `.public_key` | ➖ | ✅ `exclude` (delete kept as passkey escape hatch) | ✅ | ➖ | ➖ withheld |
| `CalendarSubscription.token` | ✅ rotatable, own feed only | ➖ **not registered, deliberately** | ➖ | ➖ | ➖ withheld |

---

## Member account fields

`MEMBER_ACCOUNT_FIELDS` (`password`, `last_login`, `force_password_change`,
`has_default_password`, `backup_codes_acknowledged`, `onboarding_complete`) are
stripped off *joined* member relations by `member_defer()`. This is a
performance control that doubles as a confidentiality one:
`NoCredentialColumnsOnJoinsTests` in `src/test_query_narrowing.py` scans a
page's JOINs for password/token columns.

The API's `MemberSerializer` is an explicit allowlist of 20 display fields —
no email, no phone, no security flags. **Verified: `/api/v1/` registers only
`members`, `events`, `legislation`, `committees`, `attendance`. There is no Vote,
Kai or Slating endpoint at all**, which is why the API column is ➖ throughout
the tables above.

---

## Findings — the empty cells this table found

Both are admin-surface, so the exposed population is small (whoever holds Django
superuser). They are recorded as findings anyway because the project's standing
rule — *being a Django admin is an operational role, not a grant of judicial,
deliberative or ballot-level access* — makes that explicitly not a defence.

### ⚠️ F-1 — `SlatingApplication.review_notes` is the one confidential slating field the v3.16.2 pass missed

*Location:* `src/models/slating.py:443`, `src/admin.py:2382–2408`

The model calls it what it is: `help_text='Confidential review notes'`. Its two
neighbours were both handled in v3.16.2 — `SlatingInterview.notes/strengths/
concerns` and all four `SlatingApplicationResponse` value fields carry `exclude`.
This one carries none, and it is worse than merely visible:

```python
class SlatingApplicationAdmin(admin.ModelAdmin):   # editable, not ReadOnlyAdmin
    search_fields = ('applicant__name', 'period__name', 'review_notes')
    fieldsets = (..., ('Review', {'fields': ('reviewer', 'review_notes')}), ...)
    # no exclude
```

Three surfaces at once: shown on the detail page, **searchable** (so the
changelist is an oracle over review notes even before you open one), and
exported by `export_as_csv`, which honours `exclude` and has none to honour.

*Fix (~5 min):* add `exclude = ('review_notes',)`, drop it from `search_fields`
and from the `Review` fieldset. Consider `ReadOnlyAdmin` for parity with the
other slating models.

### ⚠️ F-2 — `RecruitmentCandidateNote` is governed by an in-app permission and fully open in the admin

*Location:* `src/admin_extra.py:550–553`, gated in-app at
`src/view/committee/recruitment.py:658`

In the app, candidate notes require `RecruitmentMemberPermission.can_view_private`.
In the admin they are an editable model with `search_fields = ('candidate__name',
'body')` and no `exclude`.

This is the case CLAUDE.md's rule-of-thumb names exactly: *"if a model's
visibility is decided by an in-app permission/flag rather than by Django's own
perms, either don't register it or exclude the sensitive fields."* It is the same
shape as `KaiMemberPermissionAdmin`, which v3.16.2 called the sharpest edge and
removed.

*Fix (~5 min):* `exclude = ('body',)` and drop `body` from `search_fields`, or
unregister the model and manage notes in the app, as Kai is. Whichever you pick,
add a comment at the site saying which — an unexplained absence reads as an
oversight to the next reviewer.

### Also worth a decision

Neither `src_slatingapplication` nor `src_recruitmentcandidatenote` is in
`dev_mode_rows._EXPLICIT_SENSITIVE_TABLES`. That was defensible while both were
admin-visible — the 07-30 review's reasoning was that the inspector must not show
a developer anything they could not already read in `/admin/`, and the two lists
agreed. **If you fix F-1 and F-2, that agreement breaks**, and both tables should
be added to the withheld set in the same change.

---

## Adding a row

When you add a field that carries a confidentiality promise:

1. **Add its row here first.** Nine cells is nine questions, and the ones people
   forget are *Filter/search*, *Bulk* and *Admin CSV*.
2. **Redact in the admin with `exclude`, not by omitting from `fieldsets`.**
   Only `exclude` reaches `export_as_csv`. F-1 is what the other way looks like.
3. **Check the filter predicate, not just the output.** If a user can search or
   filter on a field they cannot read, they can binary-search its contents.
4. **Grep for the second copy.** `grep -n "text/csv" src/view/**/*.py` and
   `grep -rn "<field_name>" src/` before you call it done.
5. **Consider what the redacted view can be joined against** — a timestamp, a
   sequence, an ordering, a row count.

## Enforcement in tests

| Test | What it holds |
|---|---|
| `src/test_kai_csv_redaction.py` | Every Kai CSV goes through `_kai_csv_row`; fails on any inline `writerow([` in the module |
| `src/test_kai_search_gating.py` | The Kai search predicate is permission-gated |
| `src/test_geo_restriction.py` | Every `text/csv` writer is geo-guarded or allowlisted with a reason |
| `src/test_query_narrowing.py` | No page selects credential columns on a join |
| `src/test_dev_mode_rows.py` | No ballot content reaches the row inspector |

There is **no test** asserting that admin classes exclude the fields in this
table. That would be the natural next guard, and it is what would have caught
F-1 and F-2 automatically.
