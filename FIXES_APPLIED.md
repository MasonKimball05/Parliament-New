# Fixes Applied — Security & Code Review Follow-up

**Date:** July 2, 2026
**Context:** Fixes made in response to the findings in `CODE_REVIEW.md`.
**Scope:** 4 security issues and 1 refactor. All changes byte-compile cleanly; the DOCX sanitizer was functionally tested in isolation.

---

## Summary

| Ref | Severity | Status | Area | File(s) |
|-----|----------|--------|------|---------|
| 1.1 | High (bug) | ✅ Fixed | Encrypted IP filter never matched | `src/utils/security_utils.py` |
| 1.4 | Medium | ✅ Fixed | Deprecated `X-XSS-Protection` header | `src/middleware/security.py`, `Parliament/settings_postgres.py` |
| 1.7 | Medium | ✅ Fixed | Unsanitized DOCX→HTML preview | `src/view/view_document.py` |
| 1.6 | Low–Med | ✅ Mitigated | Geo lookups over cleartext HTTP | `src/geo_utils.py`, `src/utils/security_utils.py`, settings, `.env.example` |
| 2.1 | Medium | ✅ Refactored | Duplicated document-viewer logic | `src/view/view_document.py` |

Deliberately **not** changed: 1.3 (login enumeration) and 1.5 (`csrf_exempt` on contact form). Reasoning below.

---

## 1.1 — Encrypted IP filter bug (High)

**Problem.** `LoginHistory.ip_address` is an `EncryptedCharField` (Fernet / AES). Fernet ciphertext is non-deterministic — the same IP encrypts to a different value every time — so the anomaly engine's equality query never matched:

```python
# before — always returned empty, so every login looked like a "new IP"
known_ips = previous_logins_base.filter(ip_address=ip_address)
```

**Fix.** Decrypt and compare in Python over a bounded slice of recent logins:

```python
# after
recent_ip_addresses = {
    login.ip_address for login in previous_logins_base[:100]
}
if ip_address not in recent_ip_addresses:
    risk_factors.append(f'New IP address: {ip_address}')
    risk_score += 5
```

**Effect.** "Known IP" detection now works; repeat logins from the same IP are no longer falsely scored as new.

**File:** `src/utils/security_utils.py` (`analyze_login_risk`)

> Note: the only other equality filter on an IP column (`src/view/admin_v2.py:2788`) is on `HoneypotAccess.ip_address`, which is **not** encrypted, so it was correct and left unchanged.

---

## 1.4 — Deprecated `X-XSS-Protection` header (Medium)

**Problem.** The app emitted `X-XSS-Protection: 1; mode=block`. This header is deprecated and can *introduce* vulnerabilities in some older browsers. Modern guidance is to disable it and rely on CSP (which the app already sets with a per-request nonce).

**Fix.**

- `src/middleware/security.py` — header value changed to `0`:
  ```python
  response['X-XSS-Protection'] = '0'
  ```

**Correction (07-05-26).** The original fix also set `SECURE_BROWSER_XSS_FILTER = False` in `Parliament/settings_postgres.py`, but that setting was removed in Django 3.0 — on Django 5.1 it's dead config with no effect (`SecurityMiddleware` no longer emits the header at all). The line has been deleted; the middleware header above is the complete fix.

**Files:** `src/middleware/security.py`

---

## 1.7 — Unsanitized DOCX→HTML preview (Medium)

**Problem.** `convert_docx_to_html()` used mammoth to convert uploaded `.docx` files, stripped `style`/`class` with regex, then rendered the result via `{{ docx_html|safe }}`. Regex attribute-stripping is fragile and did not remove scripts or event handlers — a crafted document could inject markup.

**Fix.** Route mammoth output through `bleach.clean()` with a strict tag/attribute allowlist (same approach already used by the landing-page editor):

```python
_DOCX_ALLOWED_TAGS = ['p','br','b','i','em','strong','u','s','strike','a',
    'blockquote','ol','ul','li','h1'..'h6','table','thead','tbody','tfoot',
    'tr','td','th','img','hr','span','div','sup','sub','pre','code',
    'figure','figcaption']
_DOCX_ALLOWED_ATTRS = {'a': ['href','title','target','rel'],
                       'img': ['src','alt','width','height']}

html = bleach.clean(html, tags=_DOCX_ALLOWED_TAGS,
                    attributes=_DOCX_ALLOWED_ATTRS, strip=True)
```

**Verification (tested in isolation):**

| Input | Result |
|-------|--------|
| `<script>alert(1)</script>` | tag removed |
| `<img src=x onerror="alert(1)">` | `onerror` stripped → `<img src="x">` |
| `<p style="position:fixed" class="evil">` | `style`/`class` removed |
| `<a href="javascript:alert(1)">` | `href` stripped |
| headings / bold / italic / tables | preserved |

**File:** `src/view/view_document.py`

---

## 1.6 — Geo lookups over cleartext HTTP (Low–Medium)

**Problem.** IP geolocation (used for login anomaly / foreign-login detection) called `http://ip-api.com/...` on the login path, sending IPs and login timing in cleartext.

**Fix.** Made the provider base URL configurable via a new `GEO_API_BASE_URL` setting / env var, in both geolocation code paths:

- `src/geo_utils.py` (`get_ip_geo`)
- `src/utils/security_utils.py` (`get_geolocation_from_ip`)
- `Parliament/settings_postgres.py` — `GEO_API_BASE_URL = os.getenv('GEO_API_BASE_URL', 'http://ip-api.com/json/')`
- `.env.example` — documented the variable

**Why the default is still HTTP.** ip-api.com's **free** tier is HTTP-only; hard-coding `https://` would silently break lookups. Operators can now point `GEO_API_BASE_URL` at an HTTPS/TLS endpoint (e.g. an ip-api.com Pro URL) to eliminate the cleartext exposure without a code change.

**Files:** `src/geo_utils.py`, `src/utils/security_utils.py`, `Parliament/settings_postgres.py`, `.env.example`

---

## 2.1 — Document-viewer duplication (Refactor)

**Problem.** Four near-identical view functions (`view_legislation_document`, `view_chapter_document`, `view_committee_document`, `view_passed_legislation_document`) each repeated ~40 lines of file-type detection + DOCX/PDF/text conversion + context assembly.

**Fix.** Extracted a shared helper:

```python
def _build_document_context(document_field, *, title, document_type,
                            back_url, description=None,
                            uploaded_by=None, uploaded_at=None):
    ...
```

Each view now contains only its object lookup and permission checks, then calls the helper. `view_reference_document` was intentionally left as-is (it serves from a `MEDIA_URL` path rather than a model `FileField`).

**File:** `src/view/view_document.py`

---

## Intentionally deferred

**1.3 — Login user-enumeration / timing.** Django's `ModelBackend` already runs a dummy password hash on the no-user path, so authentication timing is largely equalized, and the invalid-credentials message is already generic. The one residual signal — the per-username lockout message — is a deliberate UX tradeoff. Not changed to avoid rewriting a critical auth path for marginal gain.

**1.5 — `csrf_exempt` on `contact_submit`.** A real fix requires a token or captcha (e.g. Cloudflare Turnstile / hCaptcha) on the public page, not just a server-side change. The endpoint is already IP rate-limited (5 / 10 min).

---

## Observation found during this work (not yet actioned)

`src/signals.py` (`user_logged_in` receiver) and `run_post_auth_pipeline()` in `src/utils/security_utils.py` **both** create a `LoginHistory` row, so each successful login likely writes two records. Worth confirming and de-duplicating, but left untouched here to avoid destabilizing the login flow.

---

## Verification performed

- AST parse + `py_compile` on all edited files — passed.
- DOCX sanitizer functionally tested against script/`onerror`/`style`/`javascript:` payloads — all stripped; legitimate formatting preserved.

**Not run in this environment** (macOS virtualenv can't execute under the Linux sandbox, and a full boot needs the entire dependency tree): `python manage.py check` and the login/security test suites. **Recommend running both in your environment before committing.**

## Files changed

- `src/utils/security_utils.py`
- `src/middleware/security.py`
- `src/view/view_document.py`
- `src/geo_utils.py`
- `Parliament/settings_postgres.py` *(git submodule — tracked separately)*
- `.env.example`
