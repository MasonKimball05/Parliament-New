"""
v3.38.2 (10-01-26) — JSON rendered into a <script> with |safe escapes '<'.

The slating setup page embedded member names with a bare json.dumps, the same
shape as the 07-09-26 roles_json finding. The shared helper is
src/utils/script_json.py.

Run with: python manage.py test src.tests.security.test_script_safe_json
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

from src.utils.script_json import script_safe_json


class ScriptSafeJsonTests(SimpleTestCase):
    def test_script_breakouts_are_escaped_and_the_value_round_trips(self):
        data = {'name': '</script><script>alert(1)</script>', 'note': '<!-- x'}
        out = script_safe_json(data)
        self.assertNotIn('<', out)
        self.assertEqual(json.loads(out), data)

    def test_slating_and_kai_embeds_use_it(self):
        root = Path(__file__).resolve().parents[2] / 'view'
        self.assertIn('write_in_js_data = script_safe_json(',
                      (root / 'slating' / 'period_setup.py').read_text())
        kai = (root / 'kai_reports.py').read_text()
        self.assertIn("'category_data': script_safe_json(", kai)
        self.assertIn("'monthly_data': script_safe_json(", kai)
