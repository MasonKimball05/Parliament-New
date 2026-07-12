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
#   commit-free.
#
#   Restore .env:
#     openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
#       -in .env.enc -out .env -pass file:/etc/parliament/snapshot.pass
#     (or -pass pass:'<passphrase>' if the pass file is gone — keep the
#      passphrase in your password manager too!)
#
# One-time setup on prod:
#   1. sudo mkdir -p /etc/parliament
#      sudo sh -c 'umask 077; openssl rand -base64 32 > /etc/parliament/snapshot.pass'
#      → save that passphrase in your password manager NOW.
#   2. Create the private GitHub repo; give prod's SSH key push access.
#   3. Set SNAPSHOT_REMOTE_URL below (or export it in the cron line).
#   4. chmod +x scripts/git_snapshot.sh
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
PASS_FILE="${SNAPSHOT_PASS_FILE:-/etc/parliament/snapshot.pass}"

log() { echo "[git_snapshot] $(date '+%F %T') $*"; }

# --- Excludes ----------------------------------------------------------------
# Heavy/reproducible stuff stays out; the DB has its own backup_db pipeline.
# .git/ is excluded from the copy AND thereby protected from --delete on the
# receiving side. .env is excluded here because it's committed encrypted.
EXCLUDES=(
  --exclude '.git/'
  --exclude '.env'
  --exclude '.env.enc'          # ours; don't let a stray app-dir copy clobber it
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
    echo "$new_hash" > "$STATE_DIR/env.sha256"
    log "re-encrypted .env (contents changed)"
  fi
else
  log "WARNING: no .env in $APP_DIR — snapshot will not contain one"
fi

# Belt-and-braces: never let a plaintext .env slip into the snapshot repo.
if [ -e "$STAGING_DIR/.env" ]; then
  rm -f "$STAGING_DIR/.env"
  log "WARNING: removed stray plaintext .env from staging"
fi

# --- Init staging repo on first run ------------------------------------------
if [ ! -d "$STAGING_DIR/.git" ]; then
  git -C "$STAGING_DIR" init -b "$BRANCH"
  git -C "$STAGING_DIR" config user.name "Parliament Snapshot"
  git -C "$STAGING_DIR" config user.email "snapshot@am-parliament.org"
  git -C "$STAGING_DIR" remote add origin "$REMOTE_URL"
  log "initialized staging repo at $STAGING_DIR"
fi
git -C "$STAGING_DIR" remote set-url origin "$REMOTE_URL"

# --- Commit + push -----------------------------------------------------------
# -f bypasses every .gitignore that rsync copied over — that's the point.
git -C "$STAGING_DIR" add -Af .

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
