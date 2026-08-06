"""
Parliament middleware package.

Deliberately empty of re-exports as of v3.18.7.

It previously re-exported four of the ten middleware classes plus four
performance helpers, via an `__all__` that read as authoritative and was
missing more than half the chain — including every security middleware
(`security.py` alone holds six). Nothing in the tree imported any of it:
`settings.MIDDLEWARE` names every middleware by full dotted path, and the two
real consumers of the performance helpers (`view/admin_v2.py`,
`management/commands/memory_report.py`) import from
`src.middleware.performance` directly.

So the list served no importer and misinformed every reader. Rather than grow
it to ten and take on keeping it in step with `settings.py`, it is gone:
**`settings.MIDDLEWARE` is the single register of what runs, and module paths
are how you import.** One place to look, and it is the place that is true by
construction because Django reads it.

Note that `activity_logging.py` lives in this package and is NOT a middleware —
see its docstring. It is loaded by `SrcConfig.ready()`.
"""
