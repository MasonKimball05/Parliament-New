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

Three releases, one shape. **This table is the checklist that makes the second
copy visible before it ships.** When you touch a confidential field, read across
its row.

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

---

## Kai — judicial / disciplinary

Governed **only** by in-app `KaiMemberPermission` grants. All seven Kai models
are deliberately unregistered from `/admin/`, so the admin columns are ➖ by
design — that gap is intentional and must not be "fixed".

| Field | In-app | Filter/search | CSV | Bulk | Admin | Admin CSV | API | Search | Dev rows |
|---|---|---|---|---|---|---|---|---|---|
| `KaiReport.description` | ✅ `can_view_report_details` | ✅ `_kai_search_q` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ gated on `_get_kai_access` | ➖ withheld |
| `KaiReport.submitted_by` | ✅ `can_view_submitter_identity` | ✅ `_kai_search_q` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ | ➖ withheld |
| `KaiReport.targeted_to` | ✅ `can_view_accused_identity` | ✅ `_kai_search_q` | ✅ `_kai_csv_row` | ✅ `_kai_csv_row` | ➖ | ➖ | ➖ | ✅ | ➖ withheld |
| `KaiReport.tags` | ✅ list-level, by design | ✅ ungated, by design | ✅ | ✅ | ➖ | ➖ | ➖ | ✅ | ➖ withheld |
| `KaiMemberPermission.*` | ✅ in-app only | n/a | n/a | n/a | ➖ | ➖ | ➖ | ➖ | ➖ withheld |

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
