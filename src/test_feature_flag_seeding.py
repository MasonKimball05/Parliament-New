"""
Feature-flag seeding regression test  (v3.16.2, added 07-25-26)

WHY THIS EXISTS
---------------
Feature flags are read two ways, and the two DISAGREE about missing rows:

  * Python:   FeatureFlag.is_feature_enabled('x')   -> missing row = True
              (fails OPEN — see models_feature_flags.FeatureFlag)
  * Template: {% if feature_flags.x %}              -> missing row = falsy
              (fails CLOSED — context_processors.feature_flags builds a dict
               from FeatureFlag.objects.filter(is_enabled=True), so a name
               with no row is simply absent, and Django resolves the missing
               key to '' )

So a template-gated feature whose flag was never seeded is INVISIBLE with no
error, no log line, and no failing test. That is exactly what happened to the
calendar Subscribe button: `ical_export` was defined only in seed_admin_v2.py,
never in seed_feature_flags.py, so the button never rendered on installs
seeded by the latter — while the feed endpoints (gated in Python by
`calendar_subscriptions`, which fails open) worked fine the whole time. The
feature looked deleted; it wasn't.

This test scans templates for `feature_flags.<name>` and fails if the name has
no definition in seed_feature_flags.py.

Runs standalone (no Django required):  python src/test_feature_flag_seeding.py
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / 'templates'
SEEDER = REPO_ROOT / 'src' / 'management' / 'commands' / 'seed_feature_flags.py'

# `feature_flags.items` is dict iteration in a template, not a flag name.
NON_FLAG_ATTRS = {'items', 'keys', 'values'}

FLAG_REF_RE = re.compile(r'feature_flags\.(\w+)')
SEEDED_NAME_RE = re.compile(r"'name':\s*'(\w+)'")


def seeded_flag_names():
    return set(SEEDED_NAME_RE.findall(SEEDER.read_text()))


def template_flag_refs():
    """-> {flag_name: {template paths}}"""
    refs = {}
    for path in TEMPLATE_DIR.rglob('*.html'):
        try:
            text = path.read_text(errors='ignore')
        except OSError:
            continue
        for name in FLAG_REF_RE.findall(text):
            if name in NON_FLAG_ATTRS:
                continue
            refs.setdefault(name, set()).add(str(path.relative_to(REPO_ROOT)))
    return refs


class FeatureFlagSeedingTests(unittest.TestCase):

    def test_every_template_gated_flag_is_seeded(self):
        seeded = seeded_flag_names()
        refs = template_flag_refs()
        missing = {n: sorted(p) for n, p in refs.items() if n not in seeded}
        if missing:
            lines = [
                "Template-gated feature flag(s) missing from seed_feature_flags.py.",
                "Template flag lookups fail CLOSED, so these features are silently",
                "invisible on any install seeded by that command:",
                "",
            ]
            for name, paths in sorted(missing.items()):
                lines.append(f"  - {name}  (used in: {', '.join(paths)})")
            lines += [
                "",
                "Fix: add an entry to the feature_flags list in",
                "src/management/commands/seed_feature_flags.py (see ical_export).",
            ]
            self.fail("\n".join(lines))

    def test_seeder_is_parseable_and_non_empty(self):
        """Guard against the regex silently matching nothing after a refactor."""
        seeded = seeded_flag_names()
        self.assertGreater(
            len(seeded), 5,
            "seed_feature_flags.py yielded almost no flag names — the seeder "
            "format probably changed and SEEDED_NAME_RE needs updating, "
            "otherwise this whole test silently passes.",
        )

    def test_templates_were_actually_scanned(self):
        """Guard against the template dir moving and the scan finding nothing."""
        self.assertTrue(TEMPLATE_DIR.is_dir(), f"missing template dir: {TEMPLATE_DIR}")
        self.assertGreater(
            len(list(TEMPLATE_DIR.rglob('*.html'))), 50,
            "found suspiciously few templates — check TEMPLATE_DIR.",
        )


if __name__ == '__main__':
    refs = template_flag_refs()
    seeded = seeded_flag_names()
    print(f"seed_feature_flags.py defines {len(seeded)} flags")
    print(f"templates reference {len(refs)} flag(s): {', '.join(sorted(refs))}")
    for name in sorted(refs):
        mark = 'OK     ' if name in seeded else 'MISSING'
        print(f"  {mark} {name}")
    unittest.main(verbosity=2)
