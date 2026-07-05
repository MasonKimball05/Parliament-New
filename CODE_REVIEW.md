# Parliament — Full Code Review

**Reviewed:** July 2026 · Django 5.1.15, Python 3.13, PostgreSQL, Channels/Daphne, Celery, DRF
**Scope:** ~83k LOC of Python across `src/`, settings, middleware, templates (310 files), infra scripts.

This is a large, mature, feature-rich chapter-management app (voting, legislation, committees, chat, 2FA/passkeys, service hours, admin tooling). The security posture is unusually strong for a project this size — layered middleware, field encryption, rate limiting, honeypots, geo-restriction, CSP with nonces. The main risks are a few concrete correctness bugs, some defense-in-depth measures that give a false sense of protection, and accumulated sprawl/tech-debt. Findings are grouped and prioritized.

---

## Summary of priorities

| # | Severity | Area | Issue |
|---|----------|------|-------|
| 1 | High (bug) | Security/Correctness | Encrypted `ip_address` fields are queried with `.filter(ip_address=...)` — never matches, silently breaks anomaly detection |
| 2 | High | Config | `ALLOWED_HOSTS = ['*']` in `base_settings.py`; weak `SECRET_KEY` fallbacks |
| 3 | Medium | Security | Username/email login enumeration + non-constant-time email lookup |
| 4 | Medium | Security | `X-XSS-Protection: 1; mode=block` and reliance on regex WAF middleware |
| 5 | Medium | Architecture | Repeated view logic (4x near-identical document viewers), 3,400-line `admin_v2.py` |
| 6 | Medium | Repo hygiene | 48 MB `tailwindcss` binary + `get-docker.sh` committed; secrets-adjacent `.bak` files on disk |
| 7 | Low–Med | Error handling | 105 broad `except`, ~51 silently `pass` — masks failures |
| 8 | Low | UI/UX | Inconsistent inline `onclick=` vs CSP nonce model; a11y gaps (no skip link) |
| 9 | Low | Docs | ~25 top-level markdown design/status docs; hard to tell current source of truth |

---

## 1. Security review

### What's done well
- **Real password hashing** via Django `authenticate()`; custom validators including a `PwnedPasswordValidator` and 9-char minimum.
- **Field-level encryption** (Fernet / AES-128-CBC+HMAC) for IPs and similar, with a production guard that refuses to boot without `ENCRYPTION_KEY`.
- **Layered auth**: password + TOTP 2FA + WebAuthn passkeys, with policy-driven enforcement middleware and impersonation that correctly cycles the session and preserves an audit trail (`login_as_view.py`).
- **Rate limiting** on login, per-account (distributed-attack aware) and per-IP, password reset, and passkey endpoints, with DB-persisted lockouts for admin visibility.
- **Secrets are not committed** — `git ls-files` shows only `.env.example` / `.env.dev.example`; `.env`, `users.json`, `data_backup.json`, backups are all gitignored.
- **Nginx blocks `/media/` directly**; all document downloads route through `@login_required` views with object-level `can_user_view()` checks.
- **CSP** with per-request nonce and no `unsafe-inline` for scripts; HSTS, nosniff, referrer-policy, permissions-policy all set in production.
- **`serve_exportable_media`** correctly uses `realpath` + prefix check to prevent path traversal.
- **CSRF `next`/redirect** validated with `url_has_allowed_host_and_scheme`.

### Findings

**1.1 — HIGH (functional bug): encrypted IP fields are filtered by equality and will never match.**
`LoginHistory.ip_address` is an `EncryptedCharField` (Fernet, *non-deterministic* ciphertext), yet the risk engine queries it with equality:

```python
# src/utils/security_utils.py:247
known_ips = previous_logins_base.filter(ip_address=ip_address)
if not known_ips.exists():
    risk_factors.append(f'New IP address: {ip_address}')
```

Because Fernet produces different ciphertext for the same plaintext every time, this filter matches *nothing* — every login is scored as a "new IP," and any other code relying on equality lookups against encrypted columns is similarly broken. This silently degrades the anomaly detection the system advertises. Fix by either (a) storing a deterministic keyed HMAC/`blind index` column for lookups alongside the encrypted value, or (b) decrypting-and-comparing in Python over the recent slice (fine given the queryset is already limited to the last N). Audit every `.filter(<encrypted_field>=...)` across the codebase.

**1.2 — HIGH: permissive host/secret fallbacks in `base_settings.py`.**
`ALLOWED_HOSTS = ['*']` and `SECRET_KEY_GENERAL = os.getenv(..., 'fallback-secret')`. Production runs on `settings_postgres` (which fixes both), but `base_settings` is a live import path (`settings_sqlite` inherits it) and the insecure defaults are one misconfigured `DJANGO_SETTINGS_MODULE` away from being served. Remove the `'*'` and the literal fallback secret entirely; fail loudly instead. Same treatment for `dev-only-insecure-secret-key...` — keep it strictly gated behind `DEBUG`.

**1.3 — MEDIUM: login user-enumeration surface.**
`login_view` accepts email-or-username, does `User.objects.get(email__iexact=username)` before authenticating, and the per-account lockout keys off the submitted username. Differences in response/timing between "valid user, wrong password" and "no such user" allow enumeration, and an attacker can trigger account-specific lockouts (a targeted DoS) by name. Mitigate with a constant-time dummy `check_password` on the miss path and consider not revealing lockout-by-username to unauthenticated callers.

**1.4 — MEDIUM: the regex "WAF" middleware is defense-in-theater and worth right-sizing.**
`InputSanitizationMiddleware` scans query/POST/path for SQL/XSS patterns. Django's ORM already parameterizes and templates auto-escape, so this adds little real protection while creating (a) false positives that forced a growing `skip_paths` allowlist (`/legislation/`, `/contact/submit/`, the landing editor…), and (b) a maintenance and performance cost. It also *skips scanning for all authenticated users*, so it only ever inspects anonymous traffic. Keep the IP blacklist enforcement and security headers; strongly consider dropping the pattern-matching or moving it to logging-only. Relatedly, `X-XSS-Protection: 1; mode=block` is deprecated and can *introduce* vulnerabilities in older browsers — modern guidance is `0` and rely on CSP.

**1.5 — MEDIUM: `csrf_exempt` on public POST endpoints.**
`contact_submit` and the honeypot endpoints are `@csrf_exempt`. For the honeypots that's intentional and fine. For `contact_submit` it's a real unauthenticated write endpoint — it is rate-limited (good) but consider adding CSRF via a token embedded in the public page or a hCaptcha/turnstile, since it can send emails to officer addresses.

**1.6 — LOW/MEDIUM: geolocation over plaintext HTTP.**
`security_utils.get_geolocation_from_ip` calls `http://ip-api.com/...` (no TLS) on the login hot path. IPs and login timing traverse the network in cleartext, and it's a 3s blocking call on every foreign login. Use the HTTPS endpoint and/or move it off the request path.

**1.7 — LOW: `X_FRAME_OPTIONS='SAMEORIGIN'` + document iframes.**
Intentional for the viewer, but combined with `img-src https:` and DOCX→HTML rendering it's worth confirming the mammoth output is sanitized (it strips `style`/`class` via regex but doesn't run through bleach; `{{ docx_html|safe }}` trusts it). Regex stripping of attributes is fragile — route DOCX HTML through `bleach.clean` like the landing editor already does.

**1.8 — Verify:** `DATA_UPLOAD_MAX_MEMORY_SIZE = 20MB` with `FILE_UPLOAD_MAX_MEMORY_SIZE = 20MB` means large uploads never spill to disk and are held in RAM per worker — combined with the RAM-optimization focus in the repo, confirm this is intended.

---

## 2. Architecture & code quality

**2.1 — Duplication in document viewers.** `view_document.py` has four ~50-line view functions (`view_legislation_document`, `view_chapter_document`, `view_committee_document`, `view_passed_legislation_document`) that are near-identical: fetch object → `get_file_type_info` → conditionally convert DOCX/PDF/text → build the same context dict. Extract a single helper `_render_document(request, document_field, title, type, back_url, ...)`. This is the clearest quick win.

**2.2 — God-module `admin_v2.py` (3,469 lines).** Combined with `kai_reports.py` (1,822) and `service_hours.py` (1,169), these are hard to test and navigate. Split by feature area into a package (`view/admin_v2/security.py`, `.../users.py`, etc.). The `src/view/` directory already mixes single-file views and subpackages (`chat/`, `officer/`, `slating/`) — standardize on packages.

**2.3 — Error handling swallows failures.** 105 broad `except Exception:` and ~51 that just `pass`. Examples: LoginLockout creation, geo lookups, watch-flag alerts. Some are deliberately non-fatal, but blanket `except: pass` hides real bugs (e.g. the encrypted-filter issue above would surface faster with narrower handling and logging). Prefer specific exceptions and always log at `warning`.

**2.4 — Migrations are gitignored.** `.gitignore` excludes `**/migrations/` (keeping only `__init__.py`). For a project with a real Postgres database and CI that runs `makemigrations` at test time, not tracking migrations means schema history isn't reproducible, review can't see schema changes, and two developers can generate divergent migrations. This is a significant process risk — strongly recommend committing migrations.

**2.5 — Query performance.** Good news: 243 uses of `select_related`/`prefetch_related`, so N+1 awareness is present. Worth a targeted pass with `django-debug-toolbar` on the heavy pages (admin_v2 dashboards, kai_reports, directory) to confirm.

**2.6 — Dependencies.** Pinned versions are current and reasonable. Note `Django==5.1.15` is a 5.1.x LTS-adjacent line; keep an eye on 5.2 upgrade path. `requests==2.33.1` and `psycopg2` + `psycopg2-binary` both listed (pick one; binary for dev, source for prod is a known pattern but redundant here).

**2.7 — Test suite exists (~6,500 LOC across `test_*.py`) and CI runs on Postgres.** Strong. Consider adding coverage reporting to CI and a regression test that would have caught 1.1 (assert a repeat login from the same IP is *not* flagged "new IP").

---

## 3. UI/UX & frontend

**3.1 — CSP nonce vs. inline handlers inconsistency.** The security model states all inline `onclick=` were removed in favor of `addEventListener`, and `script-src` has no `unsafe-inline`. But 36 inline `on*=` handlers remain across ~20 templates (e.g. `committee/education.html` has 16, `base.html` has 2 like `onclick="dismissChatUnread(event)"`). Inline *event handler attributes* aren't blocked by `script-src` nonce policy (they'd need `script-src-attr 'unsafe-inline'` / `unsafe-hashes`), so these currently work — but they contradict the stated model and will silently break if the CSP is tightened. Migrate them to `addEventListener` for consistency.

**3.2 — Accessibility.** Decent baseline: `lang="en"`, viewport meta, 127 `aria-*` attributes, 29 `role=`, 342 `<label for>` for 903 inputs, and heavy `focus:ring` usage (1,472). Gaps: no visible "skip to content" link in `base.html`; label-to-input ratio (~38%) suggests many inputs rely on placeholder-only labeling — audit forms for programmatic labels. The duplicate coat-of-arms `<img>` in base has `alt=""` in one place and a real alt in another (fine, but be deliberate about decorative vs. informative).

**3.3 — Dark mode** is implemented thoughtfully (pre-paint inline script to avoid flash, `auto` respecting `prefers-color-scheme`). Good.

**3.4 — Client-side data islands.** Several templates do `{{ foo|safe }}` into `<script>` (e.g. `roles_json|safe`, `category_data|safe`). The custom `jsonify` filter escapes properly, but confirm the `|safe` JSON blobs are produced with `json_script` or an escaping serializer, not raw `json.dumps`, to avoid `</script>` breakout.

---

## 4. Repo hygiene & infra

**4.1 — 48 MB `tailwindcss` standalone binary is committed** (git pack is 96 MB total). Also `get-docker.sh` (22 KB vendored installer). Remove both from history (download in build step / `build_css.sh`); the repo will shrink dramatically and clones speed up.

**4.2 — Stale/secret-adjacent files on disk:** `.env`, `.env.dev`, `.env.postgres.bak`, `users.json`, `data_backup.json` (460 KB) all exist in the working tree. They're gitignored (good) but `.bak` and data backups with PII/hashes sitting in the repo folder are an exfiltration risk if the directory is ever archived/shared. Move them out of the project tree.

**4.3 — Documentation sprawl.** ~25 top-level markdown files (`SECURITY.md`, `SECURITY_GUIDE.md`, `LOGIN_SECURITY.md`, `PASSWORD_RESET_SECURITY.md`, multiple RAM/SYSTEMD guides, `TEST_*`, design docs). Valuable content, but overlapping and hard to tell what's current. Consolidate into a `docs/` tree with an index, and archive superseded design notes.

**4.4 — `DEBUG` toggles static/media serving** in both `Parliament/urls.py` and `src/urls.py`; ensure production truly runs with `DEBUG=False` (it's env-driven and defaults False — good) so those routes stay disabled.

---

## Recommended order of attack

1. **Fix 1.1** (encrypted-field equality filter) and add the regression test — it's a silent correctness failure in a security feature.
2. **Harden config** (1.2): remove `['*']` and literal secret fallbacks from `base_settings.py`.
3. **Commit migrations** (2.4) — process risk that compounds over time.
4. **Purge the 48 MB binary from git history** (4.1).
5. Right-size the WAF middleware and fix `X-XSS-Protection` (1.4); route DOCX HTML through bleach (1.7).
6. Refactor the four document views (2.1) and start splitting `admin_v2.py` (2.2).
7. UI consistency: migrate inline handlers to listeners (3.1); add a skip link and audit form labels (3.2).

Overall: a genuinely impressive amount of security engineering for a chapter app — the priority is making sure those features actually *work* (1.1), that insecure fallbacks can't leak into production (1.2), and that the repo/process debt (migrations, giant binary, doc sprawl) gets paid down before it slows the team.
