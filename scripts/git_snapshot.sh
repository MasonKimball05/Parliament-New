#!/usr/bin/env bash
# ============================================================================
# git_snapshot.sh — periodic full-code snapshot of the prod app dir to a
# private git repo, INCLUDING gitignored files (except secrets + heavy junk).
#
# Why not just `git add -A && git push` in the app repo?
#   Prod's working tree is a `git pull` of GitHub, so tracked files never
#   differ from HEAD, and gitignored files are skipped by `git add` — hence
#   the old script's constant "no changes detected". This script instead
#   rsyncs the app dir into a dedicated staging repo and `git add -Af`
#   (force) so ignore rules don't apply.
#
# Secrets:
#   .env is NEVER committed in plaintext. It is encrypted with a passphrase
#   (AES-256, openssl — nothing to install) and committed as .env.enc.
#   Re-encrypted only when .env actually changes, so unchanged runs stay
#   commit-free. Each *.enc file gets a companion *.enc.hmac (HMAC-SHA256,
#   keyed with the same passphrase) so tampering with the ciphertext in the
#   snapshot repo is detectable — AES-CBC alone does not authenticate.
#
#   Restore .env (verify FIRST, then decrypt):
#     python3 -c 'import hmac,hashlib,sys; \
#       print(hmac.new(open(sys.argv[2],"rb").read().strip(), open(sys.argv[1],"rb").read(), hashlib.sha256).hexdigest())' \
#       .env.enc /etc/parliament/snapshot.pass   # must equal contents of .env.enc.hmac
#     openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
#       -in .env.enc -out .env -pass file:/etc/parliament/snapshot.pass
#     (or -pass pass:'<passphrase>' if the pass file is gone — keep the
#      passphrase in your password manager too!)
#   Same procedure for db_latest.dump.enc / db_latest.dump.enc.hmac.
#
#   DB dump lives on its own `db-dump` branch (single orphan commit,
#   force-pushed each time the dump changes) so daily re-encrypted dumps
#   never accumulate in main's history — encrypted blobs don't delta-compress,
#   and GitHub hard-fails pushes on files >100 MB. To restore:
#     git fetch origin db-dump && git checkout origin/db-dump -- db_latest.dump.enc db_latest.dump.enc.hmac
#
# One-time setup on prod:
#   1. sudo mkdir -p /etc/parliament
#      sudo sh -c 'umask 077; openssl rand -base64 32 > /etc/parliament/snapshot.pass'
#      → save that passphrase in your password manager NOW.
#   2. Create the private GitHub repo; give prod's SSH key push access.
#   3. Set SNAPSHOT_REMOTE_URL below (or export it in the cron line).
#   4. chmod +x scripts/git_snapshot.sh
#   5. Log rotation: sudo cp scripts/logrotate.parliament-snapshot /etc/logrotate.d/parliament-snapshot
#
# Suggested cron (sudo crontab -e), daily 4am:
#   0 4 * * * SNAPSHOT_REMOTE_URL=git@github.com:MasonKimball05/parliament-snapshot.git /var/www/Parliament-New/scripts/git_snapshot.sh >> /var/log/parliament/git_snapshot.log 2>&1
# ============================================================================
set -euo pipefail

# --- Config (override via environment) --------------------------------------
APP_DIR="${SNAPSHOT_APP_DIR:-/var/www/Parliament-New}"
STAGING_DIR="${SNAPSHOT_STAGING_DIR:-/var/backups/parliament-code-snapshot}"
STATE_DIR="${STAGING_DIR}.state"                  # not committed
REMOTE_URL="${SNAPSHOT_REMOTE_URL:?Set SNAPSHOT_REMOTE_URL to the private repo SSH URL}"
BRANCH="${SNAPSHOT_BRANCH:-main}"
DUMP_BRANCH="${SNAPSHOT_DUMP_BRANCH:-db-dump}"
DUMP_DIR="${SNAPSHOT_DUMP_DIR:-/var/backups/parliament}"
PASS_FILE="${SNAPSHOT_PASS_FILE:-/etc/parliament/snapshot.pass}"

log() { echo "[git_snapshot] $(date '+%F %T') $*"; }

# HMAC-SHA256 of $1 keyed with the snapshot passphrase → $1.hmac
# (python3 so the key never appears in argv; AES-CBC alone is malleable)
hmac_file() {
  python3 - "$1" "$PASS_FILE" > "$1.hmac" <<'PY'
import hashlib, hmac, sys
data = open(sys.argv[1], 'rb').read()
key = open(sys.argv[2], 'rb').read().strip()
print(hmac.new(key, data, hashlib.sha256).hexdigest())
PY
}

# --- Excludes ----------------------------------------------------------------
# Heavy/reproducible stuff stays out; the DB has its own backup_db pipeline.
# .git/ is excluded from the copy AND thereby protected from --delete on the
# receiving side. .env is excluded here because it's committed encrypted.
EXCLUDES=(
  --exclude '.git/'
  --exclude '.env*'             # ALL .env variants (.env.dev, .env.bak, …); also protects staging's .env.enc(+.hmac) from --delete
  --exclude 'db_latest.dump.enc'      # ours (see DB dump section); protect from --delete
  --exclude 'db_latest.dump.enc.hmac'
  --exclude 'venv/'
  --exclude '.venv/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude 'node_modules/'
  --exclude 'staticfiles/'      # collectstatic output — reproducible
  --exclude 'media/'            # user uploads — not code (flip if you want them)
  --exclude '*.dump'
  --exclude '*.sqlite3'
  --exclude '*.log'
)

# --- Sanity ------------------------------------------------------------------
[ -d "$APP_DIR" ] || { log "ERROR: APP_DIR $APP_DIR not found"; exit 1; }
command -v rsync >/dev/null || { log "ERROR: rsync not installed"; exit 1; }
mkdir -p "$STAGING_DIR" "$STATE_DIR"

# --- Copy code (including gitignored files) into staging ---------------------
rsync -a --delete "${EXCLUDES[@]}" "$APP_DIR/" "$STAGING_DIR/"

# --- Encrypt .env (only when it changed — keeps unchanged runs commit-free) --
if [ -f "$APP_DIR/.env" ]; then
  if [ ! -r "$PASS_FILE" ]; then
    log "ERROR: passphrase file $PASS_FILE missing/unreadable — refusing to snapshot without encrypted .env"
    exit 1
  fi
  new_hash=$(sha256sum "$APP_DIR/.env" | cut -d' ' -f1)
  old_hash=$(cat "$STATE_DIR/env.sha256" 2>/dev/null || true)
  if [ "$new_hash" != "$old_hash" ] || [ ! -f "$STAGING_DIR/.env.enc" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
      -in "$APP_DIR/.env" -out "$STAGING_DIR/.env.enc" -pass "file:$PASS_FILE"
    hmac_file "$STAGING_DIR/.env.enc"
    echo "$new_hash" > "$STATE_DIR/env.sha256"
    log "re-encrypted .env (contents changed)"
  fi
else
  log "WARNING: no .env in $APP_DIR — snapshot will not contain one"
fi

# --- Off-server copy of newest DB dump (encrypted + authenticated) -----------
# The dump is NOT committed to $BRANCH (see push section: it goes to
# $DUMP_BRANCH as a single force-pushed orphan commit). dump_changed=1 tells
# the push section a fresh push is needed.
dump_changed=0
latest_dump=$(ls -t "$DUMP_DIR"/*.dump 2>/dev/null | head -1 || true)
if [ -z "$latest_dump" ]; then
  log "WARNING: no *.dump in $DUMP_DIR — DB dump not snapshotted (is backup_db running?)"
elif [ ! -r "$PASS_FILE" ]; then
  log "WARNING: passphrase file $PASS_FILE missing/unreadable — DB dump not snapshotted"
else
  dump_hash=$(sha256sum "$latest_dump" | cut -d' ' -f1)
  old_dump_hash=$(cat "$STATE_DIR/dump.sha256" 2>/dev/null || true)
  if [ "$dump_hash" != "$old_dump_hash" ] || [ ! -f "$STAGING_DIR/db_latest.dump.enc" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
      -in "$latest_dump" -out "$STAGING_DIR/db_latest.dump.enc" -pass "file:$PASS_FILE"
    hmac_file "$STAGING_DIR/db_latest.dump.enc"
    dump_changed=1
    log "re-encrypted newest DB dump ($(basename "$latest_dump"))"
  fi
fi

# Belt-and-braces: never let ANY plaintext .env* slip into the snapshot repo.
find "$STAGING_DIR" -name '.env*' ! -name '.env.enc' ! -name '.env.enc.hmac' -not -path '*/.git/*' \
  -exec rm -fv {} + | while read -r f; do log "WARNING: removed stray $f from staging"; done

# --- Init staging repo on first run ------------------------------------------
if [ ! -d "$STAGING_DIR/.git" ]; then
  git -C "$STAGING_DIR" init -b "$BRANCH"
  git -C "$STAGING_DIR" config user.name "Parliament Snapshot"
  git -C "$STAGING_DIR" config user.email "snapshot@am-parliament.org"
  git -C "$STAGING_DIR" remote add origin "$REMOTE_URL"
  log "initialized staging repo at $STAGING_DIR"
fi
git -C "$STAGING_DIR" remote set-url origin "$REMOTE_URL"

# --- Commit + push (code branch — dump excluded) -----------------------------
# -f bypasses every .gitignore that rsync copied over — that's the point.
# The ':!' pathspecs keep the dump off $BRANCH so daily dump blobs never
# accumulate in code history; one-time `rm --cached` migrates repos that
# committed it before this change.
git -C "$STAGING_DIR" rm --cached --quiet -- db_latest.dump.enc db_latest.dump.enc.hmac 2>/dev/null || true
git -C "$STAGING_DIR" add -Af -- . ':!db_latest.dump.enc' ':!db_latest.dump.enc.hmac'

src_commit=$(GIT_OPTIONAL_LOCKS=0 git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo 'unknown')

if git -C "$STAGING_DIR" diff --cached --quiet; then
  log "no changes since last snapshot (app @ $src_commit)"
else
  git -C "$STAGING_DIR" commit -m "Snapshot $(date -u '+%F %T UTC') — app @ $src_commit"
  log "committed snapshot (app @ $src_commit)"
fi

# Always push — recovers cleanly if a previous run committed but failed to push.
git -C "$STAGING_DIR" push -u origin "$BRANCH"
log "push complete"

# --- Push DB dump as a single orphan commit on $DUMP_BRANCH ------------------
# Plumbing (hash-object/mktree/commit-tree), no checkout: the branch is
# recreated parentless and force-pushed, so the remote always holds exactly
# one commit — superseded blobs become unreachable and GitHub GCs them.
if [ "$dump_changed" = 1 ] && [ -f "$STAGING_DIR/db_latest.dump.enc" ]; then
  dump_blob=$(git -C "$STAGING_DIR" hash-object -w db_latest.dump.enc)
  hmac_blob=$(git -C "$STAGING_DIR" hash-object -w db_latest.dump.enc.hmac)
  dump_tree=$(printf '100644 blob %s\tdb_latest.dump.enc\n100644 blob %s\tdb_latest.dump.enc.hmac\n' \
    "$dump_blob" "$hmac_blob" | git -C "$STAGING_DIR" mktree)
  dump_commit=$(git -C "$STAGING_DIR" commit-tree "$dump_tree" \
    -m "DB dump $(date -u '+%F %T UTC') — $(basename "$latest_dump")")
  git -C "$STAGING_DIR" push -f origin "$dump_commit:refs/heads/$DUMP_BRANCH"
  # State written only after a successful push — a failed push retries next run.
  echo "$dump_hash" > "$STATE_DIR/dump.sha256"
  log "force-pushed dump to $DUMP_BRANCH ($(basename "$latest_dump"))"
  # Drop superseded local dump blobs so staging disk doesn't grow either.
  git -C "$STAGING_DIR" gc --prune=now --quiet || true
fi
