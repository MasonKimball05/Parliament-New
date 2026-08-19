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
| v3.19.4 | 08-08-26 † | `0bcf510` | *Committed & pushed 08-08-26.* 08-08 auto-run fixes, all 🟡 and four of five in `performance.py`. `perf_sampled_count` was written on every stored sample and **read by nothing**, while `stored_samples` used a saturating `len()` — the same defect v3.19.3 had just fixed for `total_requests`, sixty lines below the fix; now `sampled_requests` (exact) and `stored_samples` (occupancy) are separate. `memory_report`'s "buffer near capacity" check could never be false (a full ring buffer is the steady state) — replaced with a byte measurement. Three wrong accuracy claims corrected and `requests_last_hour` → `samples_last_hour`; the admin-v2 dashboard, the only human-facing reader, finally says its averages are sampled. `FORGED_CF_HEADER` throttled per socket peer — unthrottled it could roll a `RotatingFileHandler` shared with every other security event. Draft attachments now have exactly one lifetime: a `post_delete` receiver, and publish unlinks the redundant private original `on_commit`. **No migration.** ⚠️ **31 new tests, none executed — sixth consecutive batch.** |
| v3.19.5 | 08-09-26 † | `b7c80be` | *Committed & pushed 08-09-26. **Row corrected 08-09-26**, minutes after the commit — the fourth release running whose ledger lines went in stale. See the note below.* 08-09 auto-run fixes. 🟠 **The v3.19.3 draft-file fix left the old route open, and two reports recorded it as closed.** `serve_legislation_draft_document` was built and both templates repointed, but `media/<path:path>` was never touched — so any authenticated member could still fetch any draft at `/media/legislation_drafts/<name>`, and the uuid that v3.19.3 calls *"explicitly NOT the access control"* was the access control. Pre-`0016` drafts had only a slug of the bill's title. Fixed with `PRIVATE_MEDIA_PREFIXES`, checked on the **resolved** path. 🟡 "Exactly one lifetime" did not cover **replace or clear** — the two things a member does from the edit form; `LegislationDraftForm` now unlinks the file it replaces. 🟡 The buffer threshold could not fire (512 KB vs a full buffer) **and the 13,693-byte measurement it came from was taken on identical entries, which `pickle` memoises** — real cost is 22–33 KB; now a per-entry budget beside the measurement. 🟡 The suppressed `FORGED_CF_HEADER` count expired unread whenever a burst stopped. **No migration.** ⚠️ **20 new tests, none executed — seventh consecutive batch.** |
| v3.19.6 | 08-11-26 † | `aef4f73` | *Committed & pushed 08-11-26 01:03. **Row corrected 08-11-26** — the FIFTH release running whose ledger lines went in stale, and this time inside the very commit that committed them. `src.W003` has now been deferred through five instances; build it.* 08-10 auto-run fixes. 🔴 **`/media/` served EIGHT more upload directories under a narrower promise than its own** — Kai allegation attachments, slating GPA screenshots and application files, excuse documents (doctors' notes), service-hours proof and bug screenshots, all readable by any authenticated member at a `slugify()` name, and all exempt from 2FA and lockdown. v3.19.5 built the right mechanism (`PRIVATE_MEDIA_PREFIXES`) and put ONE entry in it, calling drafts *"the first such thing in this codebase"*; it was the ninth, because nothing had enumerated the population. Fixed with 8 ownership-aware views, a schema-walking classification test that fails the build on an unclassified `upload_to`, and uuid names for the four confidential directories. Also caught: a view that checked access then `redirect()`ed to `/media/`. 🟡 The buffer budget was a TOTAL that three places called per-entry — fourth release on that condition, and the test guarding it asserted the inverse. 🟡 The forged-header tally still died with a burst that ends; now flushed on the write side at powers of ten. **Migration `0017` (AlterField only).** ✅ **TESTS RUN — 1,240 across all 58 modules**, first time in eight batches; 12 pre-existing failures, all reproduced on pristine `b7c80be`, incl. one v3.19.5 test that has never passed. |

| v3.19.7 | 08-13-26 † | `de0aeea` | *Committed & pushed 08-13-26. **Row corrected 08-13-26** — the SIXTH release running whose ledger lines went in stale, and the first one `src.W003` caught after the fact: the check built IN this release reported this release, on the 08-13 auto-run's `manage.py check`. That is the guard working and nobody running it — see v3.19.8, which puts it in `preflight`.* 08-11 auto-run fixes. 🟠 **Every private upload was served `inline` with a content type guessed from its filename, and four of the eight writers validated nothing** — `gpa_screenshot` trusted the uploader's own multipart `content_type` header, the three custom-field `file_value` writers checked nothing at all. A `.html` stored in `slating/gpa_screenshots/` came back as `text/html` on this origin, to the committee reviewing it. Fixed in three layers: an inline allowlist (PDF + raster images; **not** `image/svg+xml`), a storage-level refusal that no writer can bypass, and `validate_uploaded_file` at the four sites. Found on the way: **`validate_mime_type` had never rejected anything** — its `raise` was inside a `try` whose `except Exception` logged it — and `BLOCKED_EXTENSIONS` named `.php`/`.jsp`/`.exe` but not `.html`/`.svg`. 🟡 The two monitoring-liveness tests **could not pass** since v3.19.3 made storage 1-in-20 probabilistic. 🟡 The lockout-release negative control asserted `!= 302` and then `in (302, …)`; endpoint verified safe, test rewritten to assert the effect. 🟡 The suite's failure count **depended on partitioning** (8 vs 12) — the cache was never cleared between tests; new `TEST_RUNNER`. 🟡 The four `5x src_featureflag` N+1s fixed via `FeatureFlag.resolve_many`, plus two uncached `.get()`s in `view/api.py` that an `ACCEPTED_REPEATS` entry had misdescribed as cold-cache artefacts. 💡 **`src.W003` BUILT — the ledger check, after five instances and four deferrals.** A `manage.py check` that reconciles each changelog's `**Committed & pushed:**` line and its row here against `git log --diff-filter=A`. ⚠️ **It says NOTHING about deployment** — per this file's own 08-02-26 amendment, `--diff-filter=A` dates a commit and cannot know whether a release shipped, so the Deployed column stays yours by hand. Both of its first two runs found bugs in itself (68 pre-ledger releases reported as defects; two corrected changelogs reported because their correction notes quote the words *"not yet"*), and its third run found the real thing: `v3.19.6.md` still read *not yet* inside the commit that committed it. **No migration.** ✅ **1,270 tests, 0 failures, under two partitionings that agree.** |

| v3.19.8 | 08-13-26 † | `3111b22` | *Committed & pushed 08-13-26. **Row corrected 08-15-26** — the SEVENTH release running whose ledger lines went in stale, and the second `src.W003` caught after the fact. The check is not failing; the moment is. See v3.19.9, which puts the check in the pre-push hook — the last trigger before a commit leaves the machine.* 08-13 auto-run fixes, all in code v3.19.7 wrote two days earlier. 🔴 **`validate_mime_type` began rejecting every Word and Excel document in the chapter.** v3.19.7 correctly fixed a `raise` trapped inside its own `except Exception` — and nothing asked what a check that had never rejected anything would start saying no to. The MIME map was dead code since 2025: `.xlsx` failed on a 2 KB sniff window too small to identify any OOXML file (8 KB suffices), `.docx` because libmagic reports it as `octet-stream` at every window. All 17 upload paths, including doctors' notes and legislation. Fixed by writing the enumeration FIRST (`test_upload_type_fixtures.py` walks `ALLOWED_FILE_TYPES` and fails the build on an extension with no real sample), widening the window, and validating zip-backed formats **structurally** — open the container, read what it declares — rather than admitting `application/zip`, which would have made the check meaningless. The new test caught a truncation bug in its own fix on the first run. 🟠 **The v3.19.7 test runner cleared every cache alias, and in production that alias is the session store** — `manage.py test` on the server would have signed out the chapter. Settings now force LocMem under test; the runner refuses to start against anything else. 🟡 The inline allowlist protected six private directories and not the ten public ones — seventh instance of "a rule stated correctly, then something left outside the helper", and the first where the thing left outside was the LARGER surface. Now shared via `src/utils/content_disposition.py`. 🟡 **`src.W003` works and nothing ran it**; now gated by `manage.py preflight`, verified by negative control. **No migration.** ✅ **1,313 tests, 0 failures, two partitionings agree.** ⚠️ **Unresolved and recorded:** a failure in `test_hardcoded_urls` surfaced under `--parallel` as `TypeError: cannot pickle 'traceback' object` with no test name, and a minimal reproduction did not reproduce it — re-run serially before trusting a parallel run that dies that way. |
| v3.19.9 | 08-15-26 † | `ee295da` | *Committed 08-15-26. **Row filled in 08-15-26, minutes after the commit and before the push** — the first time this line has been corrected at the right moment rather than days later, because the new pre-push hook stopped the push and said so. That is the whole point of moving the check to push time.* 08-15 auto-run fixes, reviewing v3.19.8. 🟠 **Four ordinary malformed Office files were HTTP 500, not "rejected".** v3.19.8's structural zip check caught `(BadZipFile, OSError, EOFError)` around the `ZipFile()` constructor and left `archive.open()`/`read()` — the second half of the same operation — outside it; an unsupported compression method, an encrypted entry, a corrupt member (`BadZipFile`, the *same type the handler names*) and an encrypted ODF `mimetype` all escaped uncaught, on all 17 upload paths, reachable by any member. Fixed by splitting detection into a function containing no verdict, so the catch can be as broad as a stdlib parser deserves. 🟠 **`tblib` was missing, so `--parallel` could not report ANY failure** — it aborted the whole run with `TypeError: cannot pickle 'traceback' object` and no results at all. Closes v3.19.8 §5; also corrects that report's claim that CI runs in parallel (it does not). 🟡 **The v3.19.7 cache isolation was absent in SPAWNED workers, i.e. on macOS** — installed in the launching process, and Django re-bootstraps spawn workers with the module-level `setup_test_environment`, never the runner's override. Measured `True` under fork, `ABSENT` under spawn. 🟡 **The pre-push hook has never been installed** — `.git/hooks/` holds only samples; the guard that would have caught `test_url_smoke` red 07-30→08-02, v3.19.6's 12 pre-existing failures and seven stale ledger lines has never run. Now also gates the release-integrity checks (an `--amend` at push time vs a follow-up commit at deploy time), with a test that fails when it is missing or stale. **No migration** (but `pip install -r requirements.txt` — `tblib` is new). Plus two carried housekeeping items: `test_hardcoded_urls`'s OOXML part-name literals replaced by a rule (`_is_ooxml_part_name`, both literals removed so the rule is not masked by a list), and `preflight`'s check partition stated once — the latter flagged on a `CheckMessage.__eq__` mechanism that **turned out not to exist**, recorded as such. ✅ **1,339 tests, 0 failures, two partitionings agree; 7 new tests verified failing against the pre-fix tree.** |
| v3.19.10 | *not deployed* | `6c7a44a` | 08-17 auto-run fixes. 🟠 **CI's Security Check job had been failing since 07-29-26** — nineteen days, ~12 pushes: `bandit -r src/ -ll` exited 1 on 12 MEDIUM findings (all benign, all now carrying justified inline `# nosec`), bisected against `git archive` of ten release commits. Nothing swallowed the signal; GitHub showed a red ❌ on every push and nobody downstream read it. **v3.19.9's new pre-push hook does not run bandit or pip-audit**, so the one gate that was red is the one the new gate does not cover. 🟠 **7 CVEs in 2 pinned deps** — `sqlparse` 0.5.5 → 0.6.0 (4, all unreachable: transitive, never imported here) and `cryptography` 48.0.1 → 50.0.0 (3, of which **PYSEC-2026-3553 IS reachable** — WebAuthn attestation carries a client-supplied `x5c` chain into a certificate path routine with an exponential blowup). Bump drags pyOpenSSL 26.2.0 → 26.4.0; suite verified green on the new versions *before* the pins moved. 🟡 **Every query budget was exactly 3 too high** — the first request in each `TestCase` pays `SystemLockdown.get_instance()`'s `get_or_create`, three queries production never spends, and the artefact sat one under `STALENESS_SLACK = 4` so the suite's own staleness check could not see it. All six re-measured; the two v3.18.6 dashboards finally have measured ceilings. 🟡 **The pre-push hook did not run the gate that was red** — both scans added, with flags pinned to `ci.yml` by a test, and a skip that is loud in BOTH directions (a check that cannot run must not report like one that failed, nor like one that passed); all four branches exercised with injected tools. 🟡 **`SystemLockdown.get_instance()` was a write on the read path** — `get_or_create` under middleware that runs on every request; now a read, with the absence cached via a sentinel because the naive fix would have traded INSERT-once for an uncached SELECT forever. **No migration; `pip install -r requirements.txt` IS required, and `make hooks` on every developer machine.** ✅ 1,349 tests, 0 failures under two partitionings; bandit exit 0 and pip-audit clean for the first time since July. |
| v3.19.11 | *not deployed* | *not yet* | 08-19 auto-run fixes, all descending from v3.19.10's `get_instance()` change. 🟡 **`LandingPageContent.get_instance()` was the same `get_or_create` on the PUBLIC anonymous landing page** — measured: one `INSERT` on the first `GET /`, then one uncached SELECT forever. v3.19.10 fixed the model it was looking at and the population was never enumerated (eighth instance of that shape, and the first where what was left outside was another model). New `SingletonRow` mixin + `test_singleton_rows.py`, which walks `apps.get_models()` so the next singleton is covered by a test written today. 🟡 **v3.19.10 CREATED a 500 on the emergency-lockdown console** — once `get_instance()` stops creating the row it can return an unsaved instance, and `save(update_fields=[…])` on one raises `DatabaseError: Save with update_fields did not affect any rows.`; `manage_lockdown`'s "update whitelist" and "update message" both use it, i.e. the two actions you take during the fresh-install and restore situations where the row is absent. Guarded on the mixin, not at the call sites. 🟠 **Django 5.2.16 → 5.2.17** (PYSEC-2026-3717 / CVE-2026-15830, GeoDjango WKT/WKB unbounded recursion) — **not reachable** (`contrib.gis` is not installed), bumped because CI's pip-audit step and v3.19.10's two-day-old pre-push gate would both have blocked this very push. **No migration; `pip install -r requirements.txt` IS required.** 🟡 **The bandit suppressions, and this batch's own first pass got them wrong** — it called two stale; stripping each one and re-scanning says **25 of 26 are load-bearing**, and the tidy it proposed would have **reddened CI** (naming the ids on a `mark_safe` line leaves B308 reported — the other four sites were green only because prose tokens accidentally made them blanket). The one real redundancy was the comment written to explain a suppression, which bandit read as one. All 26 rewritten with the justification behind a second `#`; new `src/test_nosec_hygiene.py`; `Test in comment` warnings ~200 → 0. ✅ 1,372 tests, 0 failures under two partitionings, on 5.2.17; 13 of 19 new tests verified failing against `a168726`. |

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

> **⚠️ It has now happened FOUR releases running — v3.18.8, v3.19.2, v3.19.4,
> v3.19.5 — and the fourth one is the argument (added 08-09-26).** v3.19.5 went
> into `b7c80be` with its `**Committed & pushed:**` line still reading *not yet*
> and its row here reading *not committed*, **written that way deliberately**,
> minutes earlier, because a hash cannot honestly be guessed before the commit
> exists. Both were corrected within minutes of the push rather than the next
> morning, which is the best this can be done by hand — and it is still a
> separate act of memory that happens to have been remembered.
>
> **The lesson from four instances is narrower than "people forget".** Every
> other release detail is *knowable while writing the changelog*; these two lines
> are the only ones whose value does not exist until the write is over. A
> document cannot record a fact that postdates it, so no amount of care at
> authoring time fixes this — the check has to run *after* the commit. That is
> `src.W003`, and it has been deferred twice on v3.18.4's principle that the
> response to a pattern should not ride along with an instance of it. **The
> pattern has now recurred three times since that decision. Build it.**

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

> **† Reconstructed 08-17-26, and the marker is the point.** These six rows sat
> at *not deployed* for up to nine days while the releases were live. Mason
> confirmed on 08-17-26 that everything through v3.19.9 had shipped days
> earlier, and directed that the dates be taken from the commits that added each
> changelog. **They are therefore derived, not observed** — the dagger says so,
> because this file's entire reason for existing is that a plausible-looking
> unverified claim is indistinguishable from a checked one. Every other row in
> this table was written by someone who had just done the deploy.
>
> ⚠️ **AND THIS IS THE MIRROR OF THE EIGHT-REPORT ERROR, WHICH IS WORTH SAYING
> PLAINLY.** That one read a stale *"folds into the pending deploy"* line and
> concluded a backlog existed. The 08-17-26 auto-run read this column, found six
> explicit *not deployed* cells in a file that was visibly being maintained, and
> concluded the same thing — and was wrong the same way, for the opposite
> reason. **What was being maintained was the Commit column**, because
> `src.W003` and the pre-push hook can check a sha against `git log`. Nothing
> can check the Deployed column from inside the repo, so nothing did.
>
> **The half of the ledger that got a guard is the half that needed one least.**
> `preflight`'s `check_deploy_ledger_is_stamped` (v3.19.10) is the response: it
> runs on the server, after the restart, which is the only place and moment that
> knows what is actually live.

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
