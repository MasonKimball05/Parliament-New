"""
json.dumps that is safe to render inside a <script> block with |safe.

Python's json.dumps does not escape '<', so a value containing '</script>'
would end the script tag (the 07-09-26 roles_json finding). Escaping '<' as
\\u003c is valid JSON and neutralizes both '</script>' and '<!--' breakouts.

The shared copy (v3.38.2). quote_book.py, committee/education.py and
officer/transitions.py each carry an older private `_script_safe_json` that
does the same thing; new call sites should import this one.
"""
import json


def script_safe_json(data):
    return json.dumps(data).replace('<', '\\u003c')
