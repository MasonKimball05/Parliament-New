"""
One shape for "there is exactly one row of this, and reading it must not
write it" — v3.19.11.

⚠️ WHY THIS FILE EXISTS, AND IT IS THE PATTERN THIS CODEBASE KEEPS RECORDING.

v3.19.10 found that `SystemLockdown.get_instance()` was `get_or_create(pk=1)`
under middleware that runs on every request — a write on the read path — and
fixed it correctly, with a cached sentinel for the absent row so that dropping
the INSERT did not trade it for an uncached SELECT forever. Good fix.

**It was applied to the model that was being looked at, not to the population
the defect belongs to.** `LandingPageContent.get_instance()` is the same three
lines, and it sits on the *public, unauthenticated* landing page — so the
anonymous front door of this application issued `INSERT INTO
src_landingpagecontent` while serving a GET, on any install where the row had
not been created yet. Measured 08-19-26: one INSERT on the first anonymous
`GET /`.

CLAUDE.md already carries the rule from v3.19.6 in as many words:

> **Building the general mechanism is not applying it to the general case.** A
> set is only the general form if something enumerates the population it is
> drawn from; otherwise it is an `if` with better manners.

So this release does the enumeration rather than the second instance:
`src/test_singleton_rows.py` walks `apps.get_models()`, finds every model
exposing a `get_instance()`, and fails the build if any of them writes while
answering. A third singleton added in 2027 is covered without anyone
remembering this file exists.

⚠️ **AND THE SECOND HALF, WHICH v3.19.10 DID NOT HAVE.** Removing the create
means `get_instance()` can now hand back an **unsaved** instance, and an unsaved
instance does not behave like a saved one under `save(update_fields=[...])`:
Django issues an UPDATE, matches zero rows, and raises `DatabaseError("Save with
update_fields did not affect any rows.")`. That is a 500, and it landed on
`admin_v2.manage_lockdown`'s "update whitelist" and "update message" actions —
i.e. on the emergency-lockdown console, in exactly the fresh-install and
database-restore situations where the row is missing in the first place.

`save()` below absorbs that: on a singleton that has never been persisted, an
`update_fields` save becomes a full insert, because "write only these columns"
is not a meaningful instruction about a row that does not exist. The guard lives
on the mixin rather than at the two call sites deliberately — **the thing this
codebase has been left holding seven times running is a call site somebody
forgot** — so no writer, present or future, can get it wrong quietly.
"""


class SingletonRow:
    """
    Mix in **before** `models.Model`:

        class Thing(SingletonRow, models.Model):
            CACHE_KEY = 'thing_instance'

    It is a plain mixin, not an abstract model, so adding it to an existing
    model changes no fields and needs no migration.

    Set `CACHE_KEY` to cache the row (and its absence); leave it `None` to get
    the read-only behaviour with no caching, which is the right choice for a
    singleton that is not read on a hot path.
    """

    #: Singletons in this project are all `pk=1`. Named rather than inlined so
    #: the three places that need it cannot disagree.
    SINGLETON_PK = 1

    #: `None` disables caching for this model. A cached singleton MUST have an
    #: invalidation receiver — see the ones at the bottom of `security.py` and
    #: `landing.py`. The TTL is a backstop, never the correctness mechanism.
    CACHE_KEY = None
    CACHE_TTL = 300

    #: Marker meaning "the row does not exist yet". A plain `None` cannot be
    #: used: `cache.get` returns `None` for a miss, so caching `None` is
    #: indistinguishable from caching nothing and the query would run on every
    #: request anyway — which is the whole thing this avoids. (Carried from
    #: v3.19.10's `SystemLockdown.CACHE_MISSING`, which this replaces.)
    CACHE_MISSING = '__singleton_row_absent__'

    @classmethod
    def invalidate_cache(cls):
        from django.core.cache import cache

        if cls.CACHE_KEY:
            cache.delete(cls.CACHE_KEY)

    @classmethod
    def get_instance(cls):
        """
        Return the singleton. **Never writes.**

        When the row is absent this returns an unsaved `cls(pk=1)`, whose field
        defaults are exactly the answer `get_or_create` used to produce — the
        difference is that answering the question no longer changes the answer.
        `pk` is set so that a caller who goes on to save writes *the* singleton
        rather than a second row.

        The absence is cached too, and it has to be: dropping the write without
        caching the miss trades one INSERT-once for one uncached SELECT on every
        request, forever, on any install where nobody has opened the page. The
        sentinel is safe for a stated reason rather than a lucky one — the
        `post_save` receiver fires when the row is **created**, which is the
        exact moment "there is no row" stops being true.

        The cached value is a serialized copy (both LocMem and Redis pickle on
        `set`), so a caller mutating what it gets back — `landing_page` does,
        substituting photo tags into the HTML before rendering — cannot leak
        that mutation into the next request. `test_singleton_rows.py` pins it.
        """
        from django.core.cache import cache

        if cls.CACHE_KEY:
            cached = cache.get(cls.CACHE_KEY)
            if cached is not None:
                # A model instance compares unequal to a string via
                # `Model.__eq__` (returns NotImplemented → identity → False),
                # so a real cached row can never be mistaken for the sentinel.
                return cls(pk=cls.SINGLETON_PK) if cached == cls.CACHE_MISSING else cached

        instance = cls.objects.filter(pk=cls.SINGLETON_PK).first()

        if instance is None:
            if cls.CACHE_KEY:
                cache.set(cls.CACHE_KEY, cls.CACHE_MISSING, cls.CACHE_TTL)
            return cls(pk=cls.SINGLETON_PK)

        if cls.CACHE_KEY:
            cache.set(cls.CACHE_KEY, instance, cls.CACHE_TTL)
        return instance

    def save(self, *args, **kwargs):
        """
        Make `save(update_fields=[…])` safe on a singleton that does not exist.

        ⚠️ THIS IS A REAL 500 AND IT IS NOT HYPOTHETICAL — verified 08-19-26
        against `admin_v2.manage_lockdown`:

            DatabaseError: Save with update_fields did not affect any rows.

        Django takes `update_fields` as an instruction to issue an UPDATE. On an
        instance that has never been persisted the UPDATE matches nothing, and
        Django — correctly, for the case it was designed for — treats "you asked
        to update and nothing was updated" as an error rather than silently
        inserting.

        For a singleton that is the wrong reading: the caller is not trying to
        patch a row it believes exists, it is patching *the* row, which happens
        not to exist yet. So the narrowing is dropped and the save becomes an
        ordinary one, which Django resolves as UPDATE-then-INSERT (the same path
        `SystemLockdown.activate()` has always taken successfully).

        `force_insert` is deliberately NOT set: it would save one query and turn
        a concurrent creation into an `IntegrityError`, and the whole subject of
        this change is a read path that two requests can reach at once.

        Only the keyword form is handled. Positional arguments to `save()` are
        deprecated in Django 5.x and removed in 6.0, and nothing in this
        codebase uses them.
        """
        if self._state.adding and kwargs.get('update_fields'):
            kwargs['update_fields'] = None
        return super().save(*args, **kwargs)
