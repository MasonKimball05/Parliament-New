"""
Template tags for developer mode.

USAGE
-----
Wrap a value you want to inspect::

    {% load dev_tags %}
    {% dev_value report 'title' %}

With dev mode off this renders exactly what ``{{ report.title }}`` would, with
no wrapper element and no cost beyond one boolean check. With dev mode on it
renders the same text wrapped in a hoverable span carrying the model, PK, field
name, the raw value, and whether the value came from a prefetch or a fresh
query.

Free-standing notes, for things that aren't a model field::

    {% dev_note "turnout source" "computed in view, not annotated" %}

RECORD GATING
-------------
``dev_value`` never reveals a value the current user could not otherwise see.
Pass ``gated_by`` when a field is governed by a permission and the tag will show
the metadata but replace the value with a note naming the gate::

    {% dev_value report 'description' gated_by=kai_access.can_view_report_details %}

Knowing *that* a field is gated and *which* check gated it is the debugging
information you actually want; the allegation text is not. See the module
docstring in src/dev_mode.py for why this is a firm rule rather than a default.
"""

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from src.dev_mode import get_recorder, record_note

register = template.Library()


MAX_RAW_LEN = 200


def _describe(obj, field_name):
    """Collect what we can about obj.field_name without triggering a query."""
    info = {
        'model': obj.__class__.__name__,
        'app': getattr(getattr(obj, '_meta', None), 'app_label', ''),
        # The DB table is what links this element to the queries that produced
        # it — dwell-hover matches on it to highlight rows in the SQL and Shapes
        # tabs. Without it the panel and the page are two unrelated views.
        'table': getattr(getattr(obj, '_meta', None), 'db_table', ''),
        'pk': getattr(obj, 'pk', None),
        'field': field_name,
        'kind': '',
        'loaded': '',
        'raw': None,
    }

    meta = getattr(obj, '_meta', None)
    if meta is not None:
        try:
            field = meta.get_field(field_name)
            info['kind'] = field.get_internal_type()
        except Exception:
            info['kind'] = 'property/method'

        # Was this instance loaded with .only()/.defer()? Touching a deferred
        # field costs a query — worth surfacing, since that is a silent N+1.
        deferred = obj.get_deferred_fields() if hasattr(obj, 'get_deferred_fields') else set()
        if field_name in deferred:
            info['loaded'] = 'DEFERRED — reading this fires a query'
        else:
            info['loaded'] = 'loaded with instance'

    # Related managers: report prefetch status rather than evaluating them.
    attr = getattr(obj.__class__, field_name, None)
    if hasattr(attr, 'is_cached'):
        try:
            info['loaded'] = 'prefetched' if attr.is_cached(obj) else 'NOT prefetched — will query'
        except Exception:
            pass

    prefetched = getattr(obj, '_prefetched_objects_cache', None)
    if prefetched and field_name in prefetched:
        info['loaded'] = 'prefetched'

    return info


def _tooltip_html(info, display, gated_note=None):
    rows = [
        ('model', f"{info['app']}.{info['model']}" if info['app'] else info['model']),
        ('table', info.get('table')),
        ('pk', info['pk']),
        ('field', info['field']),
        ('type', info['kind']),
        ('loading', info['loaded']),
    ]
    if gated_note:
        rows.append(('gated by', gated_note))
    else:
        raw = info.get('raw')
        if raw is not None:
            raw = str(raw)
            if len(raw) > MAX_RAW_LEN:
                raw = raw[:MAX_RAW_LEN] + '…'
            if raw != str(display):
                rows.append(('raw', raw))

    body = ''.join(
        f'<span class="pdev-k">{escape(str(k))}</span>'
        f'<span class="pdev-v">{escape(str(v))}</span>'
        for k, v in rows if v not in (None, '')
    )
    return f'<span class="pdev-tip">{body}</span>'


@register.simple_tag
def dev_value(obj, field_name, gated_by=None, gate_name=None):
    """
    Render obj.field_name, annotated when dev mode is on.

    `gated_by` — pass the permission boolean that governs this field. Falsy
    means the value is withheld and the tag reports the gate instead.
    """
    recorder = get_recorder()

    is_gated = gated_by is not None and not gated_by

    try:
        value = getattr(obj, field_name, None)
        if callable(value):
            value = value()
    except Exception as exc:
        value = f'<error: {exc.__class__.__name__}>'

    if recorder is None:
        # Dev mode off — behave exactly like {{ obj.field }}.
        return '' if is_gated else value

    info = _describe(obj, field_name)
    info['raw'] = None if is_gated else value

    gate_note = None
    if is_gated:
        gate_note = gate_name or 'permission check (value withheld)'
        display = '[gated]'
    else:
        display = '' if value is None else value

    try:
        recorder.record_object({
            'model': info['model'],
            'pk': info['pk'],
            'field': info['field'],
            'loading': info['loaded'],
            'gated': bool(is_gated),
        })
    except Exception:
        pass

    css = 'pdev-val' + (' pdev-gated' if is_gated else '')
    # ⚠️ v3.19.10 — JUSTIFIED `nosec` (B308 + B703, "potential XSS on
    # mark_safe"). Bandit is right that this is the shape it should flag, and it
    # is safe here for a reason that has to stay true: **every value
    # interpolated below is escaped at the point of interpolation, not
    # upstream.** `css` is assembled from two literals; `info["table"]` and
    # `str(display)` are wrapped in `escape()` on their own lines; and
    # `_tooltip_html` escapes each key and value it emits (see the `body`
    # comprehension there) and interpolates nothing else.
    #
    # This is dev mode, which Mason runs against PRODUCTION data, so the values
    # passing through here are real member records — an unescaped one would be
    # stored XSS in an officer's own session.
    #
    # **If you add a field to this f-string or to `_tooltip_html`'s rows, wrap
    # it in `escape()` in the same edit, or delete this comment along with the
    # suppression below.** A bare `nosec` is used rather than the explicit
    # `B308,B703` pair because bandit 1.9.4 leaves B308 REPORTED when the ids
    # are named on a `mark_safe` line — re-verified 08-19-26 against a minimal
    # probe, and the same is now true of the four other `mark_safe` sites in
    # this project. Naming the ids here would redden CI.
    #
    # ⚠️ v3.19.11 — THE COMMENT ABOVE USED TO WRAP SO THAT A LINE BEGAN WITH
    # THE DIRECTIVE SPELLING ITSELF, AND BANDIT READ IT AS A DIRECTIVE. The
    # pattern it looks for matches inside a comment as readily as after code,
    # so the sentence written to *explain* this suppression became a second,
    # blanket suppression sitting on a comment line — silently covering
    # whatever code a later edit might move onto it, and emitting ~18 "not a
    # test name" warnings from its own prose.
    #
    # The first draft of THIS note reintroduced it, by quoting the spelling in
    # backticks. Never write the directive in prose; say "the directive" and
    # let the line below be the only one. Pinned by
    # `src/test_nosec_hygiene.py`, which fails on a comment-only line carrying
    # one.
    return mark_safe(  # nosec  # B308,B703: every interpolated value is escaped at the point of interpolation
        f'<span class="{css}" tabindex="0" data-pdev-table="{escape(info["table"])}">'
        f'{escape(str(display))}'
        f'{_tooltip_html(info, display, gate_note)}</span>'
    )


@register.simple_tag
def dev_note(label, value):
    """Record a free-standing note for the panel. Renders nothing inline."""
    record_note(label, value)
    return ''


@register.simple_tag(takes_context=True)
def dev_active(context):
    """True when dev mode is on — for templates that want to show extra blocks."""
    return get_recorder() is not None


@register.simple_tag
def dev_badge(label, value):
    """A small always-visible chip, only rendered in dev mode."""
    if get_recorder() is None:
        return ''
    return format_html(
        '<span class="pdev-badge"><b>{}</b> {}</span>', label, value
    )
