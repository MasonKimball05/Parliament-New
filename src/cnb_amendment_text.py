"""
The sentence a resolution uses to refer to one of its amendments (v3.45.0, in changelogs/v3.44.9.md).

Mason, 10-08-26: the print preview's amendment reference should read as the
motion it is.

  * Text only removed:
        Shall strike from Article II, Section 3 of the Bylaws (Dues): "..."
  * Text added or reworded:
        Shall amend Article II, Section 3 of the Bylaws (Dues) ...
    with what is added, or the before and the after for a rewording.
  * Several separate edits to one section: one lead-in, then a lettered list.

A resolution holds ONE amendment per section (`add_amendment` updates the
existing row), and that amendment stores the whole new section text. So
"several edits to one section" means several separate places where the old
and new text differ. `describe()` finds those places with a word-level diff
and returns them as items; the template only has to print them.

This lives on the server, not in the page's script, so it can be tested: the
suite has no browser.
"""
import re
from difflib import SequenceMatcher

#: Two edits separated by this many unchanged words or fewer are reported as
#: one edit that includes the words between them. Without it, rewording a
#: phrase reads as a string of one-word edits ("five" to "ten", "days" to
#: "weeks") with nothing to locate them by.
MERGE_GAP = 2

#: Below this share of words in common the section was rewritten, not edited.
#: It is reported as one change of the whole text.
REWRITE_RATIO = 0.4

#: v3.45.1 — above this many words (old text plus new text) the two are not
#: compared word by word; the amendment is reported as one change of the whole
#: text, the same as a rewrite. The comparison's cost grows with the square of
#: the length, it runs on every load of the print preview, and nothing limits
#: how long a proposed text may be. Measured on the 2-core test machine:
#: 20,000 words of ordinary prose took 1.5 s, 60,000 words of repetitive text
#: took over three minutes. The longest section in the seeded documents is
#: 1,373 words, so about 2,750 with its amended copy.
MAX_DIFF_WORDS = 6000

_SNAPSHOT = re.compile(r'^(?P<doc>.+?) Art\. (?P<article>\S+) § (?P<section>\S+)')


#: A word of three or more characters with punctuation after it: "semester."
#: The punctuation is compared on its own, so striking the last words of a
#: sentence is a strike, not a change of "meeting" into "meeting.". Shorter
#: tokens are left whole: they are list markers ("a.", "2.", "ii.").
_TRAILING = re.compile(r'^(.{3,}?)([.,;:!?]+)$')


class _Token(str):
    """A word. `glued` is True for punctuation that sits against the word before it."""
    glued = False


def _words(text):
    tokens = []
    for raw in (text or '').replace('\r\n', '\n').replace('\r', '\n').split():
        match = _TRAILING.match(raw)
        if match and not match.group(1).isdigit():
            tokens.append(_Token(match.group(1)))
            mark = _Token(match.group(2))
            mark.glued = True
            tokens.append(mark)
        else:
            tokens.append(_Token(raw))
    return tokens


def _join(words):
    text = ''
    for index, word in enumerate(words):
        text += word if (index == 0 or word.glued) else ' ' + word
    return text


#: How many of the words before an addition are quoted to say where it goes.
ANCHOR_WORDS = 4


def _pure_kind(hunk):
    """'strike' / 'add' for a hunk that only removes or only adds, else None."""
    a1, a2, b1, b2 = hunk
    if a2 > a1 and b2 == b1:
        return 'strike'
    if b2 > b1 and a2 == a1:
        return 'add'
    return None


def _hunks(before, after):
    """[[a_start, a_end, b_start, b_end]] for each place the two texts differ."""
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    hunks = []
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        hunk = [a1, a2, b1, b2]
        near = hunks and a1 - hunks[-1][1] <= MERGE_GAP
        # Two strikes (or two additions) close together stay separate:
        # merging them would turn "strike X" and "strike Y" into a rewording.
        same_pure = near and _pure_kind(hunk) and _pure_kind(hunk) == _pure_kind(hunks[-1])
        if near and not same_pure:
            hunks[-1][1], hunks[-1][3] = a2, b2      # swallow the short gap
        else:
            hunks.append(hunk)
    return hunks


def _where(before, a1):
    """Where an addition goes, in words: 'at the end', 'after "..."'."""
    if a1 == 0:
        return 'at the beginning'
    if a1 >= len(before):
        return 'at the end'
    return 'after “' + _join(before[max(0, a1 - ANCHOR_WORDS):a1]) + '”'


#: A list marker at the start of a line: "a.", "2.", "iv.", "(1)", "b)".
_MARKER = re.compile(r'^\(?(?:\d{1,3}|[a-z]{1,2}|[ivxlc]{1,6})[.)]$', re.IGNORECASE)

#: Two lines whose words are at least this alike are the same line, edited.
#: Below it they are a line struck and a different line added.
LINE_MATCH_RATIO = 0.5

#: An addition placed between lines is located by the line before it, quoted
#: whole when it is this many words or fewer (a list item), else by its last
#: ANCHOR_WORDS words.
SHORT_LINE_WORDS = 10


class _Line:
    """One non-blank line: its list marker ('' if none) and the words after it."""

    def __init__(self, text, start):
        self.words = _words(text)
        self.start = start                    # index of its first word in the whole text
        first = self.words[0] if self.words else ''
        self.marker, self.body = '', self.words
        if _MARKER.match(first):
            self.marker, self.body = first, self.words[1:]
        elif len(self.words) > 1 and self.words[1] == '.' and _MARKER.match(first + '.'):
            # "viii." is long enough for `_words` to split the full stop off.
            self.marker, self.body = first + '.', self.words[2:]
        self.key = ' '.join(self.body)


def _lines(text):
    lines, start = [], 0
    for raw in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if raw.strip():
            line = _Line(raw, start)
            lines.append(line)
            start += len(line.words)
    return lines


def _pair(old, new):
    """
    Line up the lines of a changed block. Returns (old_index | None, new_index | None)
    in order: a pair is one line edited, a lone old line is struck, a lone new
    line is added. Within a stretch with no pair, strikes come before additions.
    """
    pairs, i, waiting = [], 0, []
    for j, line in enumerate(new):
        for k in range(i, len(old)):
            if SequenceMatcher(a=old[k].words, b=line.words, autojunk=False).ratio() >= LINE_MATCH_RATIO:
                pairs.extend((x, None) for x in range(i, k))
                pairs.extend((None, w) for w in waiting)
                pairs.append((k, j))
                i, waiting = k + 1, []
                break
        else:
            waiting.append(j)
    pairs.extend((x, None) for x in range(i, len(old)))
    pairs.extend((None, w) for w in waiting)
    return pairs


def _after_line(line):
    words = line.words
    if len(words) <= SHORT_LINE_WORDS:
        return 'after “' + _join(words) + '”'
    return 'after “' + _join(words[-ANCHOR_WORDS:]) + '”'


def edit_items(original, proposed):
    """
    The separate edits between two versions of a section's text.

    Each item is {'kind': 'strike' | 'add' | 'change', 'before': str,
    'after': str, 'where': str}. `where` is set for an addition only and says
    where the new words go ('at the end', 'after "shall be paid"').
    Whitespace and line endings are ignored, so a CRLF copy of the same text
    has no items.

    v3.45.4 — LINE BY LINE FIRST. Mason, 10-13-26, on the print preview: taking
    one officer out of a lettered list read as "Change Administration to read
    Brotherhood, Brotherhood to read Finance, Finance to read Recruitment...",
    because a word-level diff sees every item below the removed one move up a
    letter. Lines are now matched by their words WITHOUT the list marker, so:

      * a line that only got a new letter or number is not an edit (see
        `renumbered`, which says the list was relettered);
      * a line that is gone is one strike, a new line is one addition, placed
        after the line before it;
      * a line that was edited is diffed word by word, as before.
    """
    if _words(original) == _words(proposed):
        return []
    before_words, after_words = _words(original), _words(proposed)
    # v3.45.1: past MAX_DIFF_WORDS the whole text is one change, so a huge
    # paste cannot make SequenceMatcher (quadratic) hold a request thread.
    too_long = len(before_words) + len(after_words) > MAX_DIFF_WORDS
    if before_words and after_words and (too_long or SequenceMatcher(
            a=before_words, b=after_words, autojunk=False).ratio() < REWRITE_RATIO):
        return [{'kind': 'change', 'before': _join(before_words), 'after': _join(after_words), 'where': ''}]

    old, new = _lines(original), _lines(proposed)
    matcher = SequenceMatcher(a=[l.key for l in old], b=[l.key for l in new], autojunk=False)
    items = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        pairs = [(k, None) for k in range(i1, i2)] + [(None, k) for k in range(j1, j2)] \
            if tag != 'replace' else _pair(old[i1:i2], new[j1:j2])
        if tag == 'replace':
            pairs = [(None if a is None else a + i1, None if b is None else b + j1) for a, b in pairs]
        # The old line the next addition follows.
        anchor = i1 - 1
        run_kind, run = None, []

        def flush():
            if not run:
                return
            text = _join([w for line in run for w in line.words])
            if run_kind == 'strike':
                items.append({'kind': 'strike', 'before': text, 'after': '', 'where': '', '_run': 'strike'})
            elif items and items[-1].get('_run') == 'strike':
                # Lines struck and other lines put in their place: one change.
                struck = items.pop()
                items.append({'kind': 'change', 'before': struck['before'], 'after': text, 'where': ''})
            else:
                if anchor < 0:
                    where = 'at the beginning'
                elif anchor >= len(old) - 1:
                    where = 'at the end'
                else:
                    where = _after_line(old[anchor])
                items.append({'kind': 'add', 'before': '', 'after': text, 'where': where})

        for a, b in pairs:
            if a is not None and b is not None:
                flush()
                run_kind, run = None, []
                items.extend(_line_items(old[a], new[b], old))
                anchor = a
            elif a is not None:
                if run_kind != 'strike':
                    flush()
                    run_kind, run = 'strike', []
                run.append(old[a])
            else:
                if run_kind != 'add':
                    flush()
                    run_kind, run = 'add', []
                run.append(new[b])
        flush()
    for item in items:
        item.pop('_run', None)
    return items


def _line_items(old_line, new_line, lines):
    """Word-level edits inside one line that was changed (its marker aside)."""
    items = []
    for a1, a2, b1, b2 in _hunks(old_line.body, new_line.body):
        removed, added = old_line.body[a1:a2], new_line.body[b1:b2]
        kind = 'change' if removed and added else ('strike' if removed else 'add')
        items.append({
            'kind': kind, 'before': _join(removed), 'after': _join(added),
            'where': _where_in_line(old_line, a1, lines) if kind == 'add' else '',
        })
    return items


def _where_in_line(line, a1, lines):
    """Where words added inside a line go, without quoting past the line's own edges."""
    head = len(line.words) - len(line.body)          # the marker, if any
    at = head + a1
    if at == 0:
        return 'at the beginning' if line is lines[0] else 'at the start of “' + _join(line.words[:ANCHOR_WORDS]) + '”'
    if at >= len(line.words):
        return 'at the end' if line is lines[-1] else _after_line(line)
    return 'after “' + _join(line.words[max(0, at - ANCHOR_WORDS):at]) + '”'


def renumbered(original, proposed):
    """True when some unchanged line only got a new list letter or number."""
    old, new = _lines(original), _lines(proposed)
    matcher = SequenceMatcher(a=[l.key for l in old], b=[l.key for l in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal' and any(old[i1 + n].marker != new[j1 + n].marker for n in range(i2 - i1)):
            return True
    return False


def section_reference(amendment):
    """'Article II, Section 3 of the Bylaws (Dues)' for an amendment's section."""
    section = amendment.section
    article = section.article
    doc = article.document.get_doc_type_display()
    article_number, section_number = article.number, section.former_number or section.number
    # A passed resolution keeps citing the numbers it was voted on with, even
    # if a later one renumbered the section (`identifier_snapshot`, v3.43.0).
    match = _SNAPSHOT.match(amendment.identifier_snapshot or '')
    if match:
        doc, article_number, section_number = match.group('doc', 'article', 'section')
    reference = f'Article {article_number}, Section {section_number} of the {doc}'
    if section.title:
        reference += f' ({section.title})'
    return reference


def describe(amendment):
    """
    How the resolution states this amendment.

    Returns a dict:
        action     'strike' (text is only removed) or 'amend'
        whole      True when the whole section is struck
        reference  'Article II, Section 3 of the Bylaws (Dues)'
        lead       'Shall strike from <reference>' / 'Shall amend <reference>'
        scope      the amendment's scope note ('' if none)
        items      see `edit_items`
        renumbered True when list items below an edit only get a new letter
                   or number (v3.45.4); the motion says so in one line
                   instead of listing each one as a change
    """
    reference = section_reference(amendment)
    items = edit_items(amendment.original_text_snapshot, amendment.proposed_text)
    whole = amendment.is_whole_section_removal or (bool(items) and not (amendment.proposed_text or '').strip())
    if whole:
        action, lead = 'strike', f'Shall strike {reference} in its entirety'
    elif items and all(item['kind'] == 'strike' for item in items):
        action, lead = 'strike', f'Shall strike from {reference}'
    else:
        action, lead = 'amend', f'Shall amend {reference}'
    return {
        'action': action, 'whole': whole, 'reference': reference, 'lead': lead,
        'scope': amendment.scope_note or '', 'items': items,
        'renumbered': not whole and renumbered(amendment.original_text_snapshot, amendment.proposed_text),
    }


def sentence(amendment):
    """
    The reference as plain text, the same wording the print page builds.

    One edit is a single sentence. Several are a lead-in and a lettered list,
    returned here on one line each.
    """
    said = describe(amendment)
    lead, items = said['lead'], said['items']
    if said['scope']:
        lead += f' ({said["scope"]})'
    if said['whole']:
        text = items[0]['before'] if items else ''
        return f'{lead}: “{text}”' if text else f'{lead}.'
    if not items:
        return f'{lead}. No change to the text.'
    note = f'\n{RENUMBERED_NOTE}' if said['renumbered'] else ''
    if len(items) == 1:
        return f'{lead}{_single(items[0])}{note}'
    lines = [f'{lead} as follows:']
    for index, item in enumerate(items):
        lines.append(f'({_letter(index)}) {_listed(item)}')
    return '\n'.join(lines) + note


#: The line a motion adds when list items only moved up or down a letter.
RENUMBERED_NOTE = 'The items that follow are renumbered to match.'


def _single(item):
    if item['kind'] == 'strike':
        return f': “{item["before"]}”'
    if item['kind'] == 'add':
        return f' by adding {item["where"]}: “{item["after"]}”'
    return f' by changing “{item["before"]}” to read “{item["after"]}”'


def _listed(item):
    if item['kind'] == 'strike':
        return f'Strike “{item["before"]}”'
    if item['kind'] == 'add':
        return f'Add {item["where"]}: “{item["after"]}”'
    return f'Change “{item["before"]}” to read “{item["after"]}”'


def _letter(index):
    """0 -> a, 25 -> z, 26 -> aa."""
    letters = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord('a') + rem) + letters
    return letters
