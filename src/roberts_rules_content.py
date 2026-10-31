"""
Robert's Rules of Order as data (v4.4.0): the reference the website's
``roberts_rules`` page shows (templates/roberts_rules.html), for the app's
native screen (``src/mobile/views_roberts_rules.py``).

The web template stays hand-written HTML (its colored callouts and layout
would not survive a move to a generic renderer), so this module is a second
copy of the same words. ``src/tests/mobile/test_roberts_rules.py`` keeps the
two in step: every section, heading and motion on the page must be here, and
the Quick Reference Table's cells must match ``MOTIONS``. Edit both together.

Two parts:

* ``MOTIONS`` — every motion the page describes, with what it's for, what to
  say, and its rules (second, debate, amendment, vote). ``quick`` holds the
  Quick Reference Table's short cells; ``rules`` the longer wording from the
  motion's own card.
* ``SECTIONS`` — the page's sections in order, each a list of blocks:

  ``heading`` / ``subheading`` {text}; ``paragraph`` {text, style: body |
  label | note}; ``list`` {ordered, items: [{text, items}]}; ``box`` {tone:
  chapter | accent | success | warning | danger | neutral, title, blocks} (the
  page's callouts; ``chapter`` is an Alpha Mu rule); ``table`` {columns,
  rows}; ``sample`` {lines} (a monospaced example: a ballot, minutes);
  ``motion`` {id} (that motion's card, from ``MOTIONS``); ``quick_reference``
  (the table of every motion in ``MOTIONS`` with ``quick``).

Text uses ``**bold**`` for the page's bold runs and nothing else.
"""


# ── Block helpers ────────────────────────────────────────────────────────────

def heading(text):
    return {'type': 'heading', 'text': text}


def subheading(text):
    return {'type': 'subheading', 'text': text}


def para(text):
    return {'type': 'paragraph', 'text': text, 'style': 'body'}


def label(text):
    """A bold lead-in line ("If quorum not present:")."""
    return {'type': 'paragraph', 'text': text, 'style': 'label'}


def note(text):
    """A small, muted aside."""
    return {'type': 'paragraph', 'text': text, 'style': 'note'}


def _items(items):
    out = []
    for item in items:
        if isinstance(item, tuple):
            text, children = item
            out.append({'text': text, 'items': [{'text': c, 'items': []} for c in children]})
        else:
            out.append({'text': item, 'items': []})
    return out


def bullets(*items):
    """A bulleted list; an item is a string, or (text, [sub-items])."""
    return {'type': 'list', 'ordered': False, 'items': _items(items)}


def steps(*items):
    """A numbered list."""
    return {'type': 'list', 'ordered': True, 'items': _items(items)}


def box(tone, title, blocks, link=None):
    """
    A callout. ``link`` is (label, anchor) for the Constitution & Bylaws
    passage a chapter rule cites (the page links its text there); the API
    turns it into {label, path}.
    """
    out = {'type': 'box', 'tone': tone, 'title': title, 'blocks': blocks}
    if link:
        out['link'] = {'label': link[0], 'anchor': link[1]}
    return out


def table(columns, rows):
    return {'type': 'table', 'columns': columns, 'rows': rows}


def sample(*lines):
    return {'type': 'sample', 'lines': list(lines)}


def motion(motion_id):
    return {'type': 'motion', 'id': motion_id}


def quick_reference():
    return {'type': 'quick_reference'}


def section(section_id, title, blocks):
    return {'id': section_id, 'title': title, 'blocks': blocks}


# ── Motions ──────────────────────────────────────────────────────────────────

#: (key, label, what the page says about the category), in the page's order.
CATEGORIES = (
    ('main', 'Main', "Main motions introduce new business for the assembly's consideration."),
    ('subsidiary', 'Subsidiary', 'Subsidiary motions change or affect how a main motion is handled. Listed in '
                                 'order of precedence (highest to lowest).'),
    ('privileged', 'Privileged', 'Privileged motions relate to special matters of immediate importance. Listed '
                                 'in order of precedence (highest to lowest).'),
    ('incidental', 'Incidental', 'Incidental motions arise out of other questions and must be decided before the '
                                 'pending question. No order of precedence among themselves.'),
    ('restore', 'Reconsider & rescind', 'These motions allow an assembly to revisit and potentially reverse '
                                         'previous decisions.'),
)


def _motion(motion_id, name, category, section_id, purpose, form, rules, quick=None, short_name=None, rank=None,
            notes=(), blocks=()):
    return {
        'id': motion_id,
        'name': name,
        # The Quick Reference Table's name for it, when that differs.
        'short_name': short_name or name,
        'category': category,
        # Place in its category's order of precedence (1 = highest); None for
        # incidental motions, which have none among themselves.
        'rank': rank,
        'section': section_id,
        'purpose': purpose,
        'form': form,
        'rules': [{'label': k, 'value': v} for k, v in rules],
        'notes': list(notes),
        'blocks': list(blocks),
        # {second, debate, amend, vote}: the Quick Reference Table's row. None
        # for the two the table leaves out (reconsider, rescind).
        'quick': dict(zip(('second', 'debate', 'amend', 'vote'), quick)) if quick else None,
    }


MOTIONS = [
    _motion('main', 'Main Motion', 'main', 'main-motions',
            "Introduce new business for the assembly's consideration",
            '"I move that..."',
            [('Requires second', 'Yes'), ('Debatable', 'Yes'), ('Amendable', 'Yes'), ('Vote required', 'Majority')],
            quick=('Yes', 'Yes', 'Yes', 'Majority')),

    # Subsidiary, highest precedence first.
    _motion('lay_on_table', 'Lay on the Table', 'subsidiary', 'subsidiary-motions',
            'Temporarily set aside a pending motion',
            '"I move to lay the question on the table"',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'No'), ('Vote required', 'Majority')],
            quick=('Yes', 'No', 'No', 'Majority'), rank=1),
    _motion('previous_question', 'Previous Question (Call the Question)', 'subsidiary', 'subsidiary-motions',
            'Immediately end debate and vote',
            '"I move the previous question" or "I call the question"',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'No'),
             ('Vote required', 'Two-thirds')],
            quick=('Yes', 'No', 'No', '2/3'), short_name='Previous Question', rank=2),
    _motion('limit_debate', 'Limit or Extend Debate', 'subsidiary', 'subsidiary-motions',
            'Modify the limits of debate',
            '"I move that debate be limited to..."',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'Yes'),
             ('Vote required', 'Two-thirds')],
            quick=('Yes', 'No', 'Yes', '2/3'), short_name='Limit/Extend Debate', rank=3),
    _motion('postpone_definitely', 'Postpone to a Certain Time', 'subsidiary', 'subsidiary-motions',
            'Delay consideration until a specific time',
            '"I move to postpone the motion until..."',
            [('Requires second', 'Yes'), ('Debatable', 'Yes (limited to whether to postpone)'),
             ('Amendable', 'Yes'), ('Vote required', 'Majority')],
            quick=('Yes', 'Limited', 'Yes', 'Majority'), short_name='Postpone to Certain Time', rank=4),
    _motion('refer', 'Refer to Committee (Commit)', 'subsidiary', 'subsidiary-motions',
            'Send a motion to a committee for study',
            '"I move to refer the motion to..."',
            [('Requires second', 'Yes'), ('Debatable', 'Yes (limited)'), ('Amendable', 'Yes'),
             ('Vote required', 'Majority')],
            quick=('Yes', 'Limited', 'Yes', 'Majority'), short_name='Refer to Committee', rank=5),
    _motion('amend', 'Amend', 'subsidiary', 'subsidiary-motions',
            'Modify the pending motion',
            '"I move to amend the motion by..."',
            [('Requires second', 'Yes'), ('Debatable', 'Yes (if main motion is debatable)'),
             ('Amendable', 'Yes (but not beyond second degree)'), ('Vote required', 'Majority')],
            quick=('Yes', 'Yes*', 'Yes', 'Majority'), rank=6,
            blocks=[box('neutral', 'Three ways to amend:', [
                bullets(
                    'Insert or add words/paragraphs',
                    'Strike out words/paragraphs',
                    'Strike out and insert (substitute)',
                ),
            ])]),
    _motion('postpone_indefinitely', 'Postpone Indefinitely', 'subsidiary', 'subsidiary-motions',
            'Kill the main motion without a direct vote',
            '"I move to postpone the motion indefinitely"',
            [('Requires second', 'Yes'), ('Debatable', 'Yes (opens main question to debate)'),
             ('Amendable', 'No'), ('Vote required', 'Majority')],
            quick=('Yes', 'Yes', 'No', 'Majority'), rank=7),

    # Privileged, highest precedence first.
    _motion('fix_time_to_adjourn', 'Fix the Time to Which to Adjourn', 'privileged', 'privileged-motions',
            'Set the time and place for continued meeting',
            '"I move that when we adjourn, we adjourn to meet at..."',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'Yes'), ('Vote required', 'Majority')],
            quick=('Yes', 'No', 'Yes', 'Majority'), short_name='Fix Time to Adjourn', rank=1),
    _motion('adjourn', 'Adjourn', 'privileged', 'privileged-motions',
            'Close the meeting',
            '"I move to adjourn"',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'No'), ('Vote required', 'Majority')],
            quick=('Yes', 'No', 'No', 'Majority'), rank=2),
    _motion('recess', 'Recess', 'privileged', 'privileged-motions',
            'Take a short break',
            '"I move to recess for..."',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'Yes (as to length of recess)'),
             ('Vote required', 'Majority')],
            quick=('Yes', 'No', 'Yes', 'Majority'), rank=3),
    _motion('question_of_privilege', 'Question of Privilege', 'privileged', 'privileged-motions',
            'Bring urgent matters affecting the assembly or a member',
            '"I rise to a question of privilege" (then state the issue)',
            [('Requires second', 'No'), ('Debatable', 'No (chair rules immediately)'), ('Amendable', 'No'),
             ('Vote required', 'None (chair decides)')],
            quick=('No', 'No', 'No', 'Chair'), rank=4,
            blocks=[box('accent', 'Examples:', [
                bullets(
                    'Room is too noisy/hot/cold',
                    'Cannot hear the speaker',
                    'Safety concerns',
                    'Personal attack or rights violation',
                ),
            ])]),
    _motion('orders_of_the_day', 'Call for the Orders of the Day', 'privileged', 'privileged-motions',
            'Demand that the agenda be followed',
            '"I call for the orders of the day"',
            [('Requires second', 'No'), ('Debatable', 'No'), ('Amendable', 'No'),
             ('Vote required', 'None (chair must comply or 2/3 vote to set aside)')],
            quick=('No', 'No', 'No', 'None'), short_name='Orders of the Day', rank=5),

    # Incidental: no order among themselves.
    _motion('point_of_order', 'Point of Order', 'incidental', 'incidental-motions',
            'Enforce rules of procedure',
            '"Point of order!" or "I rise to a point of order"',
            [('Requires second', 'No'), ('Debatable', 'No')],
            quick=('No', 'No', 'No', 'Chair'),
            notes=['Chair rules on the point immediately']),
    _motion('appeal', 'Appeal from the Decision of the Chair', 'incidental', 'incidental-motions',
            "Challenge the chair's ruling",
            '"I appeal from the decision of the chair"',
            [('Requires second', 'Yes'), ('Debatable', 'Yes (in most cases)'),
             ('Vote required', 'Majority (tie sustains chair)')],
            quick=('Yes', 'Yes*', 'No', 'Majority'), short_name='Appeal'),
    _motion('suspend_rules', 'Suspend the Rules', 'incidental', 'incidental-motions',
            'Temporarily set aside standing rules',
            '"I move to suspend the rules to..."',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Vote required', 'Two-thirds')],
            quick=('Yes', 'No', 'No', '2/3'), short_name='Suspend Rules',
            notes=['Cannot suspend bylaws or fundamental rules']),
    _motion('object_to_consideration', 'Object to Consideration of a Question', 'incidental', 'incidental-motions',
            'Suppress inappropriate or sensitive motion',
            '"I object to consideration of this question"',
            [('Requires second', 'No'), ('Debatable', 'No'), ('Vote required', 'Two-thirds to sustain objection')],
            quick=('No', 'No', 'No', '2/3'), short_name='Object to Consideration',
            notes=['Must be made before debate begins']),
    _motion('division_of_question', 'Division of a Question', 'incidental', 'incidental-motions',
            'Separate a motion into independent parts',
            '"I move to divide the question..."',
            [('Requires second', 'Yes'), ('Debatable', 'No'), ('Amendable', 'Yes'), ('Vote required', 'Majority')],
            quick=('Yes', 'No', 'Yes', 'Majority'), short_name='Division of Question'),
    _motion('parliamentary_inquiry', 'Parliamentary Inquiry', 'incidental', 'incidental-motions',
            'Ask the chair a question about procedure',
            '"Parliamentary inquiry" or "Point of information"',
            [('Requires second', 'No')],
            quick=('No', '-', '-', '-'),
            notes=['Not a motion - request for information', 'Chair answers or directs to parliamentarian']),

    # Bringing a question back (not in the Quick Reference Table).
    _motion('reconsider', 'Motion to Reconsider', 'restore', 'reconsider-rescind',
            'Bring a motion back for another vote during the same session or within the same day (depending on '
            'organization)',
            '"I move to reconsider the vote on..."',
            [('Requires second', 'Yes'), ('Debatable', 'Only if original motion was debatable'),
             ('Amendable', 'No'), ('Vote required', 'Majority'),
             ('Time limit', 'Same meeting/session (or next meeting for committees)')],
            notes=['Must be made by someone who voted on the prevailing side',
                   'Can only reconsider once (cannot reconsider a reconsideration)',
                   'Can be made while another motion is pending',
                   'Takes precedence over any other motion']),
    _motion('rescind', 'Motion to Rescind (Amend Something Previously Adopted)', 'restore', 'reconsider-rescind',
            'Cancel or repeal a motion previously adopted, with no time limit',
            '"I move to rescind the motion that..." or "I move to amend the motion previously adopted by..."',
            [('Requires second', 'Yes'), ('Debatable', 'Yes'), ('Amendable', 'Yes'),
             ('Vote required', '2/3 majority, OR majority with previous notice'),
             ('Time limit', 'None (can be used anytime)')],
            short_name='Rescind',
            notes=['Can be made by any member',
                   'Can be used to nullify actions from previous meetings',
                   'Useful for reversing outdated or problematic decisions']),
]

MOTIONS_BY_ID = {m['id']: m for m in MOTIONS}


# ── Sections ─────────────────────────────────────────────────────────────────

SECTIONS = [
    section('overview', 'Overview', [
        para("Robert's Rules of Order is the most widely used manual of parliamentary procedure in the United "
             'States. It governs the meetings of a deliberative assembly, ensuring fairness, efficiency, and order '
             'in the decision-making process.'),
        heading('Basic Principles'),
        bullets(
            '**Justice and courtesy to all members** - Every member has equal rights, privileges, and obligations.',
            '**Only one question at a time** - Debates and votes on one motion before considering another.',
            ('**The rule of the majority, with respect for the minority** - Majority decides, but minority has the '
             'right to be heard.'),
            '**Every member has the right to be heard** - All members may speak when properly recognized.',
        ),
        heading('Quorum'),
        para('A quorum is the minimum number of members that must be present for business to be conducted legally. '
             'See the Quorum Requirements section for detailed information.'),
    ]),
    section('quorum', 'Quorum Requirements', [
        para('A quorum is the minimum number of voting members who must be present at a meeting for business to be '
             'legally transacted.'),
        box('chapter', 'Alpha Mu Chapter Requirement', [
            para('According to our Constitution Article IV, Section 3, quorum is defined as a **simple majority of '
                 'Active Members**.'),
        ], link=('Constitution Article IV, Section 3', 'const-meetings')),
        heading('General Quorum Principles'),
        bullets(
            ('**Default standard:** Unless otherwise specified in bylaws, quorum is a majority of all members of the'
             ' organization'),
            '**Present in person:** Members must be physically present (or virtually if authorized by bylaws)',
            "**Cannot be less than 1/3:** Robert's Rules recommends never setting quorum below one-third of members",
            ('**Should reflect active participation:** Set high enough to be representative, low enough to be '
             'achievable'),
        ),
        heading('Verifying Quorum'),
        box('neutral', 'Before Opening Meeting:', [
            bullets(
                'Chair or secretary counts all members present',
                'Compare count to quorum requirement',
                'If quorum is not present, meeting cannot be called to order',
            ),
            label('If Quorum Not Present:'),
            bullets(
                'Meeting cannot conduct business',
                'Members may take a recess and attempt to gather more members',
                'May fix time for adjourned meeting',
                'May take measures to obtain quorum at adjourned meeting',
            ),
        ]),
        heading('Loss of Quorum During Meeting'),
        box('danger', 'Critical Rule:', [
            para('If quorum is lost during a meeting (members leave), the meeting can no longer conduct business. '
                 'Any votes taken without quorum present are null and void.'),
        ]),
        box('neutral', 'When Quorum is Lost:', [
            steps(
                'Point of order may be raised: "Point of order! We no longer have a quorum present."',
                'Chair verifies the count',
                ('If confirmed, chair announces: "The chair finds that a quorum is no longer present. No business '
                 'can be conducted."'),
                'Chair entertains motion to adjourn or recess',
            ),
        ]),
        heading('Actions Allowed Without Quorum'),
        box('warning', 'Even without quorum present, members may:', [
            bullets(
                'Fix the time to which to adjourn (schedule a continuation meeting)',
                'Adjourn the meeting',
                'Take a recess',
                'Take measures to obtain a quorum (call/text members, send runner, etc.)',
            ),
            note('These actions allow the organization to function administratively without conducting substantive '
                 'business.'),
        ]),
        heading('Quorum in Committees'),
        para('Committee quorum rules differ from main assembly:'),
        bullets(
            '**Default:** Majority of all committee members (not just those present)',
            '**Cannot be set below majority:** Unlike assembly quorum, committee quorum must always be majority',
            '**Good practice:** Small committees (5 or fewer) should require all members present',
        ),
    ]),
    section('order-of-business', 'Order of Business', [
        para('The standard order of business for a meeting:'),
        steps(
            '**Call to Order** - The presiding officer opens the meeting.',
            '**Reading and Approval of Minutes** - Minutes from the previous meeting are read and approved.',
            '**Reports of Officers, Boards, and Standing Committees** - Regular reports from leadership.',
            '**Reports of Special (Select or Ad Hoc) Committees** - Reports from temporary committees.',
            '**Special Orders** - Items scheduled for a specific time.',
            '**Unfinished Business and General Orders** - Business carried over from previous meeting.',
            '**New Business** - New motions and topics.',
            '**Announcements** - Informational items.',
            '**Adjournment** - Closing of the meeting.',
        ),
    ]),
    section('special-meetings', 'Special Meetings', [
        para('A special meeting is a meeting called at a time other than the regular meeting schedule to address '
             'specific urgent or important business.'),
        box('chapter', 'Alpha Mu Chapter Requirements', [
            para('According to our Constitution Article IV, Section 2:'),
            bullets(
                'Special meetings require **4 hours advance notice**',
                'May be called by: President, Executive Board, Advisory Team, or Supermajority of active members',
                ('New business introduced at regular meetings cannot be voted on until subsequent meeting unless 2/3'
                 ' present deem it necessary'),
            ),
        ], link=('Constitution Article IV, Section 2', 'const-meetings')),
        heading('Who Can Call Special Meetings'),
        box('neutral', "Typical authorities (varies by organization's bylaws):", [
            bullets(
                '**President or presiding officer** - Most common authority',
                '**Board or executive committee** - Acting collectively',
                ('**A specified number/percentage of members** - Democratic safeguard (e.g., "10 members" or "20% of'
                 ' membership")'),
                '**Other officers** - If specified in bylaws',
            ),
            note('Always check your specific bylaws for who has the authority to call special meetings.'),
        ]),
        heading('Notice Requirements'),
        box('warning', 'Required Elements of Notice:', [
            bullets(
                '**Time and date** of the meeting',
                '**Location** (physical address or virtual meeting link)',
                '**Purpose** - The specific business to be transacted',
                '**Who called it** - Authority invoking the special meeting',
            ),
        ]),
        box('neutral', 'Timing of Notice:', [
            bullets(
                'Bylaws should specify minimum notice period (e.g., "24 hours," "one week")',
                'Notice must reach all members entitled to attend',
                'Electronic notice (email, text) is acceptable if authorized by bylaws',
                'Insufficient notice invalidates any business conducted',
            ),
        ]),
        heading('Scope of Business at Special Meetings'),
        box('danger', 'Critical Limitation:', [
            para('Only business specified in the call of the special meeting may be transacted. No other business is'
                 ' in order, even if all members are present and agree.'),
        ]),
        box('neutral', 'Example:', [
            para('Notice states: "Special meeting called to vote on emergency facility repairs."'),
            para('**✓ In order:** Discussion and vote on facility repairs'),
            para(('**✗ Out of order:** Discussion of budget, elections, or any other matter not mentioned in the '
                  'notice')),
        ]),
        heading('Quorum at Special Meetings'),
        bullets(
            'Same quorum requirement as regular meetings applies',
            'Cannot conduct business without quorum present',
            'Members should not be surprised by special meetings - proper notice prevents this',
        ),
        heading('Emergency Meetings'),
        box('warning', 'True Emergencies:', [
            para('In cases of genuine emergency where normal notice is impossible:'),
            bullets(
                'Make best effort to contact all members',
                'Document the emergency circumstances',
                'Actions taken may be ratified at next regular meeting',
                'Use this exception sparingly - abuse undermines democratic process',
            ),
        ]),
    ]),
    section('main-motions', 'Main Motions', [
        para("Main motions introduce new business for the assembly's consideration."),
        box('accent', 'How to Make a Main Motion', [
            steps(
                'Wait to be recognized by the chair',
                'Say: "I move that..." (state your motion)',
                'Wait for someone to second: "I second the motion"',
                'Chair states the motion: "It has been moved and seconded that..."',
                'Debate (if debatable)',
                'Vote on the motion',
            ),
        ]),
        motion('main'),
    ]),
    section('subsidiary-motions', 'Subsidiary Motions', [
        para('Subsidiary motions change or affect how a main motion is handled. Listed in order of precedence '
             '(highest to lowest):'),
        motion('lay_on_table'),
        motion('previous_question'),
        motion('limit_debate'),
        motion('postpone_definitely'),
        motion('refer'),
        motion('amend'),
        motion('postpone_indefinitely'),
    ]),
    section('privileged-motions', 'Privileged Motions', [
        para('Privileged motions relate to special matters of immediate importance. Listed in order of precedence '
             '(highest to lowest):'),
        motion('fix_time_to_adjourn'),
        motion('adjourn'),
        motion('recess'),
        motion('question_of_privilege'),
        motion('orders_of_the_day'),
    ]),
    section('incidental-motions', 'Incidental Motions', [
        para('Incidental motions arise out of other questions and must be decided before the pending question. No '
             'order of precedence among themselves.'),
        motion('point_of_order'),
        motion('appeal'),
        motion('suspend_rules'),
        motion('object_to_consideration'),
        motion('division_of_question'),
        motion('parliamentary_inquiry'),
    ]),
    section('reconsider-rescind', 'Reconsider and Rescind', [
        para('These motions allow an assembly to revisit and potentially reverse previous decisions.'),
        box('chapter', 'Alpha Mu Chapter Protection Periods', [
            para('Our Constitution and Bylaws establish "protection periods" for failed amendments:'),
            bullets(
                '**Constitution amendments:** Cannot be reintroduced for 15 chapter meetings after failure',
                '**Bylaws amendments:** Cannot be reintroduced for 10 chapter meetings after failure',
            ),
            note('These periods prevent repeated attempts to pass the same failed amendment.'),
        ], link=('Constitution and Bylaws', 'const-amendments')),
        motion('reconsider'),
        motion('rescind'),
        heading('When to Use Each Motion'),
        box('neutral', None, [
            table(['Scenario', 'Use This'], [
                ['Realized mistake immediately after vote', 'Reconsider'],
                ['New information available same meeting', 'Reconsider'],
                ["Want to reverse last week's decision", 'Rescind'],
                ['Policy adopted years ago needs changing', 'Rescind'],
            ]),
        ]),
        heading('What Cannot Be Reconsidered or Rescinded'),
        box('danger', 'Cannot be used for:', [
            bullets(
                ('Actions where something cannot be undone (contracts signed, resignations accepted, members '
                 "expelled after they've left)"),
                "Votes that are purely procedural (like adjournment, recess after it's been taken)",
                'Nominations or elections after person has been notified and assumed office',
                'Any action where innocent third parties have acquired rights',
            ),
        ]),
        heading('Practical Examples'),
        box('success', 'Example 1: Reconsider (Same Meeting)', [
            para('**Situation:** Chapter voted to hold formal chapter at 6pm. Member who voted "yes" realizes most '
                 'members have 6pm classes.'),
            para('**Solution:** Member says: "I voted on the prevailing side, and I move to reconsider the vote on '
                 'the time of formal chapter."'),
        ]),
        box('accent', 'Example 2: Rescind (Different Meeting)', [
            para('**Situation:** Chapter voted last semester to require ties at all meetings. Members now find this '
                 'impractical.'),
            para('**Solution:** Any member says: "I move to rescind the motion requiring ties at all meetings."'),
            note('Requires 2/3 vote, OR majority if notice was given at previous meeting.'),
        ]),
        heading('Previous Notice Reduces Vote Requirement'),
        box('warning', None, [
            para('For Rescind (or Amend Something Previously Adopted):'),
            bullets(
                '**Without notice:** Requires 2/3 vote',
                '**With previous notice:** Requires only majority vote',
            ),
            note('Previous notice means announcing at a previous meeting or in the call of the meeting that this '
                 'motion will be introduced.'),
        ]),
    ]),
    section('voting', 'Voting Procedures', [
        heading('Methods of Voting'),
        box('neutral', '1. Voice Vote (Viva Voce)', [
            para('Chair: "All in favor say \'aye.\' Those opposed say \'no.\'"'),
            note('Most common method for routine motions.'),
        ]),
        box('neutral', '2. Rising Vote (Standing)', [
            para('Chair: "All in favor please rise. Be seated. Those opposed please rise."'),
            note('Used when voice vote result is unclear or for important matters.'),
        ]),
        box('neutral', '3. Show of Hands', [
            para('Chair: "All in favor raise your hand. Thank you. Those opposed raise your hand."'),
            note('Often used in small groups or committees.'),
        ]),
        box('neutral', '4. Ballot Vote', [
            para('Written votes, typically for elections or sensitive matters.'),
            note('Ensures secrecy and allows for accurate count.'),
        ]),
        box('neutral', '5. Roll Call Vote', [
            para("Each member's vote is recorded individually by name."),
            note('Used when a record of individual votes is required.'),
        ]),
        heading('Vote Requirements'),
        table(['Vote Type', 'Requirement', 'Examples'], [
            ['Majority Vote', 'More than half of votes cast', 'Most motions, adopt minutes'],
            ['Two-Thirds Vote', 'At least 2/3 of votes cast', 'Close debate, suspend rules, limit debate'],
            ['Plurality', 'Most votes (not majority)', 'Elections (if bylaws specify)'],
            ['Unanimous Consent', 'No objection', 'Approve minutes, grant requests'],
        ]),
        box('accent', 'Important Notes', [
            bullets(
                'Abstentions are not counted in determining if a majority or two-thirds vote has been achieved.',
                'The chair may vote to make or break a tie in small boards/committees.',
                'In assemblies, the chair only votes when it would change the outcome.',
                'A tie vote means the motion fails (except on appeals).',
            ),
        ]),
    ]),
    section('secret-ballot', 'Secret Ballot Procedures (Detailed)', [
        para('Secret ballots protect voter privacy and prevent coercion, commonly used for elections, sensitive '
             'motions, and membership decisions.'),
        box('chapter', 'Alpha Mu Chapter Requirements', [
            para('Our Constitution and Bylaws require secret ballots for:'),
            bullets(
                '**Officer Elections:** All Executive Board positions (Article IV, Section 5)',
                '**Slate Approval:** Officer slate votes (Bylaws Article II)',
                '**Kai Committee Decisions:** Disciplinary hearings and sanctions',
                '**Membership Selection:** Voting on potential new members',
            ),
            note('An advisor must be present for elections and PNM selection.'),
        ]),
        heading('Appointing Tellers'),
        box('neutral', 'Tellers are responsible for distributing, collecting, and counting ballots.', [
            bullets(
                '**Number:** Typically 2-3 tellers (odd number prevents ties)',
                '**Selection:** Appointed by chair or elected by assembly',
                '**Qualifications:** Must be impartial, trustworthy members in good standing',
                '**Cannot be:** Candidates, or members with direct interest in the outcome',
                "**Tellers' chairman:** One teller designated as lead to report results",
            ),
        ]),
        heading('Ballot Preparation'),
        box('success', 'Good Ballot Design:', [
            bullets(
                'Uniform size and color (prevents identification)',
                'Clear instructions at top',
                'List all candidates or choices in alphabetical order',
                'Sufficient space to write in votes or mark boxes',
                'No identifying marks, numbers, or watermarks',
            ),
        ]),
        box('neutral', 'Example Ballot Format:', [
            sample(
                'BALLOT - PRESIDENT',
                'Vote for ONE (1) candidate. Mark an X or checkmark.',
                '☐ Candidate A',
                '☐ Candidate B',
                '☐ Candidate C',
                'Leave blank to abstain.',
            ),
        ]),
        heading('Conducting the Ballot Vote'),
        box('warning', 'Step-by-Step Procedure:', [
            steps(
                '**Chair announces:** "The vote will be by ballot. Tellers will distribute ballots."',
                '**Tellers distribute ballots:** One blank ballot to each eligible voter',
                '**Members vote:** Mark ballots privately, fold to conceal vote',
                ('**Tellers collect:** Pass around ballot box or container', [
                    "Members drop folded ballots in themselves (tellers don't touch)",
                    'Ensures nobody can identify who voted how',
                ]),
                '**Chair verifies quorum still present** before tellers leave to count',
                '**Tellers retire to count:** Leave assembly to count in private',
                "**Tellers report:** Return and tellers' chairman reads report aloud",
                "**Chair announces result:** Declares outcome based on tellers' report",
            ),
        ]),
        heading('Counting Ballots'),
        box('neutral', 'Counting Process:', [
            steps(
                'Count total number of ballots collected',
                'Separate illegal ballots (see below)',
                'Count votes for each candidate/choice',
                'Count blank ballots separately',
                'Verify totals add up correctly',
                "All tellers sign the tellers' report",
            ),
        ]),
        box('danger', 'Illegal Ballots (Not Counted):', [
            bullets(
                '**Identifying marks:** Ballots with names, initials, or unique markings',
                '**Unclear intent:** Ballots that cannot be deciphered',
                '**Over-votes:** More votes than allowed (e.g., voting for 2 when only 1 allowed)',
                "**Write-ins (if prohibited):** When bylaws don't allow write-in candidates",
            ),
            note('Illegal ballots are not counted toward any candidate but are included in "ballots cast" total.'),
        ]),
        box('accent', 'Blank Ballots:', [
            para('A blank ballot (left completely unmarked) is legal but counts as abstention. It is counted in '
                 '"ballots cast" but not counted for any candidate.'),
        ]),
        heading("Tellers' Report"),
        box('neutral', 'Standard Report Format:', [
            sample(
                "TELLERS' REPORT",
                'Number of votes cast: 45',
                'Necessary for election (majority): 23',
                'Illegal ballots: 2',
                'Blank ballots: 3',
                'Votes received:',
                'Candidate A: 28',
                'Candidate B: 12',
                'Candidate C: 2',
                'Candidate A, having received a majority, is elected.',
                "Signed: [Tellers' names]",
            ),
        ]),
        heading('Announcing Results'),
        box('success', "Chair's Announcement (Result Decided):", [
            para('"The tellers report that Candidate A has received 28 votes, a majority, and is therefore elected '
                 'President. The next item of business is..."'),
        ]),
        box('warning', "Chair's Announcement (No Majority):", [
            para('"The tellers report that no candidate has received a majority. We will proceed to a second ballot."'),
            note('Assembly may vote to eliminate lowest candidate or continue with all candidates.'),
        ]),
        heading('Recounts and Challenges'),
        box('warning', 'When can ballots be recounted?', [
            bullets(
                "**Before announcement:** Tellers may recount on their own if numbers don't add up",
                ('**After announcement:** Member may move to recount if they believe error occurred', [
                    'Requires second and majority vote',
                    'Should occur immediately after announcement',
                ]),
                '**Ballots are preserved:** Keep ballots until challenge period passes (usually next meeting)',
            ),
        ]),
        heading('Disposing of Ballots'),
        bullets(
            'Tellers retain ballots until next meeting (in case of challenge)',
            'After challenge period, destroy ballots to maintain secrecy',
            'Never identify how specific members voted',
            "Tellers' report becomes part of permanent minutes",
        ),
    ]),
    section('unanimous-consent', 'Unanimous Consent (General Consent)', [
        para('Unanimous consent is a time-saving procedure that allows the assembly to approve routine or '
             'non-controversial matters without taking a formal vote. This is one of the most frequently used '
             'procedures in actual practice.'),
        box('success', 'Why Use Unanimous Consent?', [
            bullets(
                '**Saves time:** Avoids lengthy formal voting procedures for routine matters',
                '**Increases efficiency:** Meetings move faster when non-controversial items are handled quickly',
                '**Common practice:** Used for approving minutes, simple requests, routine approvals',
                '**Still democratic:** Any single member can object and force a formal vote',
            ),
        ]),
        heading('How Unanimous Consent Works'),
        box('neutral', 'Basic Procedure:', [
            steps(
                '**Chair states the action:** "If there is no objection, [state the action to be taken]."',
                '**Chair pauses:** Brief pause to allow objections',
                '**If no objection:** Chair announces: "Hearing no objection, it is so ordered." (Action is approved)',
                ('**If anyone objects:** Chair states: "Objection is heard. The question is on [restate action]. '
                 'Those in favor say \'aye\'..." (Proceed with normal vote)'),
            ),
        ]),
        heading('Common Uses'),
        box('accent', 'Typical Applications:', [
            bullets(
                'Approving meeting minutes',
                'Accepting committee reports (informational)',
                'Allowing a member to speak when not entitled (out of turn)',
                'Granting brief recess',
                'Dispensing with reading of documents',
                'Fixing time for next meeting',
                'Allowing withdrawal of a motion',
            ),
        ]),
        box('accent', 'NOT Appropriate For:', [
            bullets(
                'Controversial decisions',
                'Elections',
                'Anything requiring specific vote (bylaws amendments)',
                'Matters where opposition is likely',
                'Final budgets or major expenditures',
            ),
        ]),
        heading('Example Scenarios'),
        box('neutral', 'Example 1: Approving Minutes', [
            para('**Chair:** "The minutes of the last meeting have been distributed. If there is no objection, the '
                 'minutes will be approved as written."'),
            para('[Pause 2-3 seconds]'),
            para('**Chair:** "Hearing no objection, the minutes are approved."'),
        ]),
        box('neutral', 'Example 2: Taking a Recess', [
            para('**Chair:** "If there is no objection, we will take a 10-minute recess."'),
            para('[Pause]'),
            para('**Chair:** "There being no objection, we are in recess for 10 minutes."'),
        ]),
        box('neutral', 'Example 3: With Objection', [
            para('**Chair:** "If there is no objection, the chapter will purchase new furniture for $500."'),
            para('**Member:** "I object!"'),
            para('**Chair:** "Objection is heard. The question is on whether to purchase furniture for $500. Those '
                 'in favor, say \'aye\'..."'),
            para('[Proceeds with formal vote]'),
        ]),
        heading('Requesting Unanimous Consent'),
        box('warning', 'Members can also request unanimous consent:', [
            para('**Member:** "I ask unanimous consent to [state request]."'),
            para('**Chair:** "Is there any objection?" [Pause] "Hearing none, it is so ordered."'),
            para('**Example:** "I ask unanimous consent to withdraw my motion."'),
        ]),
        heading('Important Points'),
        box('warning', None, [
            bullets(
                ('**One objection is enough:** Even if only one member objects, the matter must be put to a formal '
                 'vote'),
                '**No explanation needed:** A member may object without stating reasons',
                '**Cannot be debated:** No debate on whether to grant unanimous consent',
                "**Chair's discretion:** Chair judges whether a matter is appropriate for unanimous consent",
                '**Assumed consent:** Silence equals agreement; objection must be voiced',
            ),
        ]),
        heading('Benefits in Practice'),
        box('success', None, [
            para('Using unanimous consent appropriately can dramatically improve meeting efficiency:'),
            bullets(
                'Routine approvals that would take 2-3 minutes of formal voting can be handled in 10 seconds',
                'Keeps meetings focused on substantive issues that require discussion',
                'Demonstrates trust and cooperation among members',
                'Still preserves democratic rights (any member can object and force a vote)',
            ),
            note('Best practice: Use unanimous consent for routine matters, but always take formal votes on '
                 'controversial or important decisions.'),
        ]),
    ]),
    section('debate-rules', 'Debate Rules', [
        heading('Basic Principles of Debate'),
        bullets(
            '**Recognition Required:** Members must be recognized by the chair before speaking.',
            '**Speak to the Motion:** Remarks must be germane (relevant) to the pending question.',
            '**Address the Chair:** Always address comments through the chair, not to other members.',
            '**Avoid Personalities:** Debate the merits of the motion, not the personalities of members.',
            '**Speak Only Twice:** No member may speak more than twice on the same question on the same day.',
            '**Time Limits:** Speeches limited to 10 minutes unless extended by assembly.',
        ),
        heading('Obtaining the Floor'),
        box('neutral', None, [
            steps(
                'Rise and address the chair ("Mr./Madam President" or "Mr./Madam Chairman")',
                'Wait to be recognized by the chair',
                'When recognized, state your purpose or make your motion',
            ),
        ]),
        heading('Who Gets Priority to Speak?'),
        steps(
            "The mover of the motion (if they haven't spoken)",
            "Member who hasn't spoken on the question",
            'Member who wishes to speak on opposite side from previous speaker',
            'Member who has already spoken',
        ),
        box('warning', 'Improper Debate Tactics', [
            bullets(
                "Attacking a member's motives",
                'Making personal references to other members',
                'Speaking on irrelevant matters',
                'Disturbing the assembly',
                'Reading from papers without permission',
            ),
        ]),
    ]),
    section('chair-duties', "Chair's Duties", [
        heading('Primary Responsibilities'),
        bullets(
            '**Open the Meeting:** Call meeting to order when quorum is present.',
            '**Announce Business:** State business in proper order as it comes before assembly.',
            '**Recognize Members:** Assign the floor to members entitled to speak.',
            '**State Questions:** Clearly state each motion before the assembly.',
            '**Moderate Debate:** Enforce debate rules and maintain order.',
            '**Put Questions to Vote:** Take the vote and announce the result.',
            '**Protect Rights:** Ensure the rights of all members are respected.',
            '**Expedite Business:** Keep meetings moving efficiently.',
        ),
        heading('Handling Motions (Template)'),
        box('accent', 'When a motion is made:', [
            steps(
                '**Member:** "I move that..." (make motion)',
                '**Another Member:** "I second the motion"',
                '**Chair:** "It has been moved and seconded that... [repeat motion]. Is there any discussion?"',
                '**[Allow debate]**',
                ('**Chair:** "Is there any further discussion? [pause] Hearing none, all those in favor of [motion],'
                 ' say \'aye.\'"'),
                '**Members:** "Aye"',
                '**Chair:** "Those opposed, say \'no.\'"',
                '**Members:** "No"',
                ('**Chair:** "The ayes have it, and the motion is adopted." OR "The noes have it, and the motion is '
                 'lost."'),
            ),
        ]),
        heading("Chair's Impartiality"),
        box('neutral', None, [
            para('The presiding officer must be impartial and should:'),
            bullets(
                'Not enter into debate while presiding',
                'Relinquish the chair if they wish to speak on a question',
                'Remain fair and neutral in enforcing rules',
                'Protect the rights of the minority while recognizing majority rule',
            ),
        ]),
        heading('When No Motion Needs a Second'),
        para('These items do not require a second:'),
        bullets(
            'Committee reports',
            'Nominations',
            'Point of order',
            'Parliamentary inquiry',
            'Question of privilege',
            'Call for orders of the day',
            'Object to consideration (before debate)',
            'Division of the assembly (call for standing vote)',
        ),
    ]),
    section('committees', 'Committee Procedures', [
        para('Committees are smaller groups formed to consider matters in greater detail than would be practical in '
             'the full assembly.'),
        heading('Types of Committees'),
        box('accent', 'Standing Committees', [
            para('Permanent committees established in the bylaws with ongoing responsibilities.'),
            note('**Examples:** Finance Committee, Kai Committee, Recruitment Committee'),
            note('**Duration:** Continues indefinitely until bylaws are changed'),
            note('**Authority:** Defined by bylaws; typically has ongoing jurisdiction over specific area'),
        ]),
        box('success', 'Special (Ad Hoc) Committees', [
            para('Temporary committees created for a specific purpose. They cease to exist when their task is '
                 'completed and they report to the assembly.'),
            note('**Examples:** Event planning committee, bylaws revision committee, investigation committee'),
            note('**Duration:** Exists only until task is complete or deadline expires'),
            note('**Authority:** Limited to specific charge given by assembly'),
            box('success', 'Creating an Ad Hoc Committee:', [
                steps(
                    'Motion made: "I move that a committee of 5 be appointed to plan our spring formal"',
                    'Specify: Size, purpose, deadline, and member selection method',
                    'After committee completes task and reports, it is automatically dissolved',
                ),
            ]),
            label('Common Uses for Ad Hoc Committees:'),
            bullets(
                'Investigate specific issues or complaints',
                'Plan one-time events (formals, retreats, alumni weekends)',
                'Draft proposed amendments or policy changes',
                'Research options and make recommendations',
                'Handle temporary situations not covered by standing committees',
            ),
        ]),
        box('accent', 'Committee of the Whole', [
            para('The entire assembly acting as one committee. Useful for informal discussion of complex matters.'),
            note('**Purpose:** Allows more relaxed rules for thorough discussion before formal consideration'),
            note('**When to Use:** Complex legislation needing extensive debate, or when assembly wants to discuss '
                 'freely without formal constraints'),
        ]),
        box('chapter', 'Alpha Mu Standing Committees', [
            para('Our chapter maintains 11 standing committees as defined in Constitution Article V.'),
        ], link=('Constitution Article V', 'const-committees')),
        heading('Creating a Committee'),
        box('neutral', None, [
            para('**Motion to Create:** "I move that a committee of [number] be appointed to [state purpose]"'),
            note('The motion should specify: committee size, purpose, reporting deadline (if applicable), and how '
                 'members are selected.'),
        ]),
        heading('Committee Member Selection Methods'),
        bullets(
            '**Appointed by the Chair:** President/Chair selects members',
            '**Nominated by Chair, Approved by Assembly:** Chair nominates, assembly votes',
            '**Elected by Assembly:** Members nominated and elected by the body',
            '**Volunteers:** Members volunteer and are accepted by chair/assembly',
        ),
        heading('Committee Meetings'),
        bullets(
            'Committee chair (or first-named member) calls and conducts meetings',
            'Quorum is a majority of committee members unless otherwise specified',
            'Rules of debate are more relaxed - members may speak multiple times without limit',
            "Motions don't require a second in small committees (12 or fewer members)",
            'Chair may participate in debate and make motions',
        ),
        heading('Committee Reports'),
        box('accent', 'Report Format:', [
            steps(
                'Identification of committee',
                'Statement of task assigned',
                'Description of how committee performed its work',
                'Findings and recommendations',
                "Committee's recommended action (if any)",
                'Signatures (at minimum, committee chair)',
            ),
        ]),
        note('**Presenting Reports:** Committee chair (or designee) rises and says "Madam/Mr. President, the '
             'committee on [subject] submits the following report..."'),
        heading('Receiving Committee Reports'),
        bullets(
            '**"Receive" the report:** Simply acknowledges receipt, does not adopt recommendations',
            ('**"Accept" or "Adopt" the report:** Endorses the committee\'s recommendations and makes them the '
             "assembly's action"),
            'Assembly may refer report back to committee for further work',
            'Assembly may amend committee recommendations before adopting',
        ),
    ]),
    section('nominations-elections', 'Nominations and Elections', [
        para('Elections are how an organization chooses its officers and other leaders. Nominations are the formal '
             'process of proposing candidates.'),
        heading('Methods of Nomination'),
        box('neutral', '1. Nominations from the Floor (Open Nominations)', [
            label('How it works:'),
            bullets(
                'Chair says: "Nominations are now in order for [office]"',
                'Members say: "I nominate [name]"',
                'No second required for nominations',
                'Nominee need not be present (but should be willing to serve)',
                'Chair says: "Are there any further nominations?" (repeated until no response)',
                'Chair: "Hearing none, nominations are closed" OR member moves to close nominations',
            ),
        ]),
        box('neutral', '2. Nominating Committee', [
            para('Committee presents slate of candidates. Floor nominations typically still permitted afterward.'),
            para('**Committee report:** "The nominating committee places in nomination for [office]: [name(s)]"'),
        ]),
        box('neutral', '3. Nominations by Ballot', [
            para('Members write nominees on paper ballots. Tellers compile list of all nominees receiving votes.'),
        ]),
        box('neutral', '4. Nominations by Mail/Email', [
            para('Members submit nominations in writing before the meeting. All valid nominations are presented at '
                 'the meeting.'),
        ]),
        heading('Closing Nominations'),
        box('warning', None, [
            para('**Motion:** "I move that nominations be closed" or "I move that nominations cease"'),
            bullets(
                'Requires second: Yes',
                'Debatable: No',
                'Amendable: No',
                'Vote required: Two-thirds (prevents suppression of minority nominations)',
            ),
            para('**Note:** Chair cannot close nominations on their own. If no further nominations are offered after'
                 ' asking 2-3 times, chair may declare nominations closed.'),
        ]),
        heading('Election Methods'),
        box('accent', 'Ballot Vote', [
            para('Most formal method. Ensures secrecy. Members write choice on paper ballot.'),
        ]),
        box('success', 'Voice Vote (Viva Voce)', [
            para("Members call out candidate's name. Used when only one nominee or election is uncontested."),
        ]),
        box('accent', 'Roll Call', [
            para("Secretary calls each member's name; each states their vote aloud. Creates public record of each "
                 "person's vote."),
        ]),
        box('danger', 'Acclamation (Unanimous Consent)', [
            para('When only one nominee, may elect "by acclamation." Requires unanimous consent.'),
        ]),
        heading('Vote Required to Elect'),
        box('accent', None, [
            para('**Majority Vote:** More than half of votes cast (blank ballots not counted)'),
            para('**Plurality:** Candidate with most votes wins (even if less than majority). Only valid if bylaws '
                 'specifically authorize.'),
            note('If no candidate receives majority, balloting continues. Chair may declare lowest candidate '
                 'eliminated, or assembly may vote to do so.'),
        ]),
        heading('Handling Ties'),
        bullets(
            'If tie occurs on first ballot, vote again',
            ('If tie persists, assembly may:', [
                'Continue balloting',
                'Re-open nominations',
                'Refer to committee to investigate candidates',
            ]),
            'Chair may vote to break (or create) a tie if voting by ballot',
        ),
    ]),
    section('minutes-records', 'Minutes and Record-Keeping', [
        para("Minutes are the official record of an organization's meetings. They provide an objective account of "
             'what was done, not what was said.'),
        heading('What Minutes Should Include'),
        box('accent', None, [
            steps(
                ('**Opening Information:**', [
                    'Type of meeting (regular, special, emergency)',
                    'Name of organization',
                    'Date, time, and place of meeting',
                    'Name of presiding officer and secretary',
                    'Whether quorum was present',
                ]),
                ('**Approval of Previous Minutes:** Note if minutes were read and approved, or approved with '
                 'corrections'),
                '**Reports:** Brief summary of each report given (full reports may be attached as exhibits)',
                ('**All Main Motions:**', [
                    'Exact wording of each motion',
                    'Name of mover (seconder not required in minutes)',
                    'Whether motion passed, failed, or was postponed',
                    'Vote count if taken (or "by unanimous consent")',
                ]),
                '**Points of Order and Appeals:** Record the ruling and outcome',
                '**Closing:** Time of adjournment',
                '**Signature:** Secretary (Admin) signs minutes',
            ),
        ]),
        heading('What Minutes Should NOT Include'),
        bullets(
            'Opinions or commentary from the secretary',
            'Details of discussion or debate (unless specifically ordered)',
            'Motions that were withdrawn before being stated by chair',
            'Personal remarks or attacks',
            'Name of seconder (optional, but not required)',
        ),
        heading('Sample Minute Entry'),
        sample(
            'MINUTES OF THE REGULAR MEETING',
            'Alpha Mu Chapter of Beta Theta Pi',
            'November 15, 2025, 6:00 PM, Chapter Room',
            'President John Smith presiding. Secretary Michael Johnson recording. Quorum present (45 of 60 members).',
            ('APPROVAL OF MINUTES: The minutes of the November 8, 2025 meeting were approved as corrected '
             '(correction: date of Spring Formal changed to April 12).'),
            "TREASURER'S REPORT: Treasurer David Williams reported a balance of $15,430.25. Report filed.",
            'OLD BUSINESS:',
            ('The motion to purchase new sound equipment for $2,500, postponed from the previous meeting, was taken '
             'up. After discussion, the motion passed (35 yes, 8 no, 2 abstain).'),
            'NEW BUSINESS:',
            ('Robert Chen moved "that the chapter host a campus-wide charity basketball tournament on March 15, '
             '2026, with proceeds going to the local food bank." Motion passed unanimously.'),
            'Meeting adjourned at 7:45 PM.',
            'Michael Johnson, Secretary',
        ),
        heading('Approving Minutes'),
        bullets(
            'Minutes are normally approved at the next meeting',
            'Chair asks: "Are there any corrections to the minutes?"',
            'Members may suggest corrections (no motion needed)',
            ('Chair asks: "Are there any further corrections? ... Hearing none, the minutes are approved as read" '
             '(or "as corrected")'),
            'Approval can be done by unanimous consent (no vote needed unless objection)',
        ),
        heading('Correcting Minutes After Approval'),
        para('If error is discovered after minutes are approved:'),
        box('warning', None, [
            para('**Motion:** "I move to amend the minutes of [date] by [state correction]"'),
            para("Requires majority vote to adopt. Correction noted in current meeting's minutes."),
        ]),
    ]),
    section('executive-sessions', 'Executive Sessions (Closed Meetings)', [
        para('An executive session is a meeting, or portion of a meeting, held in secret with limited attendance for'
             ' discussion of sensitive matters.'),
        box('chapter', 'Alpha Mu Chapter Practice', [
            para('Our Constitution Article IV, Section 4 states: "Meetings are closed to the public unless there are'
                 ' specific matters that permit non-members or non-pledges to attend."'),
            bullets(
                'Most chapter meetings are already "closed" (members-only)',
                'Executive sessions go further by excluding certain members or records',
                'Common uses: Kai Committee hearings, personnel matters, sensitive discussions',
            ),
        ], link=('Constitution Article IV, Section 4', 'const-meetings')),
        heading('When to Use Executive Session'),
        box('neutral', 'Appropriate Topics:', [
            bullets(
                '**Disciplinary matters:** Kai Committee cases, member conduct issues',
                '**Personnel discussions:** Officer performance, hiring/firing',
                '**Legal matters:** Potential lawsuits, legal advice from counsel',
                '**Financial sensitivity:** Individual member accounts, confidential finances',
                '**Membership candidates:** Discussion of individuals seeking membership',
                '**Negotiations:** Contract negotiations where publicity would harm position',
            ),
        ]),
        box('danger', 'Do NOT Use Executive Session For:', [
            bullets(
                'Avoiding transparency or accountability',
                'Hiding mistakes or mismanagement',
                'Making routine decisions',
                'Excluding members to prevent opposition',
            ),
            note("Executive sessions should be rare and used only when secrecy truly serves the organization's "
                 'interests.'),
        ]),
        heading('How to Enter Executive Session'),
        box('warning', 'Motion and Procedure:', [
            steps(
                (('**Member moves:** "I move that we go into executive session to discuss [state general subject '
                  'without details]"'), [
                    'State reason (e.g., "personnel matter," "disciplinary case")',
                    "Don't reveal details publicly",
                ]),
                '**Requires second:** Yes',
                '**Debatable:** No (but chair may briefly explain need)',
                '**Vote required:** Majority',
                '**Who must leave:** Motion should specify (e.g., "all non-members," "all except officers")',
            ),
        ]),
        heading('Who May Attend Executive Session'),
        box('neutral', 'Typically Included:', [
            bullets(
                'Members in good standing (unless motion specifies otherwise)',
                'Officers and necessary staff',
                'Advisors (if authorized by bylaws or motion)',
                'Legal counsel (when needed)',
                'Specific individuals invited for their expertise or involvement',
            ),
        ]),
        box('warning', 'Usually Excluded:', [
            bullets(
                'Non-members, guests, and observers',
                'Press and media',
                'Members with direct conflict of interest in the matter',
                'Individuals who are the subject of the discussion (may be invited for part of session)',
            ),
        ]),
        heading('Conducting Executive Session'),
        box('accent', 'During Executive Session:', [
            bullets(
                '**Confidentiality rule:** All attendees pledge not to discuss proceedings outside the room',
                "**Normal rules apply:** Robert's Rules still govern procedure",
                '**Minutes:** Secretary may take minimal minutes or no minutes (depending on sensitivity)',
                '**Actions recorded:** Any votes or decisions must be recorded, even if details are not',
                '**Only stated business:** Discuss only the matter specified in the motion',
            ),
        ]),
        heading('Leaving Executive Session'),
        box('success', 'To Return to Regular Session:', [
            steps(
                'Member moves: "I move that we rise from executive session"',
                'Requires second and majority vote',
                'Chair declares: "We are no longer in executive session"',
                'Those who were excluded may return',
                'Regular business resumes',
            ),
        ]),
        heading('Minutes of Executive Session'),
        box('accent', 'Recording Executive Sessions:', [
            bullets(
                '**Separate minutes:** Executive session minutes kept separate from regular minutes',
                '**Limited distribution:** Only shown to authorized members (e.g., officers, board)',
                ('**Regular minutes note:** "Meeting went into executive session at 7:30pm to discuss a personnel '
                 'matter. Executive session ended at 8:00pm."'),
                '**Actions reported:** Decisions/votes may be reported in regular minutes without details',
            ),
        ]),
        box('neutral', 'Example (Regular Minutes):', [
            sample(
                ('"At 7:30pm, the chapter voted to enter executive session to discuss a disciplinary matter. '
                 'Non-members were excused. Executive session concluded at 8:15pm. The chapter voted to impose '
                 'sanctions as recommended by the Kai Committee. Details are recorded in confidential executive '
                 'session minutes."'),
            ),
        ]),
        heading('Confidentiality and Ethics'),
        box('warning', 'All Attendees Must:', [
            bullets(
                'Maintain strict confidentiality about what was discussed',
                'Not disclose details to those who were not present',
                'Understand that breach of confidentiality may result in sanctions',
                'Remember that executive session is a privilege, not a tool for gossip',
            ),
        ]),
    ]),
    section('amending-bylaws', 'Amending the Constitution and Bylaws', [
        para('Amending governing documents (constitution, bylaws) is different from amending ordinary motions. These'
             " procedures protect the organization's fundamental rules from hasty changes."),
        box('chapter', 'Alpha Mu Chapter Requirements', [
            para('Our Constitution and Bylaws establish specific amendment procedures:'),
            box('neutral', 'Constitution Amendments (Article VI):', [
                steps(
                    'Submit in writing to Executive Board for approval',
                    'Requires **supermajority (2/3)** of Executive Board',
                    'If Board rejects, member may bring to chapter meeting',
                    'Chapter vote requires **2/3 supermajority** with quorum present',
                    'Failed proposals cannot be reintroduced for **15 chapter meetings**',
                ),
            ]),
            box('neutral', 'Bylaws Amendments (Article X):', [
                steps(
                    'Submit in writing to Executive Board for approval',
                    'Requires **simple majority** of Executive Board',
                    'If Board rejects, member may bring to chapter meeting',
                    'Chapter vote requires **simple majority (51%)** with quorum present',
                    'Failed proposals cannot be reintroduced for **10 chapter meetings**',
                    '"Protected Status" prevents similar amendments during protection period',
                ),
            ]),
            note('Both are subordinate to the Code of Beta Theta Pi, IFC, Samford University, and applicable laws.'),
        ], link=('Constitution and Bylaws', '')),
        heading('General Principles for Amending Bylaws'),
        box('neutral', 'Key Differences from Regular Motions:', [
            bullets(
                '**Higher vote threshold:** Usually 2/3 majority or higher (vs. simple majority)',
                '**Previous notice required:** Members must be notified in advance',
                '**Cannot be reconsidered:** Once adopted or rejected, cannot use motion to reconsider',
                '**Protection periods:** Failed amendments often cannot be quickly reintroduced',
                '**Written submission:** Must be submitted in exact wording',
            ),
        ]),
        heading('Requirement for Previous Notice'),
        box('warning', 'What is Previous Notice?', [
            para('Previous notice means informing members at a previous meeting (or in the meeting call) that an '
                 'amendment will be proposed. This gives members time to consider and prepare.'),
            label('Methods of Giving Notice:'),
            bullets(
                'Announce at previous meeting: "At the next meeting, I will move to amend Article 5..."',
                'Include in meeting call/notice',
                'Distribute proposed amendment text to all members before meeting',
            ),
        ]),
        heading('Vote Requirements'),
        table(['Document Type', 'Vote Required', 'Notice Required?'], [
            ['Constitution (Alpha Mu)', '2/3 supermajority', 'Yes (via Executive Board process)'],
            ['Bylaws (Alpha Mu)', 'Simple majority', 'Yes (via Executive Board process)'],
            ['Bylaws (Standard)', '2/3, OR majority with notice', 'Recommended'],
            ['Standing Rules', 'Simple majority', 'No'],
        ]),
        heading('Procedure for Amending'),
        box('success', 'Standard Process:', [
            steps(
                '**Draft amendment:** Write exact wording of proposed change',
                '**Give previous notice:** Announce at previous meeting or in call',
                '**Introduce at meeting:** "I move to amend Article [X] by [striking/inserting/substituting]..."',
                '**Second required:** Another member seconds',
                '**Debate:** Open to full debate (members discuss merits)',
                '**May be amended:** The proposed amendment itself can be amended',
                '**Vote:** Take vote using required threshold',
                '**If adopted:** Amendment takes effect immediately (unless specified otherwise)',
            ),
        ]),
        heading('Amending the Amendment'),
        box('accent', None, [
            para('Even proposed amendments to bylaws can be amended during the meeting:'),
            bullets(
                'Member proposes change to the proposed amendment',
                'Vote on the amendment to the amendment first',
                'Then vote on the (possibly modified) main amendment',
                'This requires notice only for the original amendment, not the changes',
            ),
            note('Exception: Cannot completely change the scope if notice was given for specific content.'),
        ]),
        heading('Special Considerations'),
        box('accent', 'Subordination:', [
            para("Amendments must not conflict with superior governing documents (parent organization's rules, state"
                 ' law, federal law). Such amendments are null and void even if adopted.'),
        ]),
        box('success', 'Effective Date:', [
            para('Unless the amendment specifies otherwise, changes take effect immediately upon adoption. May '
                 'specify: "This amendment takes effect at the start of the next academic year."'),
        ]),
        box('warning', 'Revision vs. Amendment:', [
            para('**Revision:** Complete rewrite of bylaws. **Amendment:** Changes to specific sections. Revision '
                 'requires even more stringent notice and vote requirements.'),
        ]),
        heading('Example Amendment Process'),
        box('neutral', 'Scenario: Changing GPA Requirement', [
            para('**Current Bylaws:** "Officers must maintain a 2.5 GPA"'),
            steps(
                ('**Previous Meeting:** Member announces: "At the next meeting, I will move to amend the bylaws to '
                 'change the officer GPA requirement from 2.5 to 3.0."'),
                ('**Current Meeting:** Member moves: "I move to amend Article VII, Section 2 by striking \'2.5\' and '
                 'inserting \'3.0\' so that it reads \'Officers must maintain a 3.0 GPA.\'"'),
                '**Second:** Another member seconds',
                '**Debate:** Members discuss pros and cons',
                '**Amendment:** Member might move to change to "2.75" instead of "3.0"',
                '**Vote:** Vote using required threshold (2/3 or majority depending on bylaws)',
                '**Result:** If passed, new GPA requirement takes effect immediately',
            ),
        ]),
    ]),
    section('common-situations', 'Common Parliamentary Situations', [
        para('Practical guidance for handling frequently encountered scenarios in meetings.'),
        heading('Someone Makes a Motion While Another is Pending'),
        box('neutral', None, [
            para('**Chair\'s Response:** "There is already a motion on the floor. We must dispose of it before '
                 'considering another main motion."'),
            note("Alternative: The new motion may be appropriate if it's a subsidiary, privileged, or incidental "
                 'motion that takes precedence.'),
        ]),
        heading('Member Speaks Without Being Recognized'),
        box('neutral', None, [
            para('**Chair\'s Response:** "The member will please be seated. You have not been recognized to speak."'),
            note('Chair should then recognize members in the order they sought recognition, alternating between '
                 'opposing viewpoints when possible.'),
        ]),
        heading('Motion Has No Second'),
        box('neutral', None, [
            para('**Chair\'s Response:** "Is there a second?" [pause] "Hearing no second, the motion is not before '
                 'the assembly."'),
            note('The motion dies for lack of a second. Chair moves to next item of business.'),
        ]),
        heading('Member Calls "Question!" to End Debate'),
        box('neutral', None, [
            para('**Proper Procedure:** Member must be recognized and formally move "previous question"'),
            para('**Chair\'s Response to "Question!":** "The member is calling for the question. Is there a second to'
                 ' move the previous question?"'),
            note('Previous question requires 2/3 vote. If it passes, take immediate vote on pending motion. If it '
                 'fails, debate continues.'),
        ]),
        heading("Confusion About What's Being Voted On"),
        box('neutral', None, [
            para("**Member's Right:** Any member may request clarification before vote"),
            para('**Member says:** "Parliamentary inquiry. What is the exact motion before the assembly?"'),
            note('Chair must clearly restate the motion before calling for vote.'),
        ]),
        heading('Vote Seems Close or Unclear'),
        box('neutral', None, [
            para('**Member\'s Option:** Call for "Division of the Assembly"'),
            para('**Member says:** "Division!" or "I call for a division"'),
            note('No second required. Chair must take a standing vote (or other countable method). Does not require '
                 'a revote, only a verifiable count of the same vote.'),
        ]),
        heading('Member Wants to Change Their Vote'),
        box('neutral', None, [
            para('**Before Result Announced:** Member has right to change vote'),
            para('**After Result Announced:** Requires unanimous consent of assembly'),
            note('On ballot votes, changes not possible after ballot is submitted to tellers.'),
        ]),
        heading("Member Disagrees with Chair's Ruling"),
        box('neutral', None, [
            para("**Member's Right:** Appeal the decision to the assembly"),
            para('**Member says:** "I appeal from the decision of the chair"'),
            para('**Requires:** Second required. Is debatable. Chair may explain ruling. Majority vote sustains or '
                 'overrules chair.'),
            note('Chair says: "The decision of the chair is appealed. Those in favor of sustaining the chair\'s '
                 'decision say aye... Those opposed say no..."'),
        ]),
        heading('Member Brings Up Unrelated Topic During Debate'),
        box('neutral', None, [
            para('**Point of Order:** Any member may raise "Point of order!"'),
            para('**Member says:** "Point of order, Mr./Madam President. The speaker is not confining remarks to the'
                 ' pending question."'),
            note('Chair rules on the point and directs speaker to stay on topic or yields floor to another member.'),
        ]),
        heading('Urgent Matter Arises During Meeting'),
        box('neutral', None, [
            para('**Question of Privilege:** Member may interrupt for urgent matter affecting assembly'),
            para('**Member says:** "I rise to a question of privilege affecting the assembly"'),
            note("Examples: Room too hot/cold, can't hear speaker, emergency situation, assault on integrity of "
                 "assembly. Chair determines if it's truly privileged and handles accordingly."),
        ]),
    ]),
    section('quick-reference', 'Quick Reference Table', [
        quick_reference(),
        note('* Debatable if main motion is debatable'),
    ]),
]




def content():
    """The whole reference, as the app's JSON."""
    return {
        'categories': [{'key': k, 'label': lbl, 'summary': summary} for k, lbl, summary in CATEGORIES],
        'motions': MOTIONS,
        'sections': SECTIONS,
    }
