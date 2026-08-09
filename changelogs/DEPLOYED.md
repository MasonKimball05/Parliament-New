# Deployment ledger

**What this file is for.** A changelog records what a release *contains*. Nothing
recorded whether it had *shipped* — so the only signal a later reader had was
each changelog's closing line, which was written *before* the deploy and never
revisited.

**That gap cost eight reports.** From 07-23-26 to 07-31-26 every nightly auto-run
opened with "deploy v3.13.1 → v3.17.x" as its top priority, and the deploy
checklist grew from 7 items to 13. On 07-31-26 Mason confirmed **every one of
those releases had shipped on the day it was written.** Each run read the same
"folds into the pending deploy" line, believed it, and wrote it forward. Nobody
was wrong at any single step; the state simply had nowhere to live.

**So: update the table when you deploy.** One line. It is the only place that
knows.

---

## Status

Dates are the commit that introduced each release's changelog, recovered from
`git log --diff-filter=A`. Mason confirmed 07-31-26 that deploys follow pushes
same-day, so commit date == deploy date **for everything up to v3.18.0**. The
v3.13.x dates come from v3.13.2/v3.13.3's own explicit `**Deployed:**` markers,
which predate this file and are more specific than their commit dates.

> **⚠️ That equivalence broke at v3.18.1 (noted 08-02-26).** It was committed
> 08-01 and deployed 08-02 — the first release where the two dates differ. The
> 08-02 nightly review had already been caught by this from the other side: it
> ran `git log --diff-filter=A`, got 08-01, and concluded v3.18.1 was live,
> while the row below said *not deployed* in as many words.
>
> **So do not re-derive deploy dates from commit dates, in either direction.**
> `--diff-filter=A` dates a commit. This table answers "is it live". They were
> the same number for eighteen releases and that made the shortcut look safe;
> it was a coincidence, not a rule.

| Release | Deployed | Commit | Notes |
|---|---|---|---|
| v3.13.0 | 07-15-26 | `7e35a15` | Officer transition checklist, `roles_json` XSS fix |
| v3.13.1 | 07-15-26 | `6440535` | Login-pipeline regression tests |
| v3.13.2 | 07-15-26 | `6440535` | Credential re-auth recovered, snapshot hardening |
| v3.13.3 | 07-15-26 | `6440535` | Passkey vote confirmation, vote-page reliability |
| v3.14.0 | 07-17-26 | `6440535` | Voting batch — auto-close parity, WebSocket push, vote receipts, turnout panel. Migration `0007` |
| v3.14.1 | 07-18-26 | `35ab66a` | 🔴 public `/media/` exposure fixed; mobile broken-seal fix |
| v3.14.2 | 07-19-26 | `1468ab9` | Filename sanitisation, RFC 5987 Content-Disposition |
| v3.15.0 | 07-19-26 | `1468ab9` | Mobile UI pass, pip-audit fix |
| v3.15.1 | 07-19-26 | `8198001` | |
| v3.15.2 | 07-19-26 | `72c01e4` | Scheduler fix |
| v3.15.3 | 07-20-26 | `bf3c7c0` | 502 geolocation error fixed |
| v3.15.4 | 07-22-26 | `c8628ac` | 07-22 auth security sweep findings A–C |
| v3.15.5 | 07-22-26 | `c8628ac` | |
| v3.15.6 | 07-23-26 | `58c9d58` | Page-visits filter; three 500-on-use admin-v2 search boxes fixed |
| v3.15.7 | 07-23-26 | `faf4e70` | Admin-v2 security mobile pass |
| v3.15.8 | 07-24-26 | `9c7c9d1` | `manage.py preflight` |
| v3.15.9 | 07-24-26 | `9c7c9d1` | CSP nonce fixes on 6 templates; chapter-stats Chart.js vendored |
| v3.15.10 | 07-24-26 | `9c7c9d1` | Supply-chain check fixes, integrity manifest auto-discovery |
| v3.16.0 | 07-24-26 | `9c7c9d1` | Admin full-coverage pass — 82 models registered |
| v3.16.1 | 07-24-26 | `9c7c9d1` | Admin feature-area sections |
| v3.16.2 | 07-25-26 | `6a1909f` | **Admin confidentiality boundary** — the standing rule in CLAUDE.md |
| v3.16.3 | 07-28-26 | `c95bfa6` | Kai search gating; template URL-name test |
| v3.17.0 | 07-29-26 | `619eaae` | |
| v3.17.1 | 07-29-26 | `619eaae` | |
| v3.17.2 | 07-29-26 | `619eaae` | |
| v3.17.3 | 07-29-26 | `cc5afea` | N+1 sweep; revived the year-dead event sign-up views |
| v3.17.4 | 07-29-26 | `b2ffd17` | `Attendance.date` fix + migration `0010`; changelog cache fix |
| v3.17.5 | 07-30-26 | `c608f82` | 07-30 auto-run fixes — cache-key bounding, geo exports, two ceilings |
| v3.17.6 | 07-30-26 | `95dd8f8` | URL hyphen rename follow-through; `nginx.conf` changed |
| v3.17.7 | 07-31-26 | `abe7367` | 07-31 auto-run fixes — 🔴 Kai bulk-export redaction, home-page join reuse, geo-guarded query-param exports |
| v3.18.0 | 07-31-26 | `eb9f72e` | 🔴 Kai recusal + stand-ins, appeals (bylaws § b.i), case aging, assignment, per-year case numbers. **Migration `0011` includes a data backfill.** |
| v3.18.1 | 08-02-26 | `0b9dcd1` | 🔴 08-01 auto-run fixes — Kai search oracle, activity-feed identity redaction (4 renderers), print-view header, exec-board bypass, CI migration gate. **Migration `0012`.** Committed 08-01, deployed 08-02 — the one release so far whose commit date and deploy date differ. |
| v3.18.2 | 08-02-26 | `eeebfae` | 🔴 08-02 auto-run fixes — `ActivityLog` was the eleventh Kai surface (submitter + accused named to every officer/chair); `is_admin` no longer grants Kai access, `KaiBreakGlassGrant` + `manage.py kai_break_glass` replace it; batched recusal lookup, folded aggregate, truncation notice, bounded case-number retry. **Migration `0013`.** ⚠️ **Run `manage.py check` after deploying — `src.W001` fires if nobody can open Kai.** *Shipped without `migrate` being run; the admin dashboard 500'd on the missing `KaiBreakGlassGrant` table until it was. See v3.18.3's `src.W002`.* |
| v3.18.3 | 08-02-26 | `41067c9` | Backlog session — query-budget suite, party-safe surface test (which immediately caught the v3.18.1 oracle reproduced inside `kai_audit.py`), full-suite re-baseline, four calendar N+1s, 51-query security-alerts N+1, `src.W002` for an out-of-date schema, two stale traversal tests, four dead files deleted. **No migration.** |
| v3.18.4 | 08-04-26 | `ad245ed` | 🔴 08-03 auto-run fixes — the `/activity-logs/?user=` author filter was a Kai disclosure the redaction could not cover; v3.18.3's second axis only fired when both identity flags were held; shared case resolution (`rows_for_cases_q`); CSV export bounded; Kai flags memoised per request; calendar upcoming-events bounded. **No migration.** *Shipped with its 15 tests never having been executed; they were run 08-04-26 alongside v3.18.5's and are green (47 in `test_kai_audit_log`, 25 in `test_kai_party_safe_surfaces`).* |
| v3.18.5 | 08-04-26 | `d804b6d` | 🔴 08-04 auto-run fixes — `redact_kai_logs` redacted three fields and the page rendered four: `ip_address` was printed and exported unredacted on Kai rows and left searchable, reconstructing the disclosure v3.18.4 closed one column over. Plus a negated JSON key transform silently dropping rows, and two export-CSV nits. **No migration.** ✅ **Both Kai audit suites executed 08-04-26 — 47 + 25 = 72 green on sqlite**, which also clears v3.18.4's 15. *Not yet checked: the negative control (do the new tests actually fail against the pre-fix tree?) and a Postgres run of the JSON-negation test. See `v3.18.5.md` → Tests.* |
| v3.18.6 | 08-04-26 | `0f2bf73` | N+1 fixes on two dashboards found by prod dev mode — `/admin-v2/two-factor/` (130 → 3: `user_has_device` walked every OTP device class per member, `two_factor_requirement` was an unjoined reverse OneToOne) and `/service-hours/dashboard/` (88 → 4: four per-member aggregates collapsed into four grouped ones). **No migration.** ⚠️ **Two new `test_query_budgets.py` classes ship WITHOUT a `BUDGET` constant — measure and fill them in.** |
| v3.18.7 | 08-07-26 | `97e2100` | *Pushed 08-06-26, deployed 08-07-26 with the five-release batch.* 08-06 auto-run fixes, all in the middleware chain — `SystemLockdown` read uncached on **every** request incl. anonymous; the `IPBlacklist` gate skipped `/admin/` and `/contact/submit/`; session-hijack detection blind half of every cycle (baseline rewritten every 300 s, compared every 600 s); performance monitoring reporting zeros three different ways. Plus `SiteSetting` caching. **No migration.** ⚠️ **Caches a security control — after deploying, activate lockdown and confirm it engages on the very next request, not five minutes later.** 48/49 tests green on the first run; the one failure was a defect in a new test, since fixed. |
| v3.19.0 | 08-07-26 | `97e2100` | *Pushed 08-06-26, deployed 08-07-26 with the five-release batch.* Private legislation drafts on My Work. **Migration `0014` — includes a data backfill; read the changelog's deploy notes.** |
| v3.19.1 | 08-07-26 | `97e2100`, `2d64116` | *Pushed 08-06-26, deployed 08-07-26 with the five-release batch.* Foreword document staged ahead of the chapter vote, four per-document C&B feature flags, and explicit document ordering (`GoverningDocument` had **no `Meta.ordering` at all** — order was whatever the DB returned). **Migration `0015`.** ⚠️ **Deploy order matters: `migrate` → `seed_feature_flags` → `seed_cnb_documents`.** The Foreword stays invisible until `cnb_foreword` is toggled on — it is unpassed governance, and it fails closed via `FeatureFlag.DISABLED_BY_DEFAULT`. `2d64116` also swept five multi-line `{# … #}` comments (which render) and added `src/test_template_comments.py` to stop a fourth occurrence. |
| v3.18.8 | 08-07-26 | `f260539` | *Pushed 08-06-26, deployed 08-07-26. **Row added 08-07-26** — this release was committed and pushed with its changelog still reading "Committed & pushed: not yet", and had no row here at all until the 08-07 auto-run caught it. See the note below.* **Every audit trail was recording the Cloudflare edge, not the visitor** — found from a prod activity-log export where all 22 distinct IPs were Cloudflare and a credential-stuffing burst shared an address pool with six members' logins. Five surfaces parsed X-Forwarded-For inline and took the rightmost entry; that rule is safe behind nginx alone and inverts behind Cloudflare. All consolidated onto `get_client_ip` + `src/test_client_ip_single_source.py`. Also removed the duplicate logout `ActivityLog` row. **No migration.** ⚠️ **Tests never executed.** |
| v3.19.2 | 08-07-26 | `bdda0e1` | *Pushed 08-06-26, deployed 08-07-26. **Row added 08-07-26** — same omission as v3.18.8.* Release Login Lockout button on the user security page, plus the bug it exposed: Parliament locks out through **two systems with nine cache keys**, and the admin's Clear button hand-listed six under a comment reading "all three systems". The three it missed were the whole `account_*` family — **the username lockout an ordinary member hits by mistyping his password** — so clearing reported success and left him locked out. Fixed by `clear_lockouts_for()`. **No migration.** ⚠️ **Tests never executed.** |
| v3.19.3 | 08-07-26 | `b98ce12` | 08-07 auto-run fixes. 🟠 **Draft attachments were readable by any authenticated member** — the row was author-scoped, the file was served by `/media/` (`@login_required`, no owner check) under a name the v3.14.2 slugifier derived from the title. New `serve_legislation_draft_document` + uuid storage names + publish now COPIES out of the private directory. 🟠 Opt-in `CLOUDFLARE_VERIFY_ORIGIN` — `CF-Connecting-IP` was trusted unconditionally, and v3.18.8 put the blocklist, both rate limiters, the honeypot ban, the geo gate and every audit row behind it. 🟠 `PerformanceMiddleware` re-serialised a 500-entry list through Redis on **every** request (~19 KB, 2 round trips) — now sampled, with exact counters. Plus the `LegislationDraft` admin note, `clear_lockouts_for(match=…)`, and these two ledger rows. **Migration `0016`.** ⚠️ **Defaults OFF for the Cloudflare check — read `v3.19.3.md` before enabling.** |
| v3.19.4 | *not deployed* | — | 08-08 auto-run fixes, all 🟡 and four of five in `performance.py`. `perf_sampled_count` was written on every stored sample and **read by nothing**, while `stored_samples` used a saturating `len()` — the same defect v3.19.3 had just fixed for `total_requests`, sixty lines below the fix; now `sampled_requests` (exact) and `stored_samples` (occupancy) are separate. `memory_report`'s "buffer near capacity" check could never be false (a full ring buffer is the steady state) — replaced with a byte measurement. Three wrong accuracy claims corrected and `requests_last_hour` → `samples_last_hour`; the admin-v2 dashboard, the only human-facing reader, finally says its averages are sampled. `FORGED_CF_HEADER` throttled per socket peer — unthrottled it could roll a `RotatingFileHandler` shared with every other security event. Draft attachments now have exactly one lifetime: a `post_delete` receiver, and publish unlinks the redundant private original `on_commit`. **No migration.** ⚠️ **31 new tests, none executed — sixth consecutive batch.** |

> **⚠️ Why v3.18.8 and v3.19.2 were missing (added 08-07-26).** Both were
> committed and pushed on 08-06, both changelogs still said
> `**Committed & pushed:** *not yet*`, and neither had a row here. Nothing
> failed — the mechanism is a per-release line that has to be revised at the
> moment of commit, and two commits made on the way out of a long session did
> not get it. **This is the file created to abolish exactly that failure**, so
> it is worth recording rather than quietly fixing: a ledger only works if
> writing to it is part of committing, and right now it is a separate act of
> memory. The 08-07 report proposes an `src.W003` system check that compares
> each changelog's claim against `git log --diff-filter=A` — the same technique
> as `src.W001`/`src.W002`, applied to the process documents.

**Outstanding spot-checks for v3.17.7** (from its changelog, not blockers):
Kai reports list as a *list-only* reviewer → Export CSV must show `[Redacted]` in
Submitted By / Targeted To / Description; home page "My Committees" member counts
(read "1 member" before); `/officers/activity/` tab counts.

---

## How to update this

When you deploy, add or amend the release's row. If several releases ship in one
push, give them the same commit — that is how v3.13.x and the `9c7c9d1` block are
recorded.

Each `changelogs/vX.Y.Z.md` also carries its own `**Deployed:**` line. Keep the
two in sync; this table is the index, the changelog line is the detail.

## Related

- `CLAUDE.md` → *Deployment Protocol (Parliament)* — the actual deploy steps
- `docs/RESTORE.md` — restoring from a snapshot
- `scripts/check_uptime.sh` — post-deploy watchdog

---

## The general lesson

**State that only ever lives in prose goes stale, and prose is persuasive.** The
changelogs never lied — each was accurate the day it was written. What made them
misleading is that they described a *future* ("folds into the pending deploy") in
a document nobody revisits, and a later reader cannot tell a stale future from a
current one.

Two rules that follow, worth applying past deploys:

1. **Don't write a claim about the future into a document nobody will revisit.**
   Put it somewhere with a status column, or write it as-of a date.
2. **When several sources agree, check whether they're independent.** Eight
   consecutive reports agreeing a deploy was pending looked like overwhelming
   evidence. It was one piece of evidence, read eight times — and the ninth run
   could have checked `git log --diff-filter=A` in about four seconds.
