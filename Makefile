# Parliament — developer shortcuts (v3.14.1)
#
#   make test-fast   run the whole test suite on sqlite (no postgres needed;
#                    a few postgres-only tests self-skip — CI still runs them)
#   make test        alias for test-fast
#   make check       Django system check + migration drift check
#   make hooks       install the pre-push hook (runs test-fast before a push;
#                    bypass a single push with: git push --no-verify)
#   make stamp-ledger  fill in the two release-ledger lines that cannot be
#                    written until the commit exists (see scripts/stamp_ledger.py).
#                    CHECK=1 reports without changing anything.

.PHONY: test-fast test check hooks stamp-ledger check-css

test-fast:
	DB_BACKEND=sqlite python3 manage.py test src -v 1

test: test-fast

check:
	DB_BACKEND=sqlite python3 manage.py check
	DB_BACKEND=sqlite python3 manage.py makemigrations --check --dry-run

hooks:
	install -m 755 scripts/pre-push.sh .git/hooks/pre-push
	@echo "pre-push hook installed — 'git push --no-verify' bypasses it"

stamp-ledger:
	@python3 scripts/stamp_ledger.py

# 09-25-26 — fail if static/css/tailwind.css is missing classes in use.
# TAILWIND=path/to/tailwindcss (default ./tailwindcss; see build_css.sh header).
check-css:
	python3 scripts/check_css_fresh.py $${TAILWIND:-./tailwindcss}
