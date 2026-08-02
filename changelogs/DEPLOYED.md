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
same-day, so commit date == deploy date for everything below. The v3.13.x dates
come from v3.13.2/v3.13.3's own explicit `**Deployed:**` markers, which predate
this file and are more specific than their commit dates.

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
| v3.18.1 | — | *not deployed* | 🔴 08-01 auto-run fixes — Kai search oracle, activity-feed identity redaction (4 renderers), print-view header, exec-board bypass, CI migration gate. **Migration `0012`.** |

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
