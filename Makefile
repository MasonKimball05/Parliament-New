# Parliament — developer shortcuts (v3.14.1)
#
#   make test-fast   run the whole test suite on sqlite (no postgres needed;
#                    a few postgres-only tests self-skip — CI still runs them)
#   make test        alias for test-fast
#   make check       Django system check + migration drift check
#   make hooks       install the pre-push hook (runs test-fast before a push;
#                    bypass a single push with: git push --no-verify)

.PHONY: test-fast test check hooks

test-fast:
	DB_BACKEND=sqlite python3 manage.py test src -v 1

test: test-fast

check:
	DB_BACKEND=sqlite python3 manage.py check
	DB_BACKEND=sqlite python3 manage.py makemigrations --check --dry-run

hooks:
	install -m 755 scripts/pre-push.sh .git/hooks/pre-push
	@echo "pre-push hook installed — 'git push --no-verify' bypasses it"
