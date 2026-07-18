# Parliament — Disaster Recovery Runbook

How to rebuild a working prod from the off-server snapshots. Written for a
future maintainer with server access but no tribal knowledge. Read top to
bottom once **before** you need it; run the drill (bottom) once a semester.

**What exists, where:**

| Thing | Where | How it gets there |
|---|---|---|
| Code (incl. gitignored files) | private `parliament-snapshot` repo, `main` branch | `scripts/git_snapshot.sh`, daily 4am cron |
| `.env` (encrypted) | same repo, `main`: `.env.enc` + `.env.enc.hmac` | same script, re-encrypted only when `.env` changes |
| Newest DB dump (encrypted) | same repo, **`db-dump` branch** (always exactly 1 commit): `db_latest.dump.enc` + `.hmac` | same script; branch is force-pushed, old dumps are NOT kept |
| Older DB dumps (plaintext, on-server only) | `/var/backups/parliament/*.dump` | the `backup_db` pipeline |
| Decryption passphrase | `/etc/parliament/snapshot.pass` on prod **and your password manager** | one-time setup |
| Clean source (no secrets) | `MasonKimball05/Parliament-New` on GitHub | normal pushes |

If the server is completely gone, everything you need is the snapshot repo +
the passphrase from your password manager.

---

## 1. Restore `.env`

```bash
git clone git@github.com:MasonKimball05/parliament-snapshot.git
cd parliament-snapshot
```

**Verify the HMAC first, then decrypt** (AES-CBC alone doesn't authenticate —
a tampered ciphertext would silently decrypt to garbage):

```bash
# Must print the exact contents of .env.enc.hmac:
python3 -c 'import hmac,hashlib,sys; \
  print(hmac.new(open(sys.argv[2],"rb").read().strip(), open(sys.argv[1],"rb").read(), hashlib.sha256).hexdigest())' \
  .env.enc /etc/parliament/snapshot.pass
cat .env.enc.hmac   # compare by eye — must match

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in .env.enc -out .env -pass file:/etc/parliament/snapshot.pass
```

No pass file (fresh server)? Recreate it from the password manager:
`umask 077; printf '%s' '<passphrase>' | sudo tee /etc/parliament/snapshot.pass`
(or use `-pass pass:'<passphrase>'` directly — then clear shell history).

## 2. Restore the database

```bash
git fetch origin db-dump
git checkout origin/db-dump -- db_latest.dump.enc db_latest.dump.enc.hmac
# HMAC-verify exactly as in step 1, then:
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in db_latest.dump.enc -out db_latest.dump -pass file:/etc/parliament/snapshot.pass

# If the server survived, use the existing helper (stops the app, drops,
# recreates, restores, restarts — DB name/user hardcoded in the script):
sudo ./scripts/restore_db.sh /path/to/db_latest.dump

# On a fresh server (script's service/DB assumptions not in place yet),
# do it manually — DB name/user/password come from the restored .env:
sudo -u postgres createdb <DB_NAME> -O <DB_USER>
pg_restore --no-owner --role=<DB_USER> -d <DB_NAME> db_latest.dump
```

Notes: the dump is at most ~24h old (4am cron) — anything after it is lost;
say so to the chapter rather than guessing. If the server survived, prefer the
newest plaintext dump in `/var/backups/parliament/` over the snapshot copy.
Delete the decrypted `db_latest.dump` when done.

## 3. Rebuild the app

```bash
cd /var/www && git clone git@github.com:MasonKimball05/Parliament-New.git
cd Parliament-New
# copy the restored .env here; then:
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate            # no-op if DB restored, but safe
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py setup_celery_schedules
```

Recreate/enable the three systemd units (unit files are in the snapshot
repo's `main` if they were in the app dir; otherwise `/etc/systemd/system/`
from the snapshot of that path, or rewrite from the originals):
`parliament-gunicorn` (**runs Daphne** — ASGI, HTTP + WebSockets on
`parliament.sock`; do NOT resurrect the old WSGI `parliament.service`),
`parliament-worker` (Celery worker), `parliament-beat` (Celery beat — run
`setup_celery_schedules` first, as above).

Then: re-point DNS/Cloudflare if the IP changed, and **purge Cloudflare
cache** (Caching → Purge) — it caches 404s from before files existed.

Re-arm the backups: snapshot cron + `/etc/logrotate.d/parliament-snapshot`
(see `git_snapshot.sh` header, one-time setup) and the `backup_db` pipeline.

---

## ⚠️ Standing warnings

- **SECRET_KEY rotation invalidates every outstanding vote receipt** (they're
  HMAC-keyed on it — `src/utils/vote_receipts.py`), plus all sessions and
  password-reset links. Restore the OLD key from `.env.enc`; never generate a
  fresh one during recovery unless the key itself is what leaked. If you must
  rotate: announce that receipts issued before the rotation can no longer be
  verified.
- **The `db-dump` branch keeps no history** — it is force-pushed to a single
  commit each time. It's the *newest* dump only, not an archive. On-server
  `/var/backups/parliament/` holds the retention window.
- **Snapshot `main` can still bloat slowly** (e.g. if `.env` churns or big
  binaries land in the app dir). To squash it flat:
  `cd <staging>; git checkout --orphan fresh && git commit -m "Squash $(date -u +%F)" && git branch -M fresh main && git push -f origin main`
  (next cron run continues normally on the new root).
- The passphrase file must stay `0600 root:root`; and the passphrase MUST
  exist in the password manager — the pass file dies with the server.

## Drill checklist (once a semester, ~20 min, laptop only)

1. Clone the snapshot repo somewhere disposable.
2. HMAC-verify + decrypt `.env.enc` → open it, confirm it's current-looking.
3. Fetch `db-dump`, HMAC-verify + decrypt, `pg_restore --list db_latest.dump`
   (structure check only — no DB needed).
4. Confirm the snapshot's newest commit is < 48h old (cron alive).
5. Confirm the passphrase in the password manager still decrypts (that IS
   what steps 2–3 tested — don't skip them by using the server's pass file).
6. Delete the disposable clone + decrypted files.

*Created 07-18-26 (v3.14.1). Update this file whenever the snapshot script,
unit names, or backup layout change.*
