"""
09-24-26 — education templates must gate controls on `access`, not `is_chair`.

⚠️ WHY. v3.32.0 made education access granular (`_get_education_access`
returns `can_view_submissions` / `can_grade_submissions` / `can_manage_tasks`)
and every POST view checks the specific key — but all 27 `{% if is_chair %}`
gates in the education templates were left as they were. `is_chair` is True
only for `committee.chairs`, so a member granted `can_manage_tasks` on
`manage_education_permissions` (and a site admin who is not a chair) reached
a read-only dashboard: the permission worked, the buttons never rendered.
Reported by chapter members 09-24-26.

Also guards the Adjust Points history panel against `innerHTML` on
chair-typed text (stored HTML injection fixed the same day).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

EDUCATION_TEMPLATES = [
    'committee/education.html',
    'committee/_education_meeting_row.html',
    'committee/education_task_form.html',
    'committee/education_quiz_questions.html',
    'committee/education_meeting_form.html',
    'committee/quiz_submissions.html',
]

IS_CHAIR_IN_TAG = re.compile(r'\{%\s*(?:if|elif)\b[^%]*\bis_chair\b[^%]*%\}')


def _read(rel):
    return (Path(settings.BASE_DIR) / 'templates' / rel).read_text()


class EducationTemplatesGateOnAccessTests(SimpleTestCase):
    def test_no_education_template_gates_a_control_on_is_chair(self):
        offenders = []
        for rel in EDUCATION_TEMPLATES:
            for n, line in enumerate(_read(rel).splitlines(), 1):
                if IS_CHAIR_IN_TAG.search(line):
                    offenders.append(f'{rel}:{n}: {line.strip()}')
        self.assertEqual(
            offenders, [],
            'Gate education controls on access.can_manage_tasks / '
            'access.can_grade_submissions, not is_chair — is_chair is False '
            'for members granted access via EducationMemberPermission:\n'
            + '\n'.join(offenders),
        )


class AdjustmentHistoryDoesNotUseInnerHtmlTests(SimpleTestCase):
    def test_render_adjustment_history_builds_rows_without_innerhtml_interpolation(self):
        src = _read('committee/education.html')
        start = src.index('function renderAdjustmentHistory')
        end = src.index('function openAdjustPointsModal', start)
        body = src[start:end]
        # `list.innerHTML = ''` (clearing) is fine; assigning a template
        # literal is the sink.
        self.assertNotRegex(body, r'innerHTML\s*=\s*`')
        self.assertNotIn('${entry.reason}', body.replace('textContent = entry.reason', ''))
