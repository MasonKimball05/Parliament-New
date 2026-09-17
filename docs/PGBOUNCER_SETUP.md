# pgbouncer setup — connection pooling for Parliament

Added 09-16-26 after a live incident (see `pgbouncer.ini`'s header comment
and `src/tasks/db_health.py` for the full story): a burst of concurrent
pledge-onboarding logins exhausted Postgres's `max_connections`. This
document walks through installing pgbouncer so that stops being possible —
demand above the pool size gets briefly queued instead of refused.

**Do this during a quiet window, not during a meeting or while people are
actively using the site.** Every step up through "Test pgbouncer directly"
is non-disruptive (pgbouncer runs alongside the existing direct connection,
nothing is switched over yet). The cutover step (pointing Django's `.env` at
pgbouncer and restarting) is the one moment of brief disruption — a few
seconds of dropped connections, same as any other app restart.

## 1. Install pgbouncer

```bash
sudo apt update
sudo apt install pgbouncer
```

This starts a default `pgbouncer` systemd service pointed at a default
config — leave it stopped for now, we're replacing the config before it
matters:

```bash
sudo systemctl stop pgbouncer
```

## 2. Get the real database name

```bash
grep DB_NAME /var/www/Parliament-New/.env
```

Note the value — you'll substitute it for `PARLIAMENT_DB_NAME` in the next
two steps.

## 3. Install the config

Copy `pgbouncer.ini` from this repo to the server (it's already checked in
at the repo root — `git pull` on the server gets it there), then:

```bash
sudo cp /var/www/Parliament-New/pgbouncer.ini /etc/pgbouncer/pgbouncer.ini
sudo sed -i 's/PARLIAMENT_DB_NAME/<the real DB_NAME value>/g' /etc/pgbouncer/pgbouncer.ini
```

Run the `sed` with the actual name substituted in — e.g. if `DB_NAME` is
`parliament_prod`, run `sed -i 's/PARLIAMENT_DB_NAME/parliament_prod/g' ...`.

## 4. Generate the userlist — no plaintext password needed

pgbouncer needs the database user's password, but Postgres can hand over the
already-hashed SCRAM secret directly — you never need to type or store the
plaintext password anywhere for this:

```bash
sudo -u postgres psql -c "SELECT usename, passwd FROM pg_shadow WHERE usename='<DB_USER value from .env>';"
```

That prints something like:

```
 usename  |                    passwd
----------+-----------------------------------------------
 parl_app | SCRAM-SHA-256$4096:abc123.../base64stuff==
```

Put that directly into pgbouncer's userlist, quoting both fields exactly as
shown (including the `SCRAM-SHA-256$...` string as-is):

```bash
sudo bash -c 'echo "\"parl_app\" \"SCRAM-SHA-256\$4096:abc123.../base64stuff==\"" > /etc/pgbouncer/userlist.txt'
sudo chown postgres:postgres /etc/pgbouncer/userlist.txt
sudo chmod 600 /etc/pgbouncer/userlist.txt
```

(Substitute the real username and the real hash string you got back from the
`SELECT` above — don't literally copy the placeholder text here.)

## 5. Start pgbouncer and test it directly — Postgres itself is untouched so far

```bash
sudo systemctl start pgbouncer
sudo systemctl enable pgbouncer
sudo systemctl status pgbouncer
```

Test a connection THROUGH pgbouncer without touching the app at all:

```bash
psql "host=127.0.0.1 port=6432 dbname=<DB_NAME> user=<DB_USER>" -c "SELECT 1;"
```

If that returns `1`, pgbouncer is correctly proxying to Postgres. If it
fails, check `sudo tail -50 /var/log/postgresql/pgbouncer.log` before going
any further — do NOT proceed to cutover until this works.

## 6. Cutover — the one step with a brief blip

Edit `/var/www/Parliament-New/.env`:

```
DB_HOST=127.0.0.1
DB_PORT=6432
```

(These were `localhost` / `5432` before — now pointing at pgbouncer instead
of Postgres directly. Everything else in `.env` — `DB_NAME`, `DB_USER`,
`DB_PASSWORD` — stays the same, since pgbouncer forwards those through to
the real Postgres connection underneath.)

Then restart everything that talks to the database — this is the moment of
brief disruption:

```bash
sudo systemctl restart parliament-gunicorn.service parliament-worker parliament-beat
```

## 7. Verify

```bash
sudo -u postgres psql -c "SHOW max_connections;"
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"
```

You should now see far fewer direct Postgres connections than before — most
of what used to be individual Django/Celery connections are now backend
connections held by pgbouncer's pool (capped at `default_pool_size = 20` per
`pgbouncer.ini`), with everything above that briefly queued inside pgbouncer
instead of erroring.

Also check the app itself still works end to end — log in, load a few pages,
check `/admin-v2/dashboard/`.

## Rolling back

If anything looks wrong after cutover, revert is one `.env` edit + restart:

```
DB_HOST=localhost
DB_PORT=5432
```

```bash
sudo systemctl restart parliament-gunicorn.service parliament-worker parliament-beat
```

That's it — pgbouncer can keep running harmlessly in the background even
while unused, or `sudo systemctl stop pgbouncer` to fully back out.

## After this is live and stable

Once pgbouncer has been running cleanly for a while, it's worth revisiting
`DB_CONN_MAX_AGE=300` in `Parliament/settings.py` — with pgbouncer's own
`server_idle_timeout=60` doing the real work of releasing idle Postgres
connections, Django's 5-minute persistent-connection setting matters much
less (and could reasonably come down, since the connection it's holding open
is now the cheap pgbouncer client-side one either way). Not urgent — raised
here so it isn't forgotten, not because it needs to happen immediately.
