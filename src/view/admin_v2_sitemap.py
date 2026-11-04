"""
Admin v2: interactive site map (v4.4.4).

A debugging map of the website, generated from the URL conf rather than kept
by hand, with what decides who can open each page and the switches that can
change it.

THE TREE. Every route is walked from ``get_resolver()`` (the URL explorer's
walk, plus namespaces). A route's parent is the nearest routed path above it:
``/committee/<code>/minutes/`` sits under ``/committee/<code>/``. Where the
path just above has no route (``/committee/`` has none) that's flagged, and
the plural of it (``/committees/``) is tried before walking further up, which
is how each committee lands under Committees. Top-level pages hang off a
synthetic "Site" root: ``/home/`` is a page, not the parent of everything, and
Home -> Committees is a link, not a parent edge. An unrouted path that two or
more routes share (``/accounts/two-factor/``) becomes a "no page" group node,
so the gap is flagged once and its routes stay together.

Parameterised routes are expanded into real rows where it's cheap: ``<code>``
under ``/committee/`` into each committee and ``<period_id>`` under
``/slating/period/`` into recent elections (``EXPANDERS``, one bounded query
each). Other parameters stay as ``<name>``.

"Linked from" comes from the templates: the template names in each view's
source, and the ``{% url %}`` tags in them (plus their includes, two levels).
Links in base.html and its includes are reported as site navigation.

Hidden by default (a toggle shows them): JSON/API and mobile-app routes,
static and media files, honeypot traps (never given an "Open page" link: one
visit bans the IP), POST-only actions (from ``require_http_methods``, or a
last path segment such as ``delete`` / ``toggle``) and redirects. The Django
admin's ~800 generated routes are left out and counted.

The tree is cached for ``CACHE_TTL`` seconds, keyed on src/urls.py's mtime
(as the URL explorer is). Building it costs one query per expander.

PERMISSIONS. Each route's callback is unwrapped layer by layer (``__wrapped__``
or the wrapper's closure, since ``require_admin_v2_auth`` doesn't use
``functools.wraps``) and every layer is named from its code object
(``officer_required.<locals>.wrapper`` -> ``officer_required``). Known guards
come from ``GUARDS``; anything else is shown as an unknown wrapper rather than
guessed at.

"Who can open this?" evaluates the guards themselves, not a copy of their
rules: each known guard's wrapper is rebuilt with its inner view swapped for a
sentinel that raises ``_Passed``, and with ``render`` swapped for a stub, then
called with a probe request for the chosen member. Reaching the sentinel means
the guard let them through; anything it returns (a 403, a redirect to login)
is the refusal. The view itself never runs, so nothing it does (committee_home
creates a CommitteePermissions row on first visit, for one) can happen. The
pledge-page middleware is checked with ``check_web_page``, the helper the app
uses for the same rule. A view's own in-body checks can't be evaluated
safely, so they're named (from its source) and left as "not evaluated".

ADJUSTING. Only through mechanisms that already exist, each a POST that needs
``confirm=yes`` from the confirm panel:

* a page's PageToggle (``apply_page_toggle``, which writes the ``page_toggle``
  ActivityLog row) and its feature flags (``apply_feature_flag_toggle``,
  ``feature_flag_toggle`` row);
* its pledge page restriction (``update_page_restriction`` /
  ``delete_page_restriction``, the education dashboard's helpers; they log
  nothing themselves, so a ``settings_changed`` row is written here, as the
  System tools page does).

Committee permissions link to the committee's own editors. Everything else is
shown as "set in code" with its file and decorator.
"""
import inspect
import os
import re
import types
from functools import lru_cache
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.test import RequestFactory
from django.urls import URLResolver, get_resolver, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from src.models import ActivityLog, Committee, CommitteePermissions, ParliamentUser, SlatingPeriod
from src.models_feature_flags import FeatureFlag, PageToggle
from src.view.admin_v2 import apply_feature_flag_toggle, apply_page_toggle, require_admin_v2_auth

CACHE_TTL = 300
MEMBER_RESULTS = 8

# ── Route walk ───────────────────────────────────────────────────────────────

KIND_PAGE, KIND_API, KIND_MOBILE, KIND_STATIC, KIND_ACTION, KIND_REDIRECT, KIND_TRAP, KIND_GROUP = (
    'page', 'api', 'mobile', 'static', 'action', 'redirect', 'trap', 'group')
KIND_LABELS = {
    KIND_PAGE: 'Page', KIND_API: 'API / JSON', KIND_MOBILE: 'Mobile app API', KIND_STATIC: 'Static / media file',
    KIND_ACTION: 'Action (POST)', KIND_REDIRECT: 'Redirect', KIND_TRAP: 'Honeypot trap',
    KIND_GROUP: 'No page (a path two or more routes share)',
}
ACTION_SEGMENTS = {
    'delete', 'remove', 'toggle', 'save', 'publish', 'unpublish', 'approve', 'reject', 'revoke', 'reset',
    'recalculate', 'close', 'withdraw', 'complete', 'update', 'send', 'end', 'reopen', 'restore', 'archive',
    'unarchive', 'mark-read', 'dismiss', 'resolve', 'bulk-update', 'save-attendance', 'toggle-publish',
    'push-to-chapter', 'unpush-from-chapter', 'add-member', 'remove-member', 'unlock', 'lock', 'logout',
}
STATIC_PREFIXES = ('/static/', '/media/', '/.well-known/')
FILE_SUFFIX = re.compile(r'\.(js|json|txt|xml|webmanifest|ico|png|svg|css|map)/?$')
_PARAM = re.compile(r'<(?:[^>:]+:)?([^>]+)>')
_REGEX_GROUP = re.compile(r'\(\?P<(\w+)>[^)]*\)')


def _route_path(prefix_text):
    """'committee/<str:code>/' -> '/committee/<code>/'; regex routes are tidied the same way."""
    text = prefix_text.replace('^', '').replace('$', '').replace('\\', '')
    text = _REGEX_GROUP.sub(lambda m: f'<{m.group(1)}>', text)
    text = text.replace('/?', '/')
    text = _PARAM.sub(lambda m: f'<{m.group(1)}>', text)
    return '/' + text.lstrip('/')


def _walk(patterns, prefix='', ns=None):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern), p.namespace or ns)
        else:
            yield prefix + str(p.pattern), p.name, p.callback, ns


def _rel(path):
    base = str(settings.BASE_DIR)
    if path and path.startswith(base):
        return os.path.relpath(path, base)
    if path and 'site-packages/' in path:
        return path.split('site-packages/', 1)[1]
    return path or '?'


def _free_vars(fn):
    code = getattr(fn, '__code__', None)
    out = {}
    if code is None:
        return out
    for name, cell in zip(code.co_freevars, fn.__closure__ or ()):
        try:
            out[name] = cell.cell_contents
        except ValueError:
            pass
    return out


_INNER_NAMES = ('view_func', 'func', 'view', 'f', 'function', 'viewfunc', 'fn', 'wrapped')


def unwrap(callback):
    """
    ``(layers, view)``: the decorator layers outermost first, each
    ``{'fn', 'inner', 'name', 'qualname', 'file', 'line', 'free'}``, and the
    innermost function (the view itself, or the ``as_view()`` function of a
    class-based view).
    """
    layers = []
    fn = callback
    for _ in range(20):
        code = getattr(fn, '__code__', None)
        if code is None:
            break
        free = _free_vars(fn)
        if 'view_class' in getattr(fn, '__dict__', {}) and code.co_qualname.endswith('as_view.<locals>.view'):
            break
        inner = getattr(fn, '__wrapped__', None)
        if inner is None:
            cands = [free[k] for k in _INNER_NAMES if k in free and callable(free[k]) and hasattr(free[k], '__code__')]
            inner = cands[0] if cands else None
        if inner is None:
            break
        layers.append({'fn': fn, 'inner': inner, 'name': code.co_qualname.split('.<locals>')[0],
                       'qualname': code.co_qualname, 'file': _rel(code.co_filename),
                       'line': code.co_firstlineno, 'free': free})
        fn = inner
    return layers, fn


# ── Guard catalogue ──────────────────────────────────────────────────────────
#
# name -> (label, rule, where it's set). "Where" is 'code', 'env', 'data' (a
# row an admin can change) or 'member' (a field on the member). Only these are
# ever executed for the "who can open" check; see the module docstring.

GUARDS = {
    'login_required': ('Signed in', 'Any signed-in member.', 'code'),
    'user_passes_test': ('Custom test', 'A test function on the member.', 'code'),
    'require_admin_v2_auth': ('Admin v2 allowlist', 'User id in ADMIN_V2_USER_IDS, plus the separate admin-v2 sign-in (7 days).', 'env'),
    'officer_required': ('Officers and chairs', 'Officer or Chair member type, or a site admin (user_is_officer_or_chair).', 'member'),
    'officer_or_advisor_required': ('Officers, chairs and advisors', 'can_view_officer_pages.', 'member'),
    'admin_required': ('Site admins', 'is_admin on the member.', 'member'),
    'exclude_pledges': ('No pledges', 'Anyone but pledges.', 'code'),
    'kai_chair_required': ('Kai chairs', 'A chair of the Kai committee, or a site admin.', 'member'),
    'committee_chair_required': ('Committee chairs', "A chair of this committee (exec board members count), or a site admin.", 'member'),
    'pledge_page_allowed': ('Pledge page restriction', 'Pledges only in the phases the restriction allows (PledgePageRestriction).', 'data'),
    'cnb_required': ('C&B Chair', 'has_cnb_permission (the CNB role) or a site admin.', 'member'),
    'bug_admin_required': ('Bug-tracker admin', 'Hardcoded user_id 73 (intentional).', 'code'),
    'feedback_admin_required': ('Feedback admin', 'Hardcoded user_id 73 (intentional).', 'code'),
    'vpp_required': ('VP of Programming', 'The VPP role or a site admin.', 'member'),
    'require_page_enabled': ('Page switch', 'The PageToggle for this page must be on (no row means on).', 'data'),
    'require_feature_flag': ('Feature flag', 'Every named FeatureFlag must be on.', 'data'),
    'slating_admin_required': ('Site admins (slating)', 'is_admin.', 'member'),
    'slating_chair_required': ('Election managers', "Slating manager, committee admin or chair; site admins only while the period isn't locked.", 'data'),
    'slating_committee_required': ('Slating committee', "Committee members, managers and admins; site admins only while the period isn't locked.", 'data'),
    'voting_member_required': ('Voting members', 'can_vote on the member.', 'member'),
}

#: Wrappers that don't decide access (shown, not evaluated).
INFO_WRAPPERS = {
    'require_http_methods': 'Allowed HTTP methods',
    'csrf_exempt': 'CSRF exempt',
    'never_cache': 'Never cached',
    'cache_control': 'Cache-Control header',
    'sensitive_post_parameters': 'Hides POST fields from error reports',
    'no_append_slash': 'No APPEND_SLASH redirect',
    'log_function_call': 'Logs the call',
    'make_middleware_decorator': 'Per-view middleware (csrf_protect, gzip or similar)',
    'xframe_options_exempt': 'X-Frame-Options exempt',
    'xframe_options_sameorigin': 'X-Frame-Options SAMEORIGIN',
    'ensure_csrf_cookie': 'Sets the CSRF cookie',
}

#: Middleware that can refuse any page, per request, and isn't evaluated here.
SITE_WIDE = (
    ('Enforce2FAMiddleware', 'Two-factor policy: a member who must use 2FA is sent to set it up.'),
    ('EmergencyLockdownMiddleware', 'Emergency lockdown blocks everyone but admins while it is on.'),
    ('ForcePasswordChangeMiddleware', 'A member whose password an admin reset must change it first.'),
    ('QuarantineEnforcementMiddleware', 'Quarantined members are signed out on every request.'),
    ('MaintenanceModeMiddleware', 'Maintenance mode blocks non-admins while it is on.'),
    ('GeoRestrictionMiddleware', 'Export endpoints are blocked outside the US.'),
)

BODY_MARKERS = ('HttpResponseForbidden', 'PermissionDenied', 'has_access')


def _describe_layer(layer):
    """JSON-safe description of one layer."""
    name, free = layer['name'], layer['free']
    out = {'name': name, 'source': f"{layer['file']}:{layer['line']}", 'defined': f"{layer['file']}:{layer['line']}",
           'args': ''}
    if name == 'user_passes_test':
        test = free.get('test_func')
        tq = getattr(test, '__qualname__', '') or ''
        if tq.startswith('login_required'):
            out['name'] = name = 'login_required'
        elif tq:
            out['args'] = tq.replace('.<locals>.', '.')
    if name == 'require_page_enabled':
        out['args'] = free.get('url_name', '')
    elif name == 'require_feature_flag':
        out['args'] = ', '.join(free.get('feature_names') or ())
    elif name == 'pledge_page_allowed':
        out['args'] = free.get('url_name', '')
    elif name == 'require_http_methods':
        out['args'] = ', '.join(free.get('request_method_list') or ())
    if name in GUARDS:
        label, rule, where = GUARDS[name]
        out.update({'type': 'guard', 'label': label, 'rule': rule, 'where': where})
    elif name in INFO_WRAPPERS:
        out.update({'type': 'info', 'label': INFO_WRAPPERS[name], 'rule': '', 'where': 'code'})
    else:
        out.update({'type': 'unknown', 'label': f'Unknown wrapper {name}', 'rule': 'Not recognised, so not evaluated.',
                    'where': 'code'})
    return out


def _view_info(view):
    """(dotted name, 'file:line', class-based view class name or '')."""
    vc = getattr(view, 'view_class', None)
    if vc is not None:
        return f'{vc.__module__}.{vc.__qualname__}', _rel(inspect.getsourcefile(vc) or ''), vc.__name__
    code = getattr(view, '__code__', None)
    mod = getattr(view, '__module__', '') or ''
    qn = getattr(view, '__qualname__', getattr(view, '__name__', '?'))
    where = f'{_rel(code.co_filename)}:{code.co_firstlineno}' if code else '?'
    return f'{mod}.{qn}', where, ''


def _cbv_layer(view):
    """A class-based view's access mixins / DRF permission classes, as an unknown (not evaluated) layer."""
    vc = getattr(view, 'view_class', None)
    if vc is None:
        return None
    mro = [c.__name__ for c in vc.__mro__]
    access = [n for n in mro if n == 'AccessMixin' or (n.endswith('Mixin') and any(
        w in n for w in ('Login', 'Permission', 'PassesTest')))]
    perms = [getattr(p, '__name__', str(p)) for p in (getattr(vc, 'permission_classes', None) or [])]
    if not access and not perms and 'dispatch' not in vc.__dict__:
        return None
    found = ', '.join(access + perms) or 'its own dispatch()'
    return {'name': vc.__name__, 'source': _rel(inspect.getsourcefile(vc) or ''), 'args': found, 'type': 'unknown',
            'label': f'Class-based view {vc.__name__}', 'where': 'code',
            'rule': f'Access is decided by {found}. Not evaluated.'}


def _view_source(view):
    target = getattr(view, 'view_class', None) or view
    try:
        return inspect.getsource(target)
    except (OSError, TypeError):
        return ''


def _classify(path, name, ns, described, view):
    """(kind, why)."""
    vc = getattr(view, 'view_class', None)
    mro = [c.__name__ for c in vc.__mro__] if vc else []
    if (name or '').startswith('honeypot_'):
        return KIND_TRAP, 'Honeypot: bans whoever opens it'
    # GET routes that change something just by being opened, so they're
    # actions, not pages: email open-tracking pixels record an open, and the
    # staff login-as pair switches who you're signed in as.
    if (name or '').startswith('track_'):
        return KIND_ACTION, 'Email open-tracking pixel: opening it records an open'
    if name in ('login-as', 'return_to_original_user'):
        return KIND_ACTION, "Switches who you're signed in as"
    if (name or '').startswith('site_monitor_'):
        return KIND_API, 'JSON endpoint (site monitor)'
    if ns == 'mobile' or path.startswith('/api/mobile/') or (name or '').startswith('mobile_'):
        return KIND_MOBILE, 'Mobile app API'
    if path.startswith(STATIC_PREFIXES) or FILE_SUFFIX.search(path) or (name or '').startswith('serve_'):
        return KIND_STATIC, 'Static or media file'
    if path.startswith('/api/') or 'APIView' in mro or any(d['name'] == 'ViewSetMixin.as_view' for d in described):
        return KIND_API, 'API route'
    tokens = set((name or '').split('_'))
    if tokens & {'api', 'json', 'ajax'} or {'api', 'json'} & set(_segments(path)):
        return KIND_API, 'JSON endpoint (by name or path)'
    if 'RedirectView' in mro:
        return KIND_REDIRECT, 'RedirectView'
    for d in described:
        if d['name'] == 'require_http_methods':
            methods = {m.strip().upper() for m in d['args'].split(',') if m.strip()}
            if methods and not methods & {'GET', 'HEAD'}:
                return KIND_ACTION, f'{"/".join(sorted(methods))} only (require_http_methods)'
    last = [s for s in path.strip('/').split('/') if s and not s.startswith('<')]
    if last and last[-1] in ACTION_SEGMENTS and path.rstrip('/').endswith(last[-1]):
        return KIND_ACTION, f'Looks like an action ("{last[-1]}")'
    return KIND_PAGE, ''


def _prettify(name):
    if not name:
        return 'Unnamed route'
    text = name.split(':')[-1].replace('admin_v2_', 'admin ').replace('_', ' ').replace('-', ' ').strip()
    return text[:1].upper() + text[1:]


# ── Templates: titles and links ──────────────────────────────────────────────

_TPL_NAME = re.compile(r"""['"]([\w./-]+\.html)['"]""")
_URL_TAG = re.compile(r"""{%\s*url\s+['"]([\w:-]+)['"]""")
_INCLUDE = re.compile(r"""{%\s*include\s+['"]([^'"]+)['"]""")
_TITLE = re.compile(r'{%\s*block\s+title\s*%}(.*?){%\s*endblock', re.S)


@lru_cache(maxsize=1)
def _template_dirs():
    from django.template.utils import get_app_template_dirs
    dirs = []
    for t in settings.TEMPLATES:
        dirs.extend(str(d) for d in t.get('DIRS', []))
    dirs.extend(str(d) for d in get_app_template_dirs('templates'))
    return dirs


def _read_template(name, memo):
    if name in memo:
        return memo[name]
    text = ''
    for d in _template_dirs():
        path = os.path.join(d, name)
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as fh:
                    text = fh.read()
            except OSError:
                pass
            break
    memo[name] = text
    return text


def _template_links(name, memo, depth=0):
    text = _read_template(name, memo)
    names = set(_URL_TAG.findall(text))
    if depth < 2:
        for inc in _INCLUDE.findall(text):
            names |= _template_links(inc, memo, depth + 1)
    return names


def _clean_title(raw):
    text = re.sub(r'{{.*?}}|{%.*?%}', ' ', raw or '')
    text = re.sub(r'\b(Parliament|Admin)\b', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -|·:—')
    return text if len(re.sub(r'\W', '', text)) >= 3 else ''


# ── Building the tree ────────────────────────────────────────────────────────

# Committee pages that only exist for one kind of committee: the view looks
# the committee up with that flag (get_object_or_404(..., is_education_committee=True)
# and so on), so every other committee gets a 404. They are only expanded under
# the committees that have the flag. (route prefix under /committee/<code>/, flag)
COMMITTEE_FEATURE_PAGES = (
    ('education/', 'is_education_committee'),
    ('education-permissions/', 'is_education_committee'),
    ('recruitment/', 'is_recruitment_committee'),
    ('recruitment-permissions/', 'is_recruitment_committee'),
    ('candidates/', 'is_recruitment_committee'),
    ('kai-permissions/', 'is_kai_committee'),
)
_COMMITTEE_FLAGS = sorted({f for _, f in COMMITTEE_FEATURE_PAGES})


def _committee_rows():
    rows = Committee.objects.order_by('name').values_list('code', 'name', *_COMMITTEE_FLAGS)[:60]
    return [(code, name, {f for f, on in zip(_COMMITTEE_FLAGS, flags) if on}) for code, name, *flags in rows]


def _feature_flag_for(path, prefix):
    """The committee-type flag a page under ``prefix`` needs, or None."""
    rest = path[len(prefix):]
    return next((f for sub, f in COMMITTEE_FEATURE_PAGES if rest.startswith(sub)), None)


EXPANDERS = (
    # (route prefix, parameter, rows, what a row is called). A row is
    # (value, label) or (value, label, flags the row has); see COMMITTEE_FEATURE_PAGES.
    ('/committee/<code>/', 'code', _committee_rows, 'Committee'),
    ('/slating/period/<period_id>/', 'period_id',
     lambda: [(str(pk), name) for pk, name in
              SlatingPeriod.objects.exclude(status='archived').order_by('-pk').values_list('pk', 'name')[:15]],
     'Election'),
)


def _cache_key():
    try:
        mtime = int(os.path.getmtime(os.path.join(settings.BASE_DIR, 'src', 'urls.py')))
    except OSError:
        mtime = 0
    return f'admin_v2_sitemap_tree_v1_{mtime}'


def _route_key(path, name):
    return f'{path}|{name or ""}'


@lru_cache(maxsize=1)
def _callbacks():
    """route key -> callback, for the routes in the tree (per process; the URL conf doesn't change at runtime)."""
    return {_route_key(_route_path(r), n): cb for r, n, cb, _ns in _walk(get_resolver().url_patterns)}


def _routes():
    """The route table (no database): one dict per route, plus the site-nav link names."""
    routes, django_admin = [], 0
    memo = {}
    nav = set()
    for base in ('base.html',):
        nav |= _template_links(base, memo)
    for raw, name, cb, ns in _walk(get_resolver().url_patterns):
        layers, view = unwrap(cb)
        if any(layer['name'] in ('ModelAdmin.get_urls', 'AdminSite.get_urls') for layer in layers):
            django_admin += 1
            continue
        path = _route_path(raw)
        described = [_describe_layer(layer) for layer in layers]
        kind, why = _classify(path, name, ns, described, view)
        dotted, where, cbv = _view_info(view)
        title, links = '', set()
        if kind in (KIND_PAGE, KIND_REDIRECT, KIND_ACTION):
            src = _view_source(view)
            for tpl in dict.fromkeys(_TPL_NAME.findall(src)):
                links |= _template_links(tpl, memo)
                if not title:
                    title = _clean_title((_TITLE.search(_read_template(tpl, memo)) or [None, ''])[1])
        full_name = f'{ns}:{name}' if ns and name else name
        routes.append({
            'key': _route_key(path, name), 'path': path, 'name': full_name or '', 'kind': kind, 'why': why,
            'label': title or _prettify(name), 'view': dotted, 'where': where, 'cbv': cbv,
            'params': re.findall(r'<(\w+)>', path),
            'links': sorted(n for n in links if n != name),
        })
    return routes, sorted(nav), django_admin


def _segments(path):
    return [s for s in path.strip('/').split('/') if s]


def build_tree():
    """
    ``{'routes': [...], 'nodes': [...], 'nav': [...], 'django_admin': n, 'kinds': [...]}``.

    A route is ``{'key', 'path', 'name', 'kind', 'why', 'label', 'view',
    'links'}`` (``links``: url names its templates link to). A node is
    ``{'id', 'p' (parent id, '' for the top level), 'r' (index into routes),
    'path', 'l' (label when it differs from the route's: an expanded
    committee or election), 'm' (the unrouted parent path, or ''),
    'x' (expanded parameters, {name: value})}``. Compact because the page
    embeds it whole. Cached for CACHE_TTL.
    """
    key = _cache_key()
    data = cache.get(key)
    if data:
        return data
    routes, nav, django_admin = _routes()

    live = []
    for prefix, param, rows_fn, what in EXPANDERS:
        rows = rows_fn()
        if rows:
            live.append((prefix, param, rows))
    nodes = []
    for ri, r in enumerate(routes):
        hit = next((e for e in live if r['path'].startswith(e[0])), None)
        if hit is None:
            nodes.append({'path': r['path'], 'r': ri, 'x': {}, 'l': ''})
            continue
        prefix, param, rows = hit
        needs = _feature_flag_for(r['path'], prefix) if param == 'code' else None
        for value, label, *extra in rows:
            if needs and needs not in (extra[0] if extra else set()):
                continue
            nodes.append({'path': r['path'].replace(f'<{param}>', str(value), 1), 'r': ri, 'x': {param: str(value)},
                          'l': label.strip() if r['path'] == prefix else ''})

    by_path, used = {}, set()
    for n in nodes:
        name = routes[n['r']]['name']
        nid = n['path'] if n['path'] not in used else f"{n['path']}|{name}"
        i = 2
        while nid in used:
            nid, i = f"{n['path']}|{name}|{i}", i + 1
        used.add(nid)
        by_path.setdefault(n['path'], nid)
        n['id'] = nid

    # Unrouted folders: a path with no route of its own that two or more
    # routes sit directly under becomes a "no page" group node, so
    # /accounts/two-factor/* hang together and the gap is flagged once, on
    # the group. (A path whose plural is routed is left alone: /committee/
    # -> /committees/.)
    group_ri = len(routes)
    routes.append({'key': '', 'path': '', 'name': '', 'kind': KIND_GROUP, 'why': 'Nothing is routed at this path',
                   'label': 'No page', 'view': '', 'links': []})
    groups = set()
    while True:
        under = {}
        for n in nodes:
            segs = _segments(n['path'])
            if len(segs) < 2:
                continue
            pp = '/' + '/'.join(segs[:-1]) + '/'
            plural = '/' + '/'.join(segs[:-2] + [segs[-2] + 's']) + '/'
            if pp not in by_path and plural not in by_path:
                under[pp] = under.get(pp, 0) + 1
        new = sorted(pp for pp, c in under.items() if c >= 2 and pp not in used)
        if not new:
            break
        for pp in new:
            nodes.append({'path': pp, 'r': group_ri, 'x': {}, 'l': _segments(pp)[-1] + '/', 'id': pp})
            by_path[pp] = pp
            used.add(pp)
            groups.add(pp)
    for n in nodes:
        segs = _segments(n['path'])
        parent, missing = '', ''
        for i in range(len(segs) - 1, 0, -1):
            cand = '/' + '/'.join(segs[:i]) + '/'
            if cand in by_path:
                parent = by_path[cand]
                break
            missing = missing or cand
            plural = '/' + '/'.join(segs[:i - 1] + [segs[i - 1] + 's']) + '/'
            if plural in by_path and plural != n['path']:
                parent = by_path[plural]
                break
        n['p'] = '' if parent == n['id'] else parent
        n['m'] = n['path'] if n['id'] in groups else missing

    counts = {}
    for n in nodes:
        k = routes[n['r']]['kind']
        counts[k] = counts.get(k, 0) + 1
    data = {
        'routes': [{k: r[k] for k in ('key', 'path', 'name', 'kind', 'why', 'label', 'view', 'links')} for r in routes],
        'nodes': [{k: n[k] for k in ('id', 'p', 'r', 'path', 'l', 'm', 'x')} for n in nodes],
        'nav': nav, 'django_admin': django_admin,
        'kinds': [{'key': k, 'label': KIND_LABELS[k], 'n': counts.get(k, 0)} for k in KIND_LABELS],
    }
    cache.set(key, data, CACHE_TTL)
    return data


def client_payload(data):
    """
    What the page embeds: routes as ``{'n' name, 'k' kind, 'l' label, 'w' why}``
    and nodes as ``{'i' id (or 's', the id past the parent's id), 'p' parent
    index (-1: top level; parents come first), 'r' route index, 'l' label
    override, 'm' unrouted parent path}``, empty keys left out. Details (view, guards, links) come from the node endpoint.
    """
    index = {n['id']: i for i, n in enumerate(data['nodes'])}
    routes = []
    for r in data['routes']:
        row = {'n': r['name'], 'k': r['kind'], 'l': r['label']}
        if r['why']:
            row['w'] = r['why']
        routes.append(row)
    nodes = []
    for n in data['nodes']:
        row = {'p': index.get(n['p'], -1), 'r': n['r']}
        # 's': the id past the parent's (the common case, and most of the bytes saved); 'i': the whole id.
        if n['p'] and n['id'].startswith(n['p']):
            row['s'] = n['id'][len(n['p']):]
        else:
            row['i'] = n['id']
        if n['l']:
            row['l'] = n['l']
        if n['m']:
            row['m'] = n['m']
        nodes.append(row)
    return {'routes': routes, 'nodes': nodes, 'kinds': data['kinds'], 'django_admin': data['django_admin']}


def _related(data, node, names):
    """Node ids for url names, preferring a node with the same expanded parameters (the same committee)."""
    out = []
    for name in names:
        cands = [n for n in data['nodes'] if data['routes'][n['r']]['name'] == name]
        if not cands:
            continue
        same = [n for n in cands if n['x'] and n['x'] == node['params']]
        pick = (same or cands)[0]
        out.append({'id': pick['id'], 'name': name, 'label': pick['l'] or data['routes'][pick['r']]['label']})
    return out


def link_info(node):
    """``{'links_to', 'linked_from', 'in_nav'}`` from the template scan."""
    data = build_tree()
    name = node['name']
    sources = sorted({r['name'] for r in data['routes'] if name and name in r['links'] and r['name']})
    return {'links_to': _related(data, node, node['links']), 'linked_from': _related(data, node, sources),
            'in_nav': name in data['nav']}


def find_node(node_id):
    """One node with its route's fields merged in (``name``, ``kind``, ``label`` ...), or None."""
    data = build_tree()
    n = next((n for n in data['nodes'] if n['id'] == node_id), None)
    if n is None:
        return None
    r = data['routes'][n['r']]
    return {**r, 'id': n['id'], 'path': n['path'], 'label': n['l'] or r['label'], 'parent': n['p'],
            'missing': n['m'], 'params': n['x'], 'k': r['key']}


def _layers_for(node):
    cb = _callbacks().get(node['k'])
    if cb is None:
        return [], None
    return unwrap(cb)


# ── Details ──────────────────────────────────────────────────────────────────

def _open_url(node):
    # Never a honeypot (opening one bans your IP), an action or an API route.
    if '<' in node['path'] or node['kind'] not in (KIND_PAGE, KIND_REDIRECT, KIND_STATIC):
        return ''
    return node['path']


_IF = re.compile(r'^\s*if (.+):\s*$')
_REFUSAL = re.compile(r'return (redirect\(|HttpResponseForbidden|JsonResponse\(.*status=40[13]|render\(.*status=403)'
                      r'|raise PermissionDenied|return denied')


def _body_checks(view):
    """
    The view's own early refusals, read from its source: each ``if ...:``
    whose next few lines return a redirect / 403 (or raise PermissionDenied),
    as ``"if not x(...) -> redirect('y')"``. Falls back to naming access-ish
    words (``BODY_MARKERS``) when there's no such shape. Reported, never run.
    """
    src = _view_source(view)
    if not src or getattr(view, 'view_class', None) is not None:
        return []
    start = src.find('def ')
    body = src[src.find('\n', start) + 1:] if start >= 0 else src  # past the decorators and the def line
    lines = body.splitlines()
    found = []
    for i, line in enumerate(lines):
        m = _IF.match(line)
        if not m:
            continue
        for nxt in lines[i + 1:i + 4]:
            r = _REFUSAL.search(nxt)
            if r:
                found.append(f'if {m.group(1).strip()[:90]} -> {nxt.strip().removeprefix("return ")[:60]}')
                break
        if len(found) >= 4:
            break
    if found:
        return found
    return [m for m in BODY_MARKERS if m in body]


def _url_or_none(name, *args):
    try:
        return reverse(name, args=args)
    except Exception:
        return None


def _q_any(fields):
    from django.db.models import Q
    q = Q()
    for f in fields:
        q |= Q(**{f: True})
    return q


def _committee_info(code):
    c = (Committee.objects.filter(code=code).select_related('admin', 'role')
         .prefetch_related('chairs').first())
    if c is None:
        return None
    grants = []
    for p in (CommitteePermissions.objects.filter(committee=c)
              .filter(_q_any(('can_manage_members', 'can_take_minutes', 'can_upload_docs')))
              .select_related('user').only('user__name', 'user__preferred_name', 'user__user_id',
                                           'can_manage_members', 'can_take_minutes', 'can_upload_docs')):
        held = [label for f, label in (('can_take_minutes', 'take minutes'), ('can_upload_docs', 'manage documents'),
                                       ('can_manage_members', 'manage members')) if getattr(p, f)]
        grants.append({'name': p.user.get_display_name(), 'held': ', '.join(held),
                       'url': reverse('admin_v2_member', args=[p.user.user_id])})
    editors = [(label, _url_or_none(name, c.code)) for label, name, show in (
        ('Member permissions (minutes, documents, members)', 'committee_member_permissions', True),
        ('Manage members, chairs and advisors', 'manage_members', True),
        ('Chat permissions', 'manage_chat_permissions', True),
        ('Kai permissions', 'manage_kai_permissions', c.is_kai_committee),
        ('Education permissions', 'manage_education_permissions', c.is_education_committee),
        ('Recruitment permissions', 'manage_recruitment_permissions', c.is_recruitment_committee),
    ) if show]
    editors += [('All committees (admin v2)', _url_or_none('admin_v2_manage_committees')),
                ('Permissions map: committee chairs', (_url_or_none('admin_v2_permissions') or '') + '#perm-chairs')]
    return {
        'name': c.name.strip(), 'code': c.code,
        'state': 'archived' if c.is_archived else ('active' if c.is_active else 'inactive'),
        'exec_board': c.is_exec_board,
        'chairs': [{'name': u.get_display_name(), 'url': reverse('admin_v2_member', args=[u.user_id])}
                   for u in c.chairs.all()],
        'admin': c.admin.get_display_name() if c.admin_id else '',
        'vp_role': c.role.name if c.role_id else '',
        'members': c.members.count(), 'advisors': c.advisors.count(), 'voting': c.voting_members.count(),
        'grants': grants,
        'editors': [{'label': label, 'url': url} for label, url in editors if url],
    }


def node_details(node):
    from src.middleware.pledge_pages import is_always_allowed, restrictable_url_names
    from src.models.education import PledgePageRestriction

    if node['kind'] == KIND_GROUP:
        return {'id': node['id'], 'path': node['path'], 'name': '', 'label': node['label'], 'kind': KIND_GROUP,
                'kind_label': KIND_LABELS[KIND_GROUP], 'why': node['why'], 'missing': node['path'], 'open_url': '',
                'view': '', 'where': '', 'cbv': '', 'layers': [], 'body_checks': [], 'page_switches': [],
                'feature_flags': [], 'pledge': None, 'committee': None, 'site_wide': [],
                'links_to': [], 'linked_from': [], 'in_nav': False}
    layers, view = _layers_for(node)
    described = [_describe_layer(layer) for layer in layers]
    cbv_layer = _cbv_layer(view)
    if cbv_layer:
        described.append(cbv_layer)
    dotted, where, cbv = _view_info(view) if view is not None else ('?', '?', '')
    for d in described:
        d['source'] = where  # where it's applied: the view's own file (``defined`` is the decorator's)
    url_name = node['name']

    toggle_names = [d['args'] for d in described if d['name'] == 'require_page_enabled' and d['args']]
    flag_names = [n.strip() for d in described if d['name'] == 'require_feature_flag' for n in d['args'].split(',')
                  if n.strip()]
    toggles = {t.url_name: t for t in PageToggle.objects.filter(url_name__in=set(toggle_names) | {url_name})}
    flags = {f.name: f for f in FeatureFlag.objects.filter(name__in=flag_names)}

    page_switches = []
    for n in dict.fromkeys(toggle_names + ([url_name] if url_name in toggles else [])):
        t = toggles.get(n)
        page_switches.append({
            'url_name': n, 'exists': t is not None, 'enabled': t.is_enabled if t else True,
            'display': t.display_name if t else n, 'enforced': n in toggle_names,
            'last': f'{t.last_toggled_by}, {t.last_toggled_at:%b %-d, %Y}' if t and t.last_toggled_at else '',
        })
    feature_flags = [{
        'name': n, 'exists': n in flags, 'enabled': flags[n].is_enabled if n in flags else FeatureFlag.resolve_many([n])[n],
        'display': flags[n].display_name if n in flags else n,
    } for n in flag_names]

    pledge = None
    if url_name and ':' not in url_name:
        row = PledgePageRestriction.objects.filter(url_name=url_name).first()
        pledge = {
            'url_name': url_name, 'always_allowed': is_always_allowed(url_name),
            'restrictable': url_name in restrictable_url_names() or row is not None,
            'row': {'phases': row.allowed_phases, 'display': row.display_name} if row else None,
            'decorator': any(d['name'] == 'pledge_page_allowed' for d in described),
            'phase_choices': PledgePageRestriction.PHASE_CHOICES,
        }

    committee = _committee_info(node['params']['code']) if node['params'].get('code') else None
    return {
        'id': node['id'], 'path': node['path'], 'name': node['name'], 'label': node['label'], 'kind': node['kind'],
        'kind_label': KIND_LABELS[node['kind']], 'why': node['why'], 'missing': node['missing'],
        'open_url': _open_url(node), 'view': dotted, 'where': where, 'cbv': cbv,
        'layers': described, 'body_checks': _body_checks(view) if view is not None else [],
        'page_switches': page_switches, 'feature_flags': feature_flags, 'pledge': pledge,
        'committee': committee,
        'site_wide': [{'name': n, 'about': a} for n, a in SITE_WIDE
                      if any(m.endswith('.' + n) for m in settings.MIDDLEWARE)],
        **link_info(node),
    }


# ── "Who can open this?" ─────────────────────────────────────────────────────

class _Passed(Exception):
    """Raised by the sentinel inner view: the guard let the request through."""


def _sentinel(*_a, **_k):
    raise _Passed


def _stub_render(_request, template_name=None, context=None, content_type=None, status=None, using=None):
    resp = HttpResponse(status=status or 200)
    resp.template_name = template_name
    return resp


class _NullMessages:
    """Stands in for the messages storage: guards call messages.error() on refusal."""
    level = 0

    def add(self, *_a, **_k):
        return None

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0


def _probe_request(real_request, member, path):
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    from django.utils import timezone

    rf = RequestFactory()
    host = real_request.get_host()
    req = rf.get(path if '<' not in path else '/', HTTP_HOST=host, secure=real_request.is_secure())
    req.user = member
    req.session = SessionStore()  # in memory; nothing here ever saves it
    # Assume the member has done the admin-v2 sign-in, so only the allowlist half is judged.
    req.session['admin_v2_authenticated'] = True
    req.session['admin_v2_auth_time'] = timezone.now().isoformat()
    req._messages = _NullMessages()
    req.csp_nonce = ''
    return req


def _run_guard(layer, request, kwargs):
    """('pass'|'deny'|'error', detail). Executes only the guard's own wrapper."""
    fn = layer['fn']
    code = fn.__code__
    cells = list(fn.__closure__ or ())
    idx = next((i for i, (name, cell) in enumerate(zip(code.co_freevars, cells))
                if _cell_is(cell, layer['inner'])), None)
    if idx is None:
        return 'error', "Couldn't isolate this guard from the view."
    cells[idx] = types.CellType(_sentinel)
    g = dict(fn.__globals__)
    g['render'] = _stub_render
    clone = types.FunctionType(code, g, code.co_name, fn.__defaults__, tuple(cells))
    clone.__kwdefaults__ = fn.__kwdefaults__
    try:
        resp = clone(request, **kwargs)
    except _Passed:
        return 'pass', ''
    except Http404:
        return 'deny', 'Not found (404): no such object for these parameters.'
    except PermissionDenied as e:
        return 'deny', f'Refused (403): {e}'
    except TypeError as e:
        return 'error', f"Needs a real value for each <parameter> to evaluate ({e})."
    except Exception as e:  # noqa: BLE001 — reported, never raised into the page
        return 'error', f'{type(e).__name__}: {e}'
    return 'deny', _describe_response(resp)


def _cell_is(cell, obj):
    try:
        return cell.cell_contents is obj
    except ValueError:
        return False


def _describe_response(resp):
    status = getattr(resp, 'status_code', '?')
    if status in (301, 302):
        return f'Redirects to {resp.get("Location", "?")}'
    tpl = getattr(resp, 'template_name', None)
    return f'Refused ({status}){f": shows {tpl}" if tpl else ""}'


def who_can_open(real_request, node, member):
    """
    ``{'verdict': 'yes'|'no'|'unknown'|'guards', 'reason', 'steps': [...], 'notes': [...], 'standing'}``.
    Never runs the view; see the module docstring.
    """
    from src import dev_mode
    from src.middleware.pledge_pages import is_always_allowed
    from src.mobile.api import check_web_page

    member_info = {'name': member.get_display_name(), 'meta': f'{member.member_type}, {member.member_status}',
                   'url': reverse('admin_v2_member', args=[member.user_id])}
    if node['kind'] == KIND_GROUP:
        return {'verdict': 'no', 'reason': 'Nothing is routed at this path: everyone gets a 404.', 'steps': [],
                'notes': [], 'standing': None, 'member': member_info}

    layers, view = _layers_for(node)
    kwargs = {k: (int(v) if v.isdigit() else v) for k, v in node['params'].items()}
    for p in re.findall(r'<(\w+)>', node['path']):
        kwargs.setdefault(p, None)
    request = _probe_request(real_request, member, node['path'])
    steps = []
    token = dev_mode._recorder.set(None)  # keep these probes out of the admin's own dev-mode panel
    try:
        for layer in layers:
            d = _describe_layer(layer)
            if d['type'] == 'info':
                continue
            if d['type'] == 'unknown':
                steps.append({'label': d['label'], 'source': d['source'], 'result': 'unknown',
                              'detail': 'Not a recognised guard, so not run.'})
                continue
            result, detail = _run_guard(layer, request, kwargs)
            steps.append({'label': d['label'] + (f' ({d["args"]})' if d['args'] else ''), 'source': d['source'],
                          'result': result, 'detail': detail, 'rule': d['rule']})
        cbv_layer = _cbv_layer(view)
        if cbv_layer:
            steps.append({'label': cbv_layer['label'], 'source': cbv_layer['source'], 'result': 'unknown',
                          'detail': cbv_layer['rule']})
        url_name = node['name']
        if url_name and ':' not in url_name and member.is_pledge:
            if is_always_allowed(url_name):
                steps.append({'label': 'Pledge page restriction (middleware)', 'result': 'pass',
                              'detail': 'Always open to pledges.', 'source': 'src/middleware/pledge_pages.py'})
            else:
                try:
                    check_web_page(member, url_name, toggle=False)
                    steps.append({'label': 'Pledge page restriction (middleware)', 'result': 'pass',
                                  'detail': f'Phase {getattr(member, "pledge_phase", None) or "all"} is allowed.',
                                  'source': 'src/middleware/pledge_pages.py'})
                except Exception as e:  # DRF PermissionDenied
                    steps.append({'label': 'Pledge page restriction (middleware)', 'result': 'deny',
                                  'detail': str(getattr(e, 'detail', e)), 'source': 'src/middleware/pledge_pages.py'})
    finally:
        dev_mode._recorder.reset(token)

    notes = []
    if not member.is_active:
        notes.append("This account is inactive, so it can't sign in at all.")
    if any(s['label'].startswith('Admin v2 allowlist') for s in steps):
        notes.append('Assumes they have done the separate admin-v2 sign-in.')
    body = _body_checks(view) if view is not None else []
    if body:
        notes.append("The view's own code also checks access (" + '; '.join(body) +
                     "). That part isn't evaluated: running the view could write data.")

    denied = next((s for s in steps if s['result'] == 'deny'), None)
    unsure = next((s for s in steps if s['result'] in ('unknown', 'error')), None)
    if denied:
        verdict, reason = 'no', f'{denied["label"]}: {denied["detail"]}'
    elif not member.is_active and steps:
        verdict, reason = 'no', "Account is inactive and can't sign in."
    elif unsure:
        verdict, reason = 'unknown', f'{unsure["label"]}: {unsure["detail"]}'
    elif body:
        # 'guards': every decorator passes, and the view's own check (named in
        # the notes) decides the rest. Not a yes: it may well refuse them.
        verdict, reason = 'guards', ("Every guard lets them through, but the view's own check decides the rest: "
                                     + '; '.join(body))
    elif not steps:
        verdict, reason = 'yes', 'No guard on this page: anyone can open it, signed in or not.'
    else:
        verdict, reason = 'yes', 'Every guard lets them through.'

    standing = None
    code = node['params'].get('code')
    if code:
        c = Committee.objects.filter(code=code).first()
        if c is not None:
            perms = CommitteePermissions.objects.filter(committee=c, user=member).first()
            standing = {
                'committee': c.name.strip(),
                'facts': [(label, bool(val)) for label, val in (
                    ('Member', c.is_member(member)), ('Chair', c.is_chair(member)),
                    ('Advisor', c.advisors.filter(pk=member.pk).exists()),
                    ('Voting member', c.voting_members.filter(pk=member.pk).exists()),
                    ('Holds the committee VP role', c.is_vp(member)),
                    ('Committee admin', c.admin_id == member.pk), ('Site admin', member.is_admin),
                    ('Take minutes', perms and perms.can_take_minutes),
                    ('Manage documents', perms and perms.can_upload_docs),
                    ('Manage members', perms and perms.can_manage_members),
                )],
            }
    return {'verdict': verdict, 'reason': reason, 'steps': steps, 'notes': notes, 'standing': standing,
            'member': member_info}


# ── Views ────────────────────────────────────────────────────────────────────

def _back(node_id):
    url = reverse('admin_v2_sitemap')
    return redirect(f'{url}?{urlencode({"node": node_id})}' if node_id else url)


def _confirm_panel(request):
    """The confirm panel a ?confirm= GET asks for, or None."""
    from src.models.education import PledgePageRestriction

    kind, target = request.GET.get('confirm'), (request.GET.get('target') or '')[:100]
    node_id = (request.GET.get('node') or '')[:300]
    if kind == 'page':
        t = PageToggle.objects.filter(url_name=target).first()
        if t:
            return {'kind': kind, 'node': node_id, 'target': target, 'display': t.display_name,
                    'enabled': t.is_enabled}
    elif kind == 'flag':
        f = FeatureFlag.objects.filter(name=target).first()
        if f:
            return {'kind': kind, 'node': node_id, 'target': target, 'display': f.display_name,
                    'enabled': f.is_enabled}
    elif kind in ('pledge_set', 'pledge_remove'):
        row = PledgePageRestriction.objects.filter(url_name=target).first()
        if kind == 'pledge_remove' and row is None:
            return None
        return {'kind': kind, 'node': node_id, 'target': target, 'phases': row.allowed_phases if row else [],
                'display': (row.display_name if row else '') or target,
                'phase_choices': PledgePageRestriction.PHASE_CHOICES}
    return None


@require_admin_v2_auth
def admin_v2_sitemap(request):
    request.session.pop(SITEMAP_RETURN_KEY, None)  # back on the map: the return bar's job is done
    data = build_tree()
    return render(request, 'admin_v2/sitemap.html', {
        'tree': client_payload(data), 'kinds': data['kinds'], 'django_admin': data['django_admin'],
        'confirm': _confirm_panel(request),
        'selected': (request.GET.get('node') or '')[:300],
        'total': len(data['nodes']),
    })


# "Open page" (v4.4.4): the page opens in this tab and every page shows a
# return bar (templates/base.html, like the login-as banner) until the admin
# goes back to the site map or closes the bar. Only the page path and node id
# are kept, in the admin's own session.
SITEMAP_RETURN_KEY = 'admin_v2_sitemap_return'


@require_admin_v2_auth
def admin_v2_sitemap_open(request):
    node_id = (request.GET.get('node') or '')[:300]
    node = find_node(node_id)
    url = _open_url(node) if node else ''
    if not url:
        messages.error(request, "That page can't be opened from the site map (it needs a value filled in, "
                                "or it's an action, API route or trap).")
        return redirect(f"{reverse('admin_v2_sitemap')}?{urlencode({'node': node_id})}")
    request.session[SITEMAP_RETURN_KEY] = {'node': node['id'], 'path': url,
                                           'label': f"{node['label']} ({url})" if node['label'] else url}
    return redirect(url)


@require_admin_v2_auth
def admin_v2_sitemap_return(request):
    """The return bar's buttons: back to the node (default) or just close the bar (POST, ?close=1)."""
    ret = request.session.pop(SITEMAP_RETURN_KEY, None) or {}
    if request.method == 'POST' and request.POST.get('close'):
        return redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(
            request.POST.get('next', ''), allowed_hosts={request.get_host()}) else 'home')
    target = reverse('admin_v2_sitemap')
    if ret.get('node'):
        target += '?' + urlencode({'node': ret['node']})
    return redirect(target)


@require_admin_v2_auth
def admin_v2_sitemap_node(request):
    node = find_node(request.GET.get('id', ''))
    if node is None:
        return JsonResponse({'error': 'No such page in the map.'}, status=404)
    return JsonResponse(node_details(node))


@require_admin_v2_auth
def admin_v2_sitemap_members(request):
    from django.db.models import Q
    q = (request.GET.get('q') or '').strip()[:80]
    if len(q) < 2:
        return JsonResponse({'members': []})
    cond = Q(name__icontains=q) | Q(username__icontains=q) | Q(preferred_name__icontains=q)
    if q.isdigit():
        cond |= Q(user_id=q)
    users = (ParliamentUser.objects.filter(cond)
             .only('user_id', 'name', 'preferred_name', 'username', 'member_type', 'member_status')
             .order_by('name')[:MEMBER_RESULTS])
    return JsonResponse({'members': [{'id': u.user_id, 'name': u.get_display_name(),
                                      'meta': f'{u.member_type}, {u.member_status}'} for u in users]})


@require_admin_v2_auth
def admin_v2_sitemap_check(request):
    node = find_node(request.GET.get('id', ''))
    member = ParliamentUser.objects.filter(user_id=(request.GET.get('member') or '')[:50]).first()
    if node is None or member is None:
        return JsonResponse({'error': 'Pick a page and a member.'}, status=404)
    return JsonResponse(who_can_open(request, node, member))


def _confirmed(request):
    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'Nothing changed: confirm the change first.')
        return False
    return True


@require_admin_v2_auth
@require_POST
def admin_v2_sitemap_page_toggle(request):
    node_id = request.POST.get('node', '')[:300]
    if not _confirmed(request):
        return _back(node_id)
    toggle = PageToggle.objects.filter(url_name=request.POST.get('target', '')).first()
    if toggle is None:
        messages.error(request, 'That page switch no longer exists.')
        return _back(node_id)
    status = apply_page_toggle(request, request.user, toggle)
    messages.success(request, f'Page "{toggle.display_name}" has been {status}.')
    return _back(node_id)


@require_admin_v2_auth
@require_POST
def admin_v2_sitemap_feature_flag(request):
    node_id = request.POST.get('node', '')[:300]
    if not _confirmed(request):
        return _back(node_id)
    flag = FeatureFlag.objects.filter(name=request.POST.get('target', '')).first()
    if flag is None:
        messages.error(request, 'That feature flag no longer exists.')
        return _back(node_id)
    status = apply_feature_flag_toggle(request, request.user, flag)
    messages.success(request, f'Feature "{flag.display_name}" has been {status}.')
    return _back(node_id)


@require_admin_v2_auth
@require_POST
def admin_v2_sitemap_pledge_restriction(request):
    from src.models.education import PledgePageRestriction
    from src.view.committee.education import delete_page_restriction, update_page_restriction

    node_id = request.POST.get('node', '')[:300]
    if not _confirmed(request):
        return _back(node_id)
    url_name = request.POST.get('target', '')[:100]
    if request.POST.get('action') == 'remove':
        row = PledgePageRestriction.objects.filter(url_name=url_name).first()
        if row is None:
            messages.error(request, 'There is no restriction on that page.')
            return _back(node_id)
        before = row.allowed_phases
        delete_page_restriction(row)
        desc = f'{request.user.get_display_name()} removed the pledge restriction on {url_name} (open to every pledge)'
        meta = {'url_name': url_name, 'before': before, 'after': None, 'via': 'admin_v2_sitemap'}
        obj_id = url_name
    else:
        phases = request.POST.getlist('phases')
        row, error = update_page_restriction(url_name, request.POST.get('display_name', ''), phases, request.user)
        if error:
            messages.error(request, error)
            return _back(node_id)
        allowed = ', '.join(row.allowed_phases) or 'no phase'
        desc = f'{request.user.get_display_name()} set the pledge restriction on {url_name}: allowed in {allowed}'
        meta = {'url_name': url_name, 'after': row.allowed_phases, 'via': 'admin_v2_sitemap'}
        obj_id = row.pk
    ActivityLog.log_activity(
        action_type='settings_changed', user=request.user, description=desc,
        object_type='PledgePageRestriction', object_id=str(obj_id), object_repr=url_name,
        metadata=meta, request=request)
    messages.success(request, 'Pledge page restriction saved.')
    return _back(node_id)
