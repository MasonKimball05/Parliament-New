# Parliament — Runtime Architecture

> Companion to [`HANDOFF_DEVELOPER.md`](HANDOFF_DEVELOPER.md) (deploy mechanics,
> directory layout, gotchas) and [`RESTORE.md`](RESTORE.md) (disaster recovery).
> This document answers a different question: **what actually happens at
> runtime** — how a request flows through the stack, where the auth layers sit,
> and what the background machinery does. Written for the graduation handoff:
> read this first, then HANDOFF_DEVELOPER.md, then skim the gotchas again.

## The big picture

```
Browser ──HTTPS──▶ Cloudflare ──▶ nginx ──unix socket──▶ Daphne (ASGI)
                   (edge cache,      │                      │
                    TLS, WAF)        │ /static/ served      ├─ HTTP  → Django views
                                     │ directly by nginx    └─ WS    → Channels consumers
                                     │
                                     └─ /media/ → Django (auth!) — since v3.14.1
                                        nginx must NOT serve /media/ directly

Celery worker  ◀──Redis broker──  Celery beat (django_celery_beat, schedules in DB)
     │
     └─ emails, push notifications, vote auto-open/close, cleanup

PostgreSQL (prod) / SQLite (dev, DB_BACKEND=sqlite) ── Redis (cache + channels layer)
```

One systemd unit serves ALL HTTP and WebSocket traffic: `parliament-gunicorn`
— which, despite the name, runs **Daphne** (see gotcha #5 in the handoff
guide). `parliament-worker` and `parliament-beat` run Celery.

## Request lifecycle (the middleware stack, in order)

Every HTTP request runs this gauntlet — order matters and several entries
have position constraints (commented in `Parliament/settings.py`):

1. `SecurityMiddleware` (Django) — HSTS, SSL redirect
2. `PerformanceMiddleware` — request timing metrics (debug panel)
3. `SessionMiddleware` → `CommonMiddleware` → `CsrfViewMiddleware`
4. `PasswordResetRateLimitMiddleware` / `LoginRateLimitMiddleware` — brute-force
   protection, backed by cache counters + `LoginLockout` rows
5. `AuthenticationMiddleware` — attaches `request.user`
6. `InputSanitizationMiddleware` — pattern-scans anonymous traffic for
   SQLi/XSS probes, adds security headers incl. the **CSP nonce** (gotcha #2).
   Deliberately skips authenticated users (ORM parameterizes; templates escape).
7. `OTPMiddleware` (django-otp) — adds `user.is_verified()`
8. `Enforce2FAMiddleware` — redirects users who must enroll/verify 2FA.
   Exempts `/static/`, `/media/`, `/exportable_media/`, `/api/`, auth endpoints
   (an `<img>` fetch must never 302 to a 2FA page — the 07-18 seal lesson)
9. `SessionTrackingMiddleware` — populates the session viewer
10. `EmergencyLockdownMiddleware` — site-wide kill switch (admin_v2)
11. `AdminAccessMonitoringMiddleware` → `ForcePasswordChangeMiddleware` →
    `QuarantineEnforcementMiddleware` — admin audit, forced resets, and
    quarantined-user ejection on every request
12. `MaintenanceModeMiddleware` — maintenance page for non-admins
13. `MessageMiddleware` → `XFrameOptionsMiddleware` → `GeoRestrictionMiddleware`
    (blocks data-export endpoints for non-US sessions)

Context processors (`src/context_processors.py`) run on every template render:
`user_preferences`, `notifications`, `impersonation`, `feature_flags`,
`maintenance_mode`, `two_factor_status`. **These execute per-request — keep
them cached/cheap.** When reviewing performance, this file is the first stop.

## Authentication — the layers

From outermost to innermost:

- **Login** (`src/view/login_view.py`): password (PBKDF2) or **passkey as
  first factor** (`webauthn.py: passkey_authenticate_begin/complete`). Rate
  limited by middleware; failures feed `LoginHistory` (with encrypted IPs via
  `EncryptedFieldMixin`) and the lockout system.
- **2FA** (django-otp): TOTP + static backup codes; policy-driven enforcement
  via `Enforce2FAMiddleware` + the admin 2FA dashboard. Recovery flow in
  `two_factor_recovery.py`.
- **Session re-auth for sensitive actions**: credential changes (2FA disable,
  passkey register/delete) and **every vote cast** require fresh password or
  passkey confirmation (`check_vote_reauth`, one-shot 2-minute session grant).
  All credential-change endpoints share a single rate bucket
  (`cred_change_attempts`).
- **Authorization**: `member_type` gates (`Member`/`Chair`/`Officer`/…) checked
  per view; page-level kill switches via `@require_page_enabled` (feature
  flags + `PledgePageRestriction` allow/block list for pledges).
- **Impersonation**: `/staff/login-as/<user_id>/` for admins, tracked by the
  `impersonation` context processor and banner.
- **API** (`src/api/`, DRF): token-based with admin approval + scopes +
  access logging — separate from session auth, exempt from 2FA middleware.

## File serving — three roots, three rules (v3.14.1 hard lesson)

| Root | Who serves it | Auth | Use for |
|---|---|---|---|
| `/static/` | nginx directly (collectstatic output) | none | Anything referenced on anonymous pages or fetched cookieless: seal, favicon, PWA icons, CSS/JS |
| `/media/` | **Django** (`serve_media.py`), optional nginx X-Accel fast path | `@login_required` | Member uploads: legislation docs, Kai attachments, profile pictures |
| `/exportable_media/` | Django (`songbook.py: serve_exportable_media`) | `@login_required` | Curated exportable assets (song audio, artwork originals) |

Rules: (1) nginx must never serve `/media/` directly — that was the 07-18
exposure; (2) anything an anonymous or sessionless request needs must live in
`/static/` — login-gated media 302s to login HTML and Cloudflare will cache
the result; (3) uploaded filenames are slugified at save time
(`src/storage.py: SanitizedFilenameMixin`, wired as the default storage in
settings `STORAGES`) so filenames are always header- and URL-safe.

## WebSockets

`Parliament/asgi.py` routes `ws/` through Channels (`src/routing.py`):
`ws/chat/<channel_id>/` → `ChatConsumer`, `ws/votes/` → `VoteConsumer`
(v3.14.0 live vote push). Same Daphne process as HTTP; Redis is the channel
layer. If WebSockets break but HTTP works, check Redis before Daphne.

## Background jobs (Celery)

Broker: Redis. Schedules live in the **database** (`django_celery_beat`) and
are seeded/updated by `python manage.py setup_celery_schedules` — run it after
any schedule change, then restart `parliament-beat`. Task inventory
(`src/tasks/`):

- `votes.py` — auto open/close for chapter/committee/slating votes (every
  minute), scheduled announcement publishing, expired vote-receipt notices
- `notifications.py` — push notifications, event reminders, daily digest,
  service-event and recruitment reminders, scheduled notification firing
- `email.py` — announcement/security/general email sends (retrying)
- `cleanup.py` — session/lockout/blacklist/quarantine/push-subscription/API-log
  pruning

Symptom map: votes not auto-closing or receipts not expiring → worker+beat;
emails/pushes stuck → worker; schedule edits not taking effect → run
`setup_celery_schedules` + restart beat.

## Data layer notes

- Custom user model `ParliamentUser`, **string PK** `user_id` (gotcha #8).
- Sensitive columns (login IPs) encrypted at rest via `encrypted_fields.py`
  (Fernet; key derived from `SECRET_KEY` — **rotating SECRET_KEY breaks them
  and vote receipts**; see RESTORE.md).
- Query invariants live on managers/querysets, not in views — e.g.
  `Legislation.objects.open_for_voting()` is THE definition of "open for
  voting"; never re-derive it with hand-copied filters.
- Migrations are tracked in git (consolidated 07-05-26: `0001_initial` +
  seed). Normal flow: `makemigrations` on dev → commit → prod runs `migrate`.

## Security posture (what a successor must not break)

The defense stack, top to bottom: Cloudflare (TLS/WAF/cache) → nginx → rate
limiting → CSP with nonces → CSRF everywhere (the only `csrf_exempt` uses are
the honeypots, the CSP report receiver, and the rate-limited public contact
form — all intentional) → 2FA enforcement → per-action re-auth → quarantine/
lockdown switches → honeypot endpoints feeding an IP blacklist → encrypted
PII columns → append-only activity/audit logs. The admin_v2 security
dashboard is the operational window into all of it.

When adding a view, the checklist is: auth decorator, member-type check,
`@require_POST` for mutations, CSRF (i.e. don't exempt it), nonce on any
inline script, and never serve user files outside the three-roots table.

---

*Created 07-19-26 (v3.14.2 batch) as part of the graduation-handoff effort.
Update this file when the middleware stack, auth flow, task inventory, or
file-serving rules change — it is the successor's mental model.*
