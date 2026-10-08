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


def edit_items(original, proposed):
    """
    The separate edits between two versions of a section's text.

    Each item is {'kind': 'strike' | 'add' | 'change', 'before': str,
    'after': str, 'where': str}. `where` is set for an addition only and says
    where the new words go ('at the end', 'after "shall be paid"').
    Whitespace and line endings are ignored, so a CRLF copy of the same text
    has no items.
    """
    before, after = _words(original), _words(proposed)
    if before == after:
        return []
    if before and after and SequenceMatcher(a=before, b=after, autojunk=False).ratio() < REWRITE_RATIO:
        return [{'kind': 'change', 'before': _join(before), 'after': _join(after), 'where': ''}]
    items = []
    for a1, a2, b1, b2 in _hunks(before, after):
        removed, added = before[a1:a2], after[b1:b2]
        kind = 'change' if removed and added else ('strike' if removed else 'add')
        items.append({
            'kind': kind, 'before': _join(removed), 'after': _join(added),
            'where': _where(before, a1) if kind == 'add' else '',
        })
    return items


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
    if len(items) == 1:
        return f'{lead}{_single(items[0])}'
    lines = [f'{lead} as follows:']
    for index, item in enumerate(items):
        lines.append(f'({_letter(index)}) {_listed(item)}')
    return '\n'.join(lines)


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
