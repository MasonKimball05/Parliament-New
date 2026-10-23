"""
Every template name written as a literal in Python, or in an
`{% include %}` / `{% extends %}` tag, resolves to a real template.

WHAT THIS FOUND (10-22-26, fixed 5c0d7c7)
-----------------------------------------
`src/view/chat/create_channel.py::delete_channel` answered a GET by rendering
`chat/delete_channel.html`, which had never been written. The "Delete Channel"
control on `chat/index.html` is a plain link, so every admin click was a 500
(`TemplateDoesNotExist`). The POST branch worked, so the bug only surfaced by
hand on Parliament-Dev.

WHY THE EXISTING SWEEPS MISSED IT
---------------------------------
`test_template_compiles` walks the files that EXIST under templates/ — it can
never see a file that is missing. `test_detail_route_smoke` requested the
route, but its fixture channel is a committee channel, so the view took the
"cannot delete committee channels" redirect and never reached `render()`.
Same lesson as v3.18.0: **a branch that renders zero times fails zero times.**

THE CHEAP GUARD
---------------
The inverse of `test_template_compiles`: read every literal template name from
the source (an AST walk, no fixtures, no client) and ask Django's own loader to
find it. It cannot see a name built at runtime (an f-string or a variable), and
it does not try to — those are listed by `test_dynamic_names_are_few` so the
blind spot stays visible rather than silent.
"""

import ast
import pathlib
import re

from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SRC = REPO / 'src'

#: Callables whose first positional (or second, after `request`) argument is a
#: template name.
RENDER_FUNCS = {
    'render', 'render_to_string', 'get_template', 'select_template',
    'TemplateResponse', 'SimpleTemplateResponse', 'render_to_response',
}
TEMPLATE_KWARGS = {'template_name'}
TEMPLATE_SUFFIXES = ('.html', '.txt', '.xml', '.ics')

TAG_RE = re.compile(r"""{%\s*(?:include|extends)\s+(['"])([^'"]+)\1""")


def _python_files():
    for path in SRC.rglob('*.py'):
        parts = path.relative_to(SRC).parts
        if 'migrations' in parts or 'tests' in parts or path.name.startswith('test'):
            continue
        # Unrouted since v3.0.0; its templates are Alpha Mu-only on the
        # multi-chapter branch (Mason, 09-27-26). Dead code, not a 500.
        if parts[:2] == ('view', 'archive'):
            continue
        yield path


def _literals(node):
    """Every string a node can evaluate to, or None if any branch is dynamic."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _literals(node.body), _literals(node.orelse)
        if body is not None and orelse is not None:
            return body + orelse
    return None


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _template_args(call):
    """The argument nodes of `call` that hold a template name."""
    name = _call_name(call.func)
    nodes = []
    if name in RENDER_FUNCS:
        # render(request, 'x.html') / TemplateResponse(request, 'x.html')
        # render_to_string('x.html') / get_template('x.html')
        idx = 1 if name in {'render', 'TemplateResponse', 'SimpleTemplateResponse'} else 0
        if len(call.args) > idx:
            arg = call.args[idx]
            nodes.extend(arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg])
    for kw in call.keywords:
        if kw.arg in TEMPLATE_KWARGS:
            nodes.append(kw.value)
    return nodes


def collect_python_references():
    """(literal names → [file:line], dynamic → [file:line])."""
    literal, dynamic = {}, []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        where = lambda n: f'{path.relative_to(REPO)}:{n.lineno}'  # noqa: E731
        class_level = set()
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef):
                class_level.update(id(n) for n in cls.body)
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Call):
                candidates = _template_args(node)
            elif isinstance(node, ast.Assign) and id(node) in class_level:
                # class-based views: template_name = 'x.html'
                if any(isinstance(t, ast.Name) and t.id in TEMPLATE_KWARGS for t in node.targets):
                    candidates = [node.value]
            for arg in candidates:
                values = _literals(arg)
                if values is None:
                    dynamic.append(where(arg))
                    continue
                for value in values:
                    if value.endswith(TEMPLATE_SUFFIXES):
                        literal.setdefault(value, []).append(where(arg))
    return literal, dynamic


def collect_tag_references():
    refs = {}
    # chapter_content/<chapter>/templates/ exists on the multi-chapter branch.
    for root in [REPO / 'templates', *sorted(REPO.glob('chapter_content/*/templates'))]:
        for path in root.rglob('*.html'):
            text = path.read_text(encoding='utf-8', errors='replace')
            for match in TAG_RE.finditer(text):
                refs.setdefault(match.group(2), []).append(str(path.relative_to(REPO)))
    return refs


def _missing(refs):
    missing = {}
    for name, sites in sorted(refs.items()):
        try:
            get_template(name)
        except TemplateDoesNotExist:
            missing[name] = sites
    return missing


class ReferencedTemplatesExistTests(SimpleTestCase):

    def test_python_literals_resolve(self):
        literal, _ = collect_python_references()
        self.assertGreater(len(literal), 100, 'the walk found suspiciously few names')
        missing = _missing(literal)
        self.assertEqual(missing, {}, 'render()/get_template() name a template that does not exist')

    def test_include_and_extends_resolve(self):
        refs = collect_tag_references()
        self.assertGreater(len(refs), 20)
        missing = _missing(refs)
        self.assertEqual(missing, {}, '{% include %}/{% extends %} name a template that does not exist')

    def test_the_walk_sees_the_bug_it_was_written_for(self):
        literal, _ = collect_python_references()
        self.assertIn('chat/delete_channel.html', literal)

    def test_dynamic_names_are_few(self):
        # Names built at runtime are invisible to this guard. Keep the count
        # honest: if it grows, check the new site by hand and raise the bound
        # with a reason, or make the name a literal.
        _, dynamic = collect_python_references()
        self.assertLessEqual(len(dynamic), DYNAMIC_BOUND, '\n'.join(dynamic))


#: Measured 10-23-26: the one site is src/view/home.py (home_classic / home_modern,
#: chosen by preference via a variable; both exist). See test_dynamic_names_are_few.
DYNAMIC_BOUND = 1
