"""
v3.15.0 — canonical pledge-class registry (Mason's idea, 07-19-26).

One deterministic source of truth for the chapter's pledge classes:

- **The founding semester = Founders** (no Greek letter; keeps the gold
  badge). It comes from the chapter config — `get_chapter().founding_semester`
  / `.founding_year` (CHAPTER_FOUNDING_SEMESTER / CHAPTER_FOUNDING_YEAR env
  vars; the default is Fall 2022). Multi-chapter phase 2, 09-27-26.
- The lettered sequence starts the NEXT semester (Spring 2023 = Alpha with
  the defaults), then one class per semester: Fall 2023 = Beta, Spring 2024 =
  Gamma, ... After Omega the sequence doubles: Alpha Alpha, Alpha Beta, ...
- 09-28-26 (multi-chapter): a chapter whose real lettering doesn't start the
  semester after its founding sets `CHAPTER_LETTERING_ANCHOR`, e.g.
  'Fall 2010 = Xi'. Every class is then counted from that one, one letter per
  semester; classes between the founders and the first letter get no letter
  (''). Colors are unaffected: they follow the class INDEX, not the letter.
- Every class gets a **stable, unique, clearly-distinct badge color** from
  CLASS_PALETTE below. The palette was generated once by farthest-point
  sampling in CIELAB space (each color placed as far as possible from all
  previous ones, and from the founder gold) over a readable badge gamut, so:
    * unique — no two classes share a color;
    * noticeably different — guaranteed min pairwise ΔE ≈ 14 (well above the
      ~10 "clearly different to the eye" threshold) across all 48 entries;
    * stable — class index i always maps to CLASS_PALETTE[i]; adding a new
      class never recolors an existing one.
  48 entries = 24 years of classes. Past that it wraps (the only case where a
  color could repeat — Mason's "unless it runs out of hexes" caveat); a
  loud comment flags it, and it's decades away.

Semester boundary: months 1–6 = Spring, months 7–12 = Fall (the upcoming
fall class becomes selectable in July, when recruitment planning starts).

Used by: profile + admin member forms (datalist choices, normalization on
save) and the directory profile card (badge colors).
"""
from datetime import date

GREEK_LETTERS = [
    'Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta',
    'Iota', 'Kappa', 'Lambda', 'Mu', 'Nu', 'Xi', 'Omicron', 'Pi', 'Rho',
    'Sigma', 'Tau', 'Upsilon', 'Phi', 'Chi', 'Psi', 'Omega',
]

def _founding():
    """(semester, year) of the founding class, from the chapter config."""
    from src.chapter import get_chapter
    chapter = get_chapter()
    return chapter.founding_semester, int(chapter.founding_year)


FOUNDERS_GREEK = 'Founder'  # matches the existing directory gold-badge check

# Farthest-point palette (see module docstring). White text is readable on
# every entry in both light and dark mode. DO NOT reorder or hand-edit —
# index position is the stable identity of a class's color. Regenerating
# with the same seed reproduces this exact list (scripts/gen_class_palette.py).
CLASS_PALETTE = [
    "#2d5fa9", "#33e1be", "#d52050", "#e133e1", "#20d520", "#1c26ba",
    "#a95f2d", "#20b1d5", "#80a92d", "#a92d80", "#20d574", "#d5e133",
    "#d54420", "#882da9", "#205cd5", "#2da9a1", "#a9882d", "#8dd520",
    "#8d20d5", "#2da96f", "#d58020", "#e133b2", "#339be1", "#a9352d",
    "#35a92d", "#a92d56", "#e13384", "#b2b224", "#33e1e1", "#2d80a9",
    "#6a46ce", "#3384e1", "#d7663c", "#3cd751", "#2020d5", "#2d35a9",
    "#e1333e", "#b224a8", "#ceb346", "#ce8e46", "#73ce46", "#33e19b",
    "#ae3cd7", "#b3ce46", "#2da94e", "#466ace", "#2da988", "#2d99a9",
]

FOUNDERS_COLOR = "#ffd700"  # gold — rendered as the existing gradient badge


def color_for_index(idx):
    """Stable badge color for a 0-based class index (0 = Founders = gold)."""
    if idx == 0:
        return FOUNDERS_COLOR
    # idx-1 because Founders (0) isn't in the generated palette.
    pos = idx - 1
    # Wrap only past the palette (>48 classes ≈ 24 yrs out): the sole
    # "ran out of colors" case. Acceptable per spec; flagged here.
    return CLASS_PALETTE[pos % len(CLASS_PALETTE)]


def _greek_position(name):
    """Inverse of _greek_for_position: 'Alpha' -> 0, 'Omega' -> 23, 'Alpha Alpha' -> 24."""
    parts = [p.capitalize() for p in (name or '').split()]
    if len(parts) == 1 and parts[0] in GREEK_LETTERS:
        return GREEK_LETTERS.index(parts[0])
    if len(parts) == 2 and all(p in GREEK_LETTERS for p in parts):
        return (GREEK_LETTERS.index(parts[0]) + 1) * len(GREEK_LETTERS) + GREEK_LETTERS.index(parts[1])
    raise ValueError(f'{name!r} is not a Greek class name (one or two letters, e.g. "Xi" or "Alpha Beta")')


def parse_lettering_anchor(text):
    """'Spring 2023 = Alpha' -> ('Spring', 2023, 0). Raises ValueError if malformed.

    Multi-chapter, 09-28-26: an older chapter's real lettering may not start the
    semester after its founding. The anchor pins one class to its letter and
    every other class is counted from it, one letter per semester.
    """
    import re
    m = re.match(r'^\s*(fall|spring)\s+(\d{4})\s*=\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)\s*$', text or '', re.I)
    if not m:
        raise ValueError(f"lettering anchor must look like 'Spring 2023 = Alpha', not {text!r}")
    return m.group(1).capitalize(), int(m.group(2)), _greek_position(m.group(3))


def _semesters_between(start, end):
    """Semesters from start (season, year) to end (season, year); may be negative."""
    (s0, y0), (s1, y1) = start, end
    return (y1 - y0) * 2 + (s1 == 'Fall') - (s0 == 'Fall')


def _anchor():
    """(index of the anchored class, its lettered position). Default: index 1 = Alpha."""
    from src.chapter import get_chapter
    text = getattr(get_chapter(), 'lettering_anchor', '')
    if not text:
        return 1, 0
    season, year, pos = parse_lettering_anchor(text)
    return _semesters_between(_founding(), (season, year)), pos


def _greek_for_index(idx, anchor):
    """Greek name for class index idx (0 = founders); '' before lettering began."""
    if idx == 0:
        return FOUNDERS_GREEK
    anchor_idx, anchor_pos = anchor
    pos = anchor_pos + (idx - anchor_idx)
    return _greek_for_position(pos) if pos >= 0 else ''


def _greek_for_position(pos):
    """0-based position in the LETTERED sequence (0 = Alpha = Spring 2023)."""
    n = len(GREEK_LETTERS)
    if pos < n:
        return GREEK_LETTERS[pos]
    # Alpha Alpha, Alpha Beta, ... Beta Alpha, ... (12+ years out, but cheap)
    return f'{GREEK_LETTERS[pos // n - 1]} {GREEK_LETTERS[pos % n]}'


def _current_semester(today=None):
    today = today or date.today()
    return ('Fall' if today.month >= 7 else 'Spring'), today.year


def all_classes(today=None):
    """Every class from the founding semester through the current one, in order.

    Returns a list of dicts: {'label': 'Spring 2023', 'greek': 'Alpha',
    'index': 1, 'hue': 137, 'is_founders': False}.
    """
    season, year = _current_semester(today)
    classes = []
    s, y = _founding()
    anchor = _anchor()
    idx = 0
    while (y, s == 'Fall') <= (year, season == 'Fall'):
        if s == 'Fall' and y == year and season == 'Spring':
            break  # don't include this year's fall before July
        classes.append({
            'label': f'{s} {y}',
            'greek': _greek_for_index(idx, anchor),
            'index': idx,
            'color': color_for_index(idx),
            'is_founders': idx == 0,
        })
        # advance one semester
        if s == 'Fall':
            s, y = 'Spring', y + 1
        else:
            s = 'Fall'
        idx += 1
    return classes


def class_by_label(label, today=None):
    label = (label or '').strip().lower()
    for c in all_classes(today):
        if c['label'].lower() == label:
            return c
    return None


def normalize(text, today=None):
    """Best-effort match of free text to a canonical class, else None.

    Accepts: 'Fall 2022' (any case/extra spaces), shorthand like
    'fa22' / 'f 22' / "fall '22" / 'sp2023', a Greek class name ('beta',
    'alpha beta'), or 'founder(s)'.
    """
    import re
    raw = (text or '').strip().lower()
    if not raw:
        return None
    classes = all_classes(today)

    if raw in ('founder', 'founders', 'founding class', 'founder class'):
        return classes[0]

    # Greek name match ('beta', 'alpha beta')
    for c in classes:
        if c['greek'].lower() == raw:
            return c

    # Season + year in any common shape
    m = re.match(r"^(f|fa|fall|s|sp|spring)[\s.'-]*((?:20)?\d{2})$", raw)
    if m:
        season = 'Fall' if m.group(1).startswith('f') else 'Spring'
        year = int(m.group(2))
        if year < 100:
            year += 2000
        return class_by_label(f'{season} {year}', today)

    return class_by_label(raw, today)


def apply_to_fields(pledge_class_text, pledge_class_greek_text, today=None):
    """Canonicalize a submitted (class, greek) pair for saving.

    Returns (pledge_class, pledge_class_greek). If the semester text resolves
    to a known class, BOTH fields are set from the registry (canonical label
    + its greek — so the greek always matches the class and typos are fixed).
    If it doesn't resolve, the typed values are preserved verbatim (legacy /
    non-standard members keep full freedom).
    """
    pc = (pledge_class_text or '').strip()
    greek = (pledge_class_greek_text or '').strip()
    c = normalize(pc, today)
    if c:
        return c['label'], c['greek']
    # Semester didn't resolve — maybe they only picked/typed a greek name.
    c2 = normalize(greek, today)
    if c2 and not pc:
        return c2['label'], c2['greek']
    return pc, greek


def badge_context(pledge_class, pledge_class_greek=None, today=None):
    """Directory-badge info for a stored (class, greek) pair, or None.

    Trusts an explicit 'Founder' greek (legacy data) even if the semester
    text doesn't parse; otherwise resolves via normalize().
    """
    if (pledge_class_greek or '').strip().lower() in ('founder', 'founders'):
        return {'greek': FOUNDERS_GREEK, 'color': FOUNDERS_COLOR,
                'is_founders': True}
    c = normalize(pledge_class, today)
    if not c:
        return None
    return {'greek': c['greek'], 'color': c['color'],
            'is_founders': c['is_founders']}
