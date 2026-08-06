"""
Constitution & Bylaws seed data for Alpha Mu Chapter of Beta Theta Pi.
Sourced from: Constitution_and_Bylaws_of_the_Samford_Chapter_Jan_26_Approved_Document.pdf
Last amended: January 26, 2025.
"""

DOCUMENTS = [
    {
        # ── FOREWORD (v3.19.1) ───────────────────────────────────────────────
        #
        # ⚠️ THIS TEXT IS NOT YET IN FORCE. It is the foreword of the NEW
        # Constitution and Bylaws (`exportable_media/legislation_docs/New-Bylaws.docx`,
        # authored 8/6/26), staged here ahead of the chapter vote so that
        # passing it is a flag toggle rather than a deploy.
        #
        # It is invisible to members until the `cnb_foreword` feature flag is
        # switched on at /admin-v2/. That flag seeds DISABLED and is listed in
        # `FeatureFlag.DISABLED_BY_DEFAULT`, so even an install that never runs
        # `seed_feature_flags` reads it as off — Python flag lookups otherwise
        # fail OPEN, which would publish unpassed governance.
        #
        # STRUCTURE: prose only. Unlike every other document here it has NO
        # articles — the whole text lives in `preamble`, because a foreword is
        # continuous prose with a dedication and a signature, not numbered
        # sections. `GoverningDocument.is_prose_only` derives that from the
        # absence of articles, and the viewer labels the block "Foreword"
        # rather than "Preamble" on the strength of it.
        #
        # CONSEQUENCE WORTH KNOWING: having no Sections means the foreword
        # CANNOT be amended through the C&B resolution flow, which targets
        # Section rows. That is intended — a foreword is the author's note, not
        # legislation — but if it ever needs to be amendable it has to be
        # restructured into articles/sections first.
        'doc_type': 'foreword',
        'title': 'Foreword',
        'display_order': 0,
        'amendment_protection_weeks': 0,
        'preamble': (
            'This document is one of many that was built by brothers whose goal was to plant trees '
            'for future Betas to enjoy their shade. This document is the structure of our governance; '
            'it has been put together with a great amount of collaboration with the chapter. This is a '
            'collection of what we as a chapter found to best benefit us in situations we have '
            'encountered. While this document is binding upon every member of the chapter, I hope that '
            'it does not become overbearing. This document is not meant to be a burden or a bat to beat '
            'someone with — it is meant to give guidance from past members for future situations.\n\n'

            'The inspiration for this document was largely the chaos we encountered of having no idea '
            'what to do in situation after situation. By setting clear paths for resolution in a given '
            'situation, my hope is that you may be able to avoid some of the pitfalls we experienced '
            'prior to this document\'s creation. This document is not perfect, I understand that; there '
            'are clear avenues to amend or repair issues in the document, and indeed numerous changes '
            'and fixes have already been made. Do not feel obligated to leave the work of the past '
            'unchanged if it is broken.\n\n'

            'There are times when the document stresses the importance of following its prescription, '
            'but other times it does not. This is intentional; my suggestion is to look at this and '
            'understand why we may have chosen to write something one way or another. My biggest fear '
            'is this: please, do not let the words of this document be a chain, but do not disregard '
            'the work and mistakes we have made in the past either.\n\n'

            'Our motto is Ἀρετή Μονάζει (Virtue Stands Alone) — we are the only chapter of Beta whose '
            'seal contains an open Bible. “Virtue stands alone” is a reference to our unique standing '
            'as a chapter. We are a chapter of Christian men at a Christian institution, and we have '
            'chosen to stand out in this way to the world because it is what makes us who we are. Do '
            'not lose sight of who we are or who we should be, even when you must stand alone.\n\n'

            'To Ian Viner, Brennan Paulus, Xander Guill, and Jacob Hoffman,\n\n'

            'This document is perhaps our greatest work and test for the future of the chapter. All of '
            'your time, dedication, and work has not been forgotten or unnoticed. I only hope that '
            'future Betas can emulate half the effort each of you put into the building of this '
            'foundational document.\n\n'

            'Mason Kimball (αμ 73), 8/6/26\n'
            'Author, Constitution and Bylaws Chair, Executive Vice President'
        ),
        'articles': [],
    },
    {
        'doc_type': 'constitution',
        'title': 'Constitution of the Samford Chapter, the Alpha Mu of Beta Theta Pi',
        'display_order': 10,
        'amendment_protection_weeks': 15,
        'preamble': (
            'Constitution of the Samford Chapter, the Alpha Mu of Beta Theta Pi.'
        ),
        'articles': [
            {
                'number': 'I',
                'title': 'Name and Purpose',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Name',
                        'content': (
                            'The name of this organization shall be the Samford chapter, the Alpha Mu of Beta Theta Pi.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Purpose',
                        'content': (
                            'The purpose of this chapter is to uphold the values, principles, and standards of Beta Theta Pi as '
                            'outlined in the fraternity\'s Constitution and Code.'
                        ),
                    },
                ],
            },
            {
                'number': 'II',
                'title': 'Membership',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Membership Requirements',
                        'content': (
                            '1. Eligible members must be regularly enrolled male students, either graduate or undergraduate, '
                            'at Samford University.\n'
                            '2. Members must not belong to any similar fraternity or organization.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Membership Categories',
                        'content': (
                            '1. Collegiate Member: As defined in the Code of Beta Theta Pi, Chapter VIII Section 1(B)(a).\n'
                            '2. Active Member: A Collegiate Member who is neither a Temporarily Inactive Member nor a Suspended Member.\n'
                            '3. Temporarily Inactive Member: As defined in the Code of Beta Theta Pi, Chapter VIII Section 1(B)(b).\n'
                            '4. Alumnus Member: As defined in the Code of Beta Theta Pi, Chapter VIII Section 1(B)(c).\n'
                            '5. Suspended Member: As defined in the Code of Beta Theta Pi, Chapter VIII Section 1(B)(d).\n'
                            '6. Recused Member: As defined in the Code of Beta Theta Pi, Chapter VIII Section 1(B)(e).'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Membership Procedures',
                        'content': (
                            '1. All new members must be initiated according to the General Fraternity\'s initiation Ritual and '
                            'the chapter\'s pre-initiation rituals.\n'
                            '2. Membership rights are equal for all members, regardless of race, color, creed, religion, age, '
                            'disability, ethnic orientation, sexual orientation, national origin, or position within the '
                            'fraternity or chapter.'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Membership Records',
                        'content': (
                            'The chapter must maintain an official roll book of all initiated members, which should include '
                            'each member\'s first name, last name, initiation date, roll number, and date of birth.'
                        ),
                    },
                ],
            },
            {
                'number': 'III',
                'title': 'Leadership',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Introduction',
                        'content': (
                            'An Executive Board will serve as leaders and officers. These members are responsible for all '
                            'operational activities and facilitate the development of a positive chapter culture congruent with '
                            'the values of Beta Theta Pi.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Purpose of the Executive Board',
                        'content': (
                            '1. Facilitate the strategic vision and sustainable operation of the chapter.\n'
                            '2. Control financial assets.\n'
                            '3. Report at stated times and intervals on the condition of the chapter.\n'
                            '4. Prepare business resulting in orderly and succinct meetings.\n'
                            '5. Determine policy in advance for approval at chapter meetings.\n'
                            '6. Provide in all other ways possible for the business-like administration of chapter affairs and policies.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Chapter Officers',
                        'content': (
                            '1. All officers of this chapter must be collegiate members, as defined in the Code of Beta Theta Pi, '
                            'Chapter VIII, and must remain in good standing with Samford University, the General Fraternity, and '
                            'the Alpha Mu chapter.\n'
                            '2. The officers shall include:\n'
                            '   a. President\n'
                            '   b. Executive Vice President & IFC Delegate\n'
                            '   c. Vice President of Administration\n'
                            '   d. Vice President of Brotherhood\n'
                            '   e. Vice President of Finance\n'
                            '   f. Vice President of Risk Management\n'
                            '   g. Vice President of Recruitment\n'
                            '   h. Vice President of Education\n'
                            '   i. Vice President of Programming\n'
                            '3. No member of the chapter may hold a title or office without first being elected to that position by the chapter.\n'
                            '   a. Members may not claim membership on any committee unless they either have been elected to the '
                            'committee by the chapter or have accepted an invitation to join the committee from its chair.\n'
                            '   b. Any member who falsely claims a title or position without proper election or appointment, as outlined '
                            'above, must be referred to the Kai Committee by the Constitution and Bylaws Chair or the Executive Vice President.\n'
                            '   c. A member may be appointed to the position of a chair that is currently unfilled; this appointment can '
                            'only be done by the Vice President that is directly superior to the chair. While a member appointed may take '
                            'actions and begin work as the chair, they must be voted upon by the chapter at the next regularly scheduled '
                            'chapter meeting.\n'
                            '   d. If the chapter denies, or votes in the negative to, an appointment the member may be reappointed as a '
                            'nominee to the position; however, they cannot take up any duties of the chair until confirmed at a regular '
                            'chapter meeting, by the chapter.\n'
                            '      i. Failure to follow these procedures are a violation of the constitution and all parties that failed '
                            'to bring the position to be voted upon by the chapter must be sent to the Kai Committee for sanctions.'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Duties of Officers',
                        'content': (
                            '1. The specific duties of all officers can be found in Article VI of the Bylaws of the Samford Chapter, '
                            'the Alpha Mu of Beta Theta Pi.\n'
                            '2. Each Executive Board officer is responsible for all business related to the corresponding operational '
                            'area. The President is responsible for the operation of the Executive Board.'
                        ),
                    },
                    {
                        'number': '5',
                        'title': 'Meetings',
                        'content': (
                            'The Executive Board will meet weekly outside of the regular chapter meeting to conduct business. '
                            'Executive Board meetings are open to all members of the chapter unless otherwise closed by the Executive Board.'
                        ),
                    },
                    {
                        'number': '6',
                        'title': 'Business of the Board',
                        'content': (
                            'All findings and proceedings of the Executive Board shall be reported to the chapter and advisors. '
                            'All legislative actions of the Executive Board are subject to approval by a majority of active members '
                            'present and voting at a regularly scheduled meeting with quorum.'
                        ),
                    },
                    {
                        'number': '7',
                        'title': 'Officer Selection',
                        'content': (
                            'Executive Board officers will be selected in accordance with the Bylaws as outlined in Article II '
                            'of the Bylaws of the Alpha Mu Chapter.'
                        ),
                    },
                ],
            },
            {
                'number': 'IV',
                'title': 'Meetings',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Regular Meetings',
                        'content': (
                            '1. Weekly chapter meetings will be held at a set time to be established no later than the final '
                            'weekly meeting of the proceeding semester.\n'
                            '2. Executive Board meetings shall be scheduled at the discretion of the Executive Board and should '
                            'generally be open to all chapter members unless an officer requests that the meeting be closed.\n'
                            '   a. Should an officer request for an Executive Board meeting to be closed, the request must be made '
                            'prior to the start of the meeting. This request must be approved in a supermajority vote of the members '
                            'present at the Executive Board meeting.\n'
                            '   b. While at times it may prove necessary, it is encouraged that Executive Board meetings only be '
                            'closed temporarily when secrecy is a necessary matter.\n'
                            '   c. Whether a meeting is closed temporarily or for the full duration of the meeting, the closure, '
                            'and for how long the meeting was closed, must be noted in the minutes taken at the Executive Board meeting.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Special Meetings',
                        'content': (
                            '1. Special meetings may be called with four (4) hours advance notice by:\n'
                            '   a. The President\n'
                            '   b. The Executive Board\n'
                            '   c. The advisory team\n'
                            '   d. A supermajority of active, good standing members of the chapter\n'
                            '2. New Business introduced at a chapter meeting may not be voted upon until a subsequent meeting.\n'
                            '   a. If two-thirds of the active members present and eligible to vote deem it necessary, this provision '
                            'may be set aside and the motion considered immediately.\n'
                            '3. Any new business will be presented at the Executive Board meeting for review prior to discussion at '
                            'the Chapter Meeting.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Quorum',
                        'content': (
                            '1. A quorum for chapter meetings shall consist of a simple majority of Active Members.\n'
                            '2. A quorum is required to pass any legislation at any special meetings or chapter meetings.\n'
                            '3. A quorum of officers is not required for any Executive Board meeting vote.'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Meeting Processes',
                        'content': (
                            '1. All meetings are to be governed by Robert\'s Rules of Order.\n'
                            '2. Meetings are closed to the public unless there are specific matters that permit non-members '
                            'or non-pledges to attend.'
                        ),
                    },
                    {
                        'number': '5',
                        'title': 'Voting',
                        'content': (
                            '1. All present Active Members in Good Standing have one vote.\n'
                            '2. The Vice President of Administration will count and record all votes in the minutes.\n'
                            '3. An advisor of the chapter must be present for elections, amendments, Trial by Chapters, '
                            'and selection of potential new members.'
                        ),
                    },
                    {
                        'number': '6',
                        'title': 'Attendance',
                        'content': (
                            '1. Attendance will be taken at chapter meetings by the Vice President of Administration.\n'
                            '2. Absences\n'
                            '   a. Members may not miss more than three chapter meetings in one semester, and not more than '
                            'one without an excuse per semester.\n'
                            '      i. Sanctions are at the discretion of the Vice President of Administration, and the Kai '
                            'Committee must approve of the sanction prior to it taking effect.\n'
                            '   b. If absent, members must submit their reasons for missing the meeting to the Vice President '
                            'of Administration.\n'
                            '      i. The submission must be in writing.\n'
                            '      ii. The submission must be made no later than 24 hours prior to the meeting.\n'
                            '      iii. Should external circumstances arise after the 24 hour prior notice requirement, that '
                            'were not reasonably known to the member, the Vice President of Administration may consider their '
                            'reason to excuse their absence.\n'
                            '   c. The Vice President of Administration will determine whether an absence is excused or unexcused '
                            'and will notify those who are absent that they were recorded as such by the next chapter meeting.\n'
                            '3. Tardiness\n'
                            '   a. Chapter meetings will start at precisely the predetermined time.\n'
                            '   b. Any member arriving more than three minutes after the predetermined time will be marked as "tardy."\n'
                            '   c. Any member arriving more than fifteen (15) minutes into the meeting will be marked "absent."\n'
                            '   d. Two instances of tardiness will constitute one absence for the purpose of Good Standing and Kai referral.\n'
                            '   e. The Vice President of Administration will notify those who are tardy that they were recorded as '
                            'such by the next chapter meeting.'
                        ),
                    },
                ],
            },
            {
                'number': 'V',
                'title': 'Committees',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Committee Outlines',
                        'content': (
                            '1. All committees are responsible for executing tasks related to their specific focus and ensuring '
                            'alignment with chapter goals. Committee members are expected to actively participate, collaborate '
                            'effectively, and fulfill any assigned duties. Chairs of each committee are responsible for leadership, '
                            'organization, and communication within their committees.\n'
                            '2. Officers are responsible for providing guidance, support, and resources to the committees they oversee. '
                            'They must ensure that committees are aligned with the chapter\'s objectives, facilitate communication '
                            'between committee chairs and the executive board, and monitor the progress of committee activities. '
                            'Officers should regularly meet with their committee chairs to address any challenges, provide strategic '
                            'direction, and ensure the successful execution of committee duties.\n'
                            '3. The President is a member of all committees of the chapter. While he enjoys the benefits of membership '
                            'within all committees, he is not a required participant in all committee meetings. His presence and vote '
                            'are only considered by the committee when he is present or if he officially delegates the EVP to act in '
                            'his place on that committee.\n'
                            '   a. For the Kai Committee the President, or his delegate, may only be an active voting member should '
                            'the requirements of Article VII, Section 1 (a) (vi-x) be met.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Ad Hoc Committees',
                        'content': (
                            '1. Ad Hoc Committees can be established by the President or by a majority vote of the chapter to '
                            'address specific issues.\n'
                            '2. If the chapter creates an Ad Hoc Committee, its members can be appointed either by the President '
                            'or through a chapter vote.\n'
                            '3. All actions proposed by an Ad Hoc Committee require initial approval from the Executive Board and '
                            'must then be approved by a majority of Active Members of the chapter where a quorum is present.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Maintained Committees',
                        'content': (
                            '1. The list of currently maintained committees by this chapter are as follows:\n'
                            '   a. Executive Board\n'
                            '   b. Kai Committee\n'
                            '   c. Brotherhood Committee\n'
                            '   d. Recruitment Committee\n'
                            '   e. Education Committee\n'
                            '   f. Risk Management Committee\n'
                            '   g. Finance Committee\n'
                            '   h. Administration Committee\n'
                            '   i. Programming Committee\n'
                            '   j. Ritual Committee\n'
                            '   k. Constitution and Bylaws Committee'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Committee Operations',
                        'content': (
                            '1. No more than 60% of a committee\'s membership, per committee, is to be members of the current '
                            'Executive Board; these positions are to be filled by non-executive board brothers of the chapter. '
                            'The Executive Board is exempt from this requirement.\n'
                            '2. All committee meetings must remain open to all initiated brothers at all times. Exceptions are as follows:\n'
                            '   a. For Kai Committee cases, the accused member must request the meeting be closed for them. Accused '
                            'members must be made known that this is an option for them prior to the trial.\n'
                            '      i. Uninitiated members are not allowed at Kai Committee meetings unless the accused is an uninitiated '
                            'member, and the meeting has not been closed by the accused.\n'
                            '   b. Executive Board meetings can only be closed if a current member of the board requests a vote to do so. '
                            'This vote requires a simple majority of the Executive Board\'s members present to pass.\n'
                            '   c. If the matter requires the secrecy of a brother, the committee meeting can be closed. This secrecy '
                            'being for his wellbeing or general privacy. A vote of a simple majority of committee members must pass to '
                            'close the meeting.\n'
                            '   d. Minutes should be taken at all committee meetings and be posted to a place where the rest of the '
                            'chapter can view them within 48 hours of the end of the meeting. If a committee meeting is closed, the '
                            'reason for closing a committee meeting must be included in the committee\'s publicly posted minutes, '
                            'exclusively to brothers and advisors. If a Kai Committee meeting is closed, then the reason for the close '
                            'is simply "at the request of the accused."'
                        ),
                    },
                    {
                        'number': '5',
                        'title': 'Committee Chairs',
                        'content': (
                            '1. The list of currently maintained committee chairs by this chapter are as follows:\n'
                            '   a. Social Chair\n'
                            '   b. Tabling Chair\n'
                            '   c. Ritual Chair\n'
                            '   d. Historian & Archivist\n'
                            '   e. Health & Wellness Chair\n'
                            '   f. Chaplain\n'
                            '   g. DEI Chair\n'
                            '   h. Chorister\n'
                            '   i. Scholarship Chair\n'
                            '   j. Kai Committee Chair\n'
                            '   k. Marketing & Advertising Chair\n'
                            '   l. Constitution and Bylaws Chair\n'
                            '   m. Sergeant at Arms\n'
                            '   n. Awards Chair\n'
                            '2. A member must be elected by a simple majority of eligible voting members present, at a chapter '
                            'meeting, with a quorum to assume a Committee Chair\'s position.\n'
                            '3. The default term for all Committee Chairs is until the end of the current Executive Board\'s term.\n'
                            '4. Committee Chair members have full voting rights under all committees they have membership in.\n'
                            '5. A Committee Chair member may resign from their position(s) at any time in front of a chapter meeting, '
                            'this resignation taking place during new business. A notice must be given to the Executive Officer '
                            '(Vice President) over the committee chair two (2) weeks prior to the resignation.'
                        ),
                    },
                    {
                        'number': '6',
                        'title': 'Committee Chair Membership',
                        'content': (
                            '1. Committee Chairs, their committee memberships, and VPs they are under:\n'
                            '   a. Social Chair – Social Committee, VP of Risk Management\n'
                            '   b. Tabling Chair – Recruitment and Programming Committees, VP of Recruitment and VP of Programming\n'
                            '   c. Ritual Chair – Education and Brotherhood Committees, VP of Education and VP of Brotherhood\n'
                            '   d. Historian & Archivist – Executive Vice President\n'
                            '   e. Health & Wellness Chair – Programming Committee, VP of Programming\n'
                            '   f. Chaplain – Programming Committee, VP of Programming\n'
                            '   g. DEI Chair – VP of Education\n'
                            '   h. Chorister – VP of Brotherhood\n'
                            '   i. Scholarship Chair – Education Committee, VP of Education\n'
                            '   j. Kai Committee Chair – Kai Committee, VP of Brotherhood\n'
                            '   k. Marketing & Advertising Chair – Admin Committee, VP of Administration\n'
                            '   l. Constitution and Bylaws Chair – Constitution and Bylaws Committee, Executive Vice President\n'
                            '   m. Awards Chair – Executive Vice President\n'
                            '2. Should any of these positions be unoccupied, their duties and responsibilities fall to the VP, '
                            'and/or committee(s), which they are part of until the position is filled.'
                        ),
                    },
                ],
            },
            {
                'number': 'VI',
                'title': 'Amendments to the Constitution',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Proposals',
                        'content': (
                            '1. Proposed amendments to this Constitution must be submitted in writing to the chapter\'s Executive '
                            'Board for initial approval.\n'
                            '   a. This approval from the Executive Board is done by a supermajority vote in favor of the amendment.\n'
                            '2. Should the initial approval from the Executive Board fail, and the member still wishes to pursue '
                            'the amendment, the member can bring a motion to amend at the next chapter meeting.\n'
                            '3. Proposals that fail a chapter vote cannot be reintroduced to the chapter or Executive Board until '
                            '15-chapter meetings pass.\n'
                            '   a. All Articles, Sections, and Subsections included in a failed amendment are granted "Protected '
                            'Status" until the designated time period expires.\n'
                            '   b. During this protected period, no amendments may be proposed or passed that would alter any '
                            'Article, Section, or Subsection under Protected Status.\n'
                            '   c. "Protected Status" is defined as a restriction that prevents changes, including edits, additions, '
                            'or deletions, to the content of the affected Articles, Sections, or Subsections.\n'
                            '   d. The Vice President of Administration is responsible for recording the relevant dates.\n'
                            '   e. The Constitution and Bylaws Chair and the Vice President of Administration are responsible for '
                            'ensuring a proposal does not infringe on anything with a Protected Status.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Approval of Amendments',
                        'content': (
                            'Amendments to the constitution must be approved by a supermajority of the active members present '
                            'at a chapter or special meeting where a quorum is present.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Amendment Authority',
                        'content': (
                            '1. All amendments in this Constitution are subordinate to the Code of Beta, the Interfraternity '
                            'Council, Samford University, and any applicable municipal, state, or federal laws.\n'
                            '2. If any amendment in this Constitution conflicts with the rules or regulations of any aforementioned '
                            'organizations or authorities, that amendment will be considered null and void.\n'
                            '3. Members cannot be held liable with respect to amendments that are nullified and voided, and charges '
                            'cannot be brought against a member for failing to follow such amendment.'
                        ),
                    },
                ],
            },
        ],
    },
    {
        'doc_type': 'bylaws',
        'title': 'Bylaws of the Samford Chapter, the Alpha Mu of Beta Theta Pi',
        'display_order': 20,
        'amendment_protection_weeks': 10,
        'preamble': (
            'Bylaws of the Samford Chapter, the Alpha Mu of Beta Theta Pi.'
        ),
        'articles': [
            {
                'number': 'I',
                'title': 'Membership',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Membership Expectations',
                        'content': (
                            '1. All initiated members are considered Active Members unless otherwise stated or designated.\n'
                            '2. While all members are responsible for holding one another accountable to these standards, failure '
                            'to uphold the expectations outlined below is grounds for a referral to the Kai Committee for '
                            'appropriate sanctions.\n'
                            '3. Each brother of Beta Theta Pi is expected to hold himself to a high standard. Our brotherhood aids '
                            'the individual, builds the fraternity, and strengthens the host academic institution through lifelong '
                            'devotion to intellectual excellence, high standards of moral conduct, and responsible citizenship. '
                            'Expectations of membership for brothers of Beta Theta Pi include, but are not limited to:\n'
                            '   a. Cultivation of the Intellect\n'
                            '      i. All members must maintain a GPA at or above the All Men\'s Average or a 3.0, whichever is higher.\n'
                            '      ii. Cheating, plagiarism, or academic dishonesty of any kind are not tolerated.\n'
                            '   b. Responsible Conduct\n'
                            '      i. Members will be urbane in deportment, courteous in expression, and steadfast in friendship.\n'
                            '      ii. Members will not use illegal or controlled substances.\n'
                            '      iii. Members will not abuse alcohol or drugs, and through their actions will create a culture of responsibility.\n'
                            '      iv. Members will follow all local, state, federal, and college laws, and adhere to The Code and '
                            'Risk Management Policy of Beta Theta Pi.\n'
                            '   c. Mutual Assistance\n'
                            '      i. Each member is required to complete at least 15 hours of service over the course of each academic year.\n'
                            '   d. Integrity\n'
                            '      i. Members will uphold and maintain the standards of Beta Theta Pi, even if campus culture or '
                            'college expectations are lower.\n'
                            '      ii. Alcohol will not be present during any event, discussion, or interaction with potential new members.\n'
                            '      iii. Beta Theta Pi will maintain a substance and alcohol-free fraternity house (including all interior '
                            'and exterior areas).\n'
                            '      iv. Members will meet all predetermined financial obligations in a timely manner.\n'
                            '   e. Trust\n'
                            '      i. Hazing, as defined by the Risk Management Policy of Beta Theta Pi and Samford University, will not be tolerated.\n'
                            '      ii. Members will treat others with respect through their attitude and actions.\n'
                            '4. Good Standing\n'
                            '   a. A member shall be considered in Good Standing unless such member:\n'
                            '      i. is deemed otherwise through the Kai Committee,\n'
                            '      ii. fails to meet requirements for Good Academic Standing as outlined in Article I, Section 1 (3) of the Bylaws,\n'
                            '      iii. fails to meet all fraternity financial obligations.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Removal of Non-Initiated, Non-Advisor Members',
                        'content': (
                            '1. The process to remove Non-Initiated, Non-Advisor Members from the chapter are as follows:\n'
                            '   a. A case must be brought to the Kai Committee where the Kai Committee will decide if there is '
                            'sufficient reason to remove a member, or members, from the chapter.\n'
                            '   b. Should the Kai Committee conclude there is sufficient reason to remove the member(s) from the '
                            'chapter, then one of the two following requirements must be satisfied:\n'
                            '      i. First; the case is brought to the Education Committee and Executive Board.\n'
                            '         1) Should both committees agree in a 60% majority agreement vote (as determined for each '
                            'committee separately) that the member should be removed from the chapter then the case is closed, '
                            'and the member is to be removed from the chapter.\n'
                            '      ii. Second; should the Education Committee and/or Executive Board be unable to reach the required '
                            '60% majority agreement vote then the Kai Committee must bring the case to the chapter. The chapter will '
                            'decide in a 60% majority vote, where a quorum is present, if the member should be removed or not.\n'
                            '         1) The head of the Kai Committee is the chair and maintains order throughout this meeting.\n'
                            '         2) The processes of this meeting are similar to that of the Trial of Officers (see Article VII, '
                            'Section 1 (c) of the Bylaws).'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'New Member Recruitment',
                        'content': (
                            '1. Potential New Member (PNM) Selection:\n'
                            '   a. To be eligible to be voted upon by the Recruitment Committee, a PNM must:\n'
                            '      i. Be active in the Samford IFC Fall Recruitment if he is being considered for the fall '
                            'recruitment cycle.\n'
                            '      ii. If a PNM is considered as part of the spring recruitment cycle, he must have met with at '
                            'least one (1) member of the Recruitment Committee twice (2 times).\n'
                            '         1) A meeting with a Recruitment Committee member may include recruitment events where at least '
                            'one member of the Recruitment Committee is present or a one-on-one meeting with a member of the committee.\n'
                            '2. Voting:\n'
                            '   a. All members who attend recruitment events and recruitment nights who interacted directly with PNMs '
                            'are eligible to vote on PNMs. If a member decides not to attend a voting meeting, they must abstain from the vote.\n'
                            '      i. If a brother has not met or is unsure if they have met the PNM being voted upon, they must abstain '
                            'from the vote, but they may speak during a discussion.\n'
                            '   b. If a member abstains from voting on a PNM, their vote is not counted in the final vote.\n'
                            '   c. A Recruitment Committee meeting cannot be closed for any reason during voting.\n'
                            '      i. A separate Recruitment Committee meeting can take place after; however, it is open unless voted '
                            'to be closed by the members of the Recruitment Committee. This vote must pass by a simple majority.\n'
                            '   d. For a PNM to receive a bid from the chapter, he must pass a supermajority vote of the non-abstaining, '
                            'Active Members, present during a Recruitment Committee PNM voting meeting.\n'
                            '   e. If a member of the chapter is disruptive while voting upon or discussing a PNM, they are to be warned, '
                            'and if they continue, they are to be removed from the meeting, and the Recruitment Committee must vote as to '
                            'whether they are to be re-included at the next meeting. This vote is done by a supermajority requirement of '
                            'Recruitment Committee members.\n'
                            '3. PNM Evaluation Criteria:\n'
                            '   a. Potential New Members will be evaluated based on quantitative and qualitative factors.\n'
                            '      i. Quantitative factors include but are not limited to: GPA, leadership positions held, and amount '
                            'of community service hours.\n'
                            '      ii. Qualitative factors include but are not limited to: demonstration of Beta Theta Pi\'s values, '
                            'strength of character, and depth of personal experiences.\n'
                            '      iii. Specifics on quantitative and qualitative factors are outlined in Article I, Section 1 (3) (a-e) '
                            'of the Alpha Mu Bylaws.\n'
                            '   b. Any active member in Good Standing with the chapter may submit recommendations or specific input on '
                            'a Potential New Member to the Vice President of Recruitment, who must provide said information to the '
                            'Recruitment Committee at the appropriate time, prior to the vote regarding said PNM.'
                        ),
                    },
                ],
            },
            {
                'number': 'II',
                'title': 'Officers',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Announcement of Officer Selection',
                        'content': (
                            '1. Officer selection should take place within the period of time between the initiation of the fall '
                            'new member class and the end of the fall academic semester. The appropriate dates should be established '
                            'and announced at the beginning of the academic year by the Executive Board. The following events need '
                            'to be addressed:\n'
                            '   a. Introduction and explanation of selection process\n'
                            '   b. Class Representative Elections\n'
                            '   c. Slating Committee Meeting and Selection Meeting\n'
                            '2. The President or the Constitution and Bylaws Chair should explain the selection process during a '
                            'chapter meeting prior to the scheduled Class Representative Elections.\n'
                            '3. The Slate Selection meeting should occur after all candidates running for a position have been '
                            'interviewed by the committee, to create a final slate to present to the chapter. The presentation of '
                            'the slate should take place during, or replace, a regularly scheduled chapter meeting.\n'
                            '4. The Slating Committee may not vote on the creation of a final slate unless all committee members are present.\n'
                            '5. The Slating Committee meetings must begin at least 2 weeks prior to when the slate will be presented.\n'
                            '6. The slate must be presented to the chapter at least 1 week prior to the end of the semester.\n'
                            '7. When the Slating Committee compiles and completes a final slate, all members of the slate must '
                            'immediately be contacted by at least one (1) member of the Slating Committee to inform them of the following:\n'
                            '   a. That he has been selected as a candidate on the final slate.\n'
                            '   b. What position he has been slated for.\n'
                            '8. Should the committee have left a position to a run-off election, both candidates should be contacted '
                            'immediately by the chair of the committee and informed of their placement within the slate.\n'
                            '   a. The chair should not tell a candidate who the other candidate being considered is and keep his name anonymous.\n'
                            '9. Should the Slating Committee have chosen to leave a position without a named candidate:\n'
                            '   a. All candidates who ran for the position should be informed by the chair of the Slating Committee of '
                            'the committee\'s decision.\n'
                            '   b. Additionally, all candidates must be informed of the process(es) for how deciding upon a candidate '
                            'will be conducted at the chapter meeting where the slate is presented.\n'
                            '10. Except for in the cases of previous exceptions, candidates who were not selected in the final slate '
                            'for a position do not need to be informed of the committee\'s decision(s).'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Officer Slating Committee',
                        'content': (
                            '1. The Slating Committee\'s membership shall be composed of the President, Constitution and Bylaws '
                            'Committee Chair, each and all class representatives, and advisors. The President will oversee this '
                            'committee, and advisors will provide input as needed.\n'
                            '   a. There must be at least one class representative from each of the following academic groups:\n'
                            '      i. Senior class\n'
                            '      ii. Junior class\n'
                            '      iii. Sophomore class\n'
                            '      iv. New Member and/or Newly Initiated Member Class\n'
                            '         (1) The New Member class is made up of all brothers who have been initiated during the current '
                            'semester. The New Member class representative is to be chosen, after initiation, prior to the first '
                            'Slating Committee meeting.\n'
                            '   b. Class Representatives:\n'
                            '      i. Class Representatives are to be chosen at least 1 week prior to the first Slating Committee meeting.\n'
                            '         (1) The New Member class is exempt from this requirement should the timing of initiation make '
                            'this deadline not possible.\n'
                            '      ii. Class Representatives must be initiated brothers, currently in good standing with Samford '
                            'University, the General Fraternity, and the Alpha Mu chapter.\n'
                            '      iii. Placement of a person\'s class is based on their completed credits/hours per University Standards.\n'
                            '      iv. Each class shall delegate a representative from among them to represent their class in the Slating '
                            'Committee. This delegate is voted upon by a simple majority of other members of their class.\n'
                            '      v. A class representative cannot be the President or the Constitution and Bylaws Committee Chair.\n'
                            '   c. Advisors:\n'
                            '      i. Are not required at meetings; however, a minimum of one (1) advisor should be present at each '
                            'Slating Committee meeting.\n'
                            '      ii. Are given an advisory vote in a subset of circumstances. These circumstances can be found in '
                            'Article II, Section 2 (6) of the Bylaws of the Samford Chapter, the Alpha Mu of Beta Theta Pi.\n'
                            '2. Should the President be eligible and interested in being slated for an Executive Board position, a '
                            'current Executive Board officer who is not eligible and interested should be appointed by the advisors '
                            'to assume these responsibilities.\n'
                            '   a. This vote is given by the advisors agreeing on a simple majority vote for an Executive Board officer '
                            'to replace the President.\n'
                            '   b. The member to replace the President as head of the Slating Committee must meet the following requirements:\n'
                            '      i. Must meet or exceed the required chapter GPA\n'
                            '      ii. Must not have any outstanding fines or dues\n'
                            '      iii. Must not have any outstanding Kai cases\n'
                            '      iv. Must be an active brother during the current term\n'
                            '      v. Must not be running for any position being slated\n'
                            '      vi. Be in good standing with Samford University, the General Fraternity, and the Alpha Mu chapter\n'
                            '3. Should a member of the Slating Committee be eligible and interested in being slated for an Executive '
                            'Board position with the new team, he will recuse himself from the Slating Committee without appointing a replacement.\n'
                            '   a. If that member is a Class Representative, his class should appoint a new representative in his place '
                            'as soon as possible.\n'
                            '   b. If no other members of the class would be eligible for membership on the Slating Committee, the '
                            'advisors should appoint a new, eligible member. This process being similar to the process of appointing a '
                            'new chair of the committee.\n'
                            '4. All Slating Committee Meetings are closed to all non-advisor or non-committee members.\n'
                            '   a. During interviews, an interviewee may be present during their time to meet with the committee.\n'
                            '   b. Interviewees may not be present at any other interviews before or after their interview has taken place.\n'
                            '5. Confidentiality and compromise must be stressed and maintained from the outset of the meeting.\n'
                            '   a. The Slating Committee, the minutes taken at any of its meetings, or conversations had cannot be '
                            'discussed with any non-committee members.\n'
                            '   b. Any and all minutes taken must be destroyed after the chapter approves a final slate.\n'
                            '   c. Minutes of the committee cannot be published or shown to non-committee members.\n'
                            '   d. Violation of this may result in a referral to the Kai committee at the discretion of the head of the '
                            'committee or the Constitution and Bylaws Chair, if he is a member of the Slating Committee.\n'
                            '6. The vote for how a member obtains a place on the slate is done by a simple majority vote of members on '
                            'the Slating Committee.\n'
                            '   a. Each vote from committee members is weighed equally. Advisors are not given a vote unless in the case '
                            'of a tie, in which case they would be given one (1) vote to break the tie. This vote is given by the advisors '
                            'agreeing on a simple majority vote for a candidate on the slate.\n'
                            '   b. If the advisors are unable to agree upon a candidate or no advisors are present, then the top two (2) '
                            'candidates are brought to the chapter to be voted upon when the slate is presented.\n'
                            '   c. Any member or advisor of the committee who fails to appear at a committee meeting that is regularly '
                            'scheduled, voids their right to vote.\n'
                            '   d. The role of advisors is primarily to offer insight or give advice on candidates or roles.\n'
                            '7. Applicants\' Slating Placement Requirements:\n'
                            '   a. All members wishing to be slated for an executive position must fill out an Executive Interest Form '
                            '(See Section 1 of the Appendix for the form\'s requirements). The Executive Interest Form must be put in a '
                            'place where all active members can easily access the form. Notice must be given to all members of the chapter '
                            'when the form is published.\n'
                            '   b. Applicants will be grouped into one (1) of three (3) categories based on their GPA. The groups and '
                            'information about these groups can be found in Section 2 of the Appendix.\n'
                            '   c. After completing interviews with the applicants, the Slating Committee will begin forming the slate. '
                            'During this process:\n'
                            '      i. The Slating Committee must first consider all applicants in Level 1, taking their interviews into '
                            'account. The committee should prioritize placing Level 1 applicants in the slate before considering those in '
                            'Level 2. If the committee has significant concerns about any applicant at any level, they may vote to bypass '
                            'that applicant after thoroughly considering all available options. This vote must be passed by a supermajority '
                            'vote of Slating Committee members where a quorum of Committee members is present.\n'
                            '      ii. If the slate is not filled after considering Level 1 applicants, the committee may then evaluate '
                            'Level 2 candidates, following the same process as with Level 1.\n'
                            '      iii. If the previous steps do not result in a completed slate, the committee may then consider '
                            'applicants in Level 3.\n'
                            '      iv. All applicants seeking to be slated must also meet the following requirements, regardless of '
                            'their placement Level:\n'
                            '         (1) Must not have any outstanding fines or dues\n'
                            '         (2) Must not have any outstanding Kai cases\n'
                            '         (3) Must be an active brother during the following term\n'
                            '         (4) Must not be graduating before their term would end\n'
                            '8. The following offices are to be selected using the slating process:\n'
                            '   a. President\n'
                            '   b. Executive Vice President & IFC Delegate\n'
                            '   c. Vice President of Brotherhood\n'
                            '   d. Vice President of Recruitment\n'
                            '   e. Vice President of Education\n'
                            '   f. Vice President of Risk Management\n'
                            '   g. Vice President of Finance\n'
                            '   h. Vice President of Administration\n'
                            '   i. Vice President of Programming\n'
                            '9. Officer selection should take place within the period between the initiation of the fall new member '
                            'class and the end of the fall academic term. The appropriate dates and proceedings of the Slating Committee '
                            'should be established and announced by the President or a delegate of the President.\n'
                            '   a. The interviewee for the slate must be given an uninterrupted voice during the course of the interview.\n'
                            '   b. The Slating Committee members and advisors are free to ask questions to the interviewee concerning '
                            'occupation, academic, and fraternity related activities.\n'
                            '   c. The interviewee can decline to answer any questions; however, this can be taken into consideration '
                            'by the committee when deciding the slate.\n'
                            '10. Should the Slating Committee have significant concerns about a member attempting to be slated for a '
                            'position, and no suitable alternative is available, the Slating Committee may vote to leave the position unfilled.\n'
                            '   a. Should the Slating Committee choose to leave a position without a nominee the position is then brought '
                            'to the chapter to be elected after the primary slate has been passed.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Presentation of the Slate',
                        'content': (
                            '1. The presentation of the slate should occur soon after the completion of the Slating Committee\'s meetings.\n'
                            '2. All ballots that occur during the course of the presentation and voting of the slate must be done by secret ballot.\n'
                            '3. Representation:\n'
                            '   a. Attendance is required of all active members, keeping in mind that a quorum must be present to vote on the slate.\n'
                            '   b. At least one (1) advisor must be present to observe but will not have a vote during the proceedings.\n'
                            '4. Procedure:\n'
                            '   a. The slate must be brought to the chapter prior to the final chapter of the semester.\n'
                            '   b. The Constitution and Bylaws Chair will explain the order of events. Confidentiality should be stressed once more.\n'
                            '   c. Robert\'s Rules for Order must be strictly followed during the duration of the meeting.\n'
                            '   d. The Constitution and Bylaws Chair will present the slate to the chapter.\n'
                            '   e. Before the slate is voted upon, the Constitution and Bylaws Chair must make a motion to have a minimum '
                            'of a three (3) minute moderated caucus where any member of the chapter may approach the floor to discuss the slate. '
                            'The Sergeant-At-Arms is responsible for maintaining order during this discussion.\n'
                            '   f. The Sergeant-At-Arms is responsible for maintaining order during the meeting and may remove members from the '
                            'meeting at his, the President\'s, or Constitution and Bylaws Chair\'s discretion. If a member is removed from the '
                            'meeting the Sergeant-at-Arms must refer the member to the Kai Committee for appropriate disciplinary action.\n'
                            '   g. The Constitution and Bylaws Chair will distribute the final ballot and announce the slate. Nominations are '
                            'not allowed from the floor.\n'
                            '   h. A 60% majority vote must be reached to pass the slate.\n'
                            '   i. All ballots relating to the slate must be done by secret ballot, where members are to be instructed to '
                            'simply write/type "pass" or "fail" on the ballot.\n'
                            '      i. If a member writes "fail," he must write what office(s) he is concerned about. Ballots will then be '
                            'collected by members of the Slating Committee.\n'
                            '   j. The Slating Committee will leave the room and count the vote. There should be no discussion of the slate '
                            'in the main room at this time. The Sergeant-At-Arms is responsible for maintaining order.\n'
                            '   k. Should the slate fail to pass:\n'
                            '      i. The Slating Committee may or may not make any changes before they reenter the main room. A slate will '
                            'be presented, and members will be instructed to vote in the same manner.\n'
                            '      ii. Should the slate fail a second time, the Slating Committee may or may not make changes to the slate '
                            'before they reenter the main room. A slate will again be presented, and members will be instructed to vote in the same manner.\n'
                            '      iii. If the Slate fails a third time, a "pass/fail" ballot will be taken for each individual office.\n'
                            '         (1) Each office will be voted upon separately, sans discussion, in order of which they are presented on the slate.\n'
                            '         (2) Each office must pass with a 60% majority.\n'
                            '            (a) If an office fails to receive the required 60% vote, the Slating Committee must change the nominee '
                            'for that position.\n'
                            '5. If the Slating Committee nominations for offices include a position with no nominee the following is to take place:\n'
                            '   a. The slate, including the vacant position, should be passed by the chapter prior to any of the following occurring.\n'
                            '   b. The chair will open the floor to nominations of members for the position.\n'
                            '      i. A person may nominate themselves or someone else.\n'
                            '      ii. A nominee must accept the nomination before being considered for the position.\n'
                            '   c. After a brief period of nominations, a short, maximum of a seven (7) minute, recess should occur for the '
                            'nominees to prepare a brief speech.\n'
                            '      i. This speech being up to two (2) minutes long but should not exceed two (2) minutes.\n'
                            '   d. After the speeches have occurred the nominees should be removed from the room for the chapter to have a five '
                            '(5) minute moderated caucus where the candidates are discussed.\n'
                            '   e. After the caucus is ended, the Slating Committee chair will instruct the chapter to begin voting. The vote '
                            'should include all the nominees\' names and the option to "abstain."\n'
                            '   f. A nominee must reach a 60% majority vote to be selected.\n'
                            '   g. Should a 60% majority be unable to be reached on the first ballot:\n'
                            '      i. A "true division of the house" must occur. During this vote, the option to "abstain" is removed and all '
                            'members present must vote for one of the nominees.\n'
                            '      ii. Should a candidate be unable to reach a 60% majority vote after a true division then only the two (2) '
                            'candidates with the most votes on the previous ballot will be placed on a ballot that only includes their names '
                            'with no option to abstain. All members must vote on one of the two candidates.\n'
                            '      iii. If a 60% majority is unable to be reached at this point, the 60% majority requirement will be lowered '
                            'to a 51% majority requirement.\n'
                            '      iv. After two (2) ballots where the previous measure is tried, if a candidate is still unable to reach the '
                            '51% majority requirement, then the position will be left blank and the Slating Committee will meet before the '
                            'next chapter meeting to conduct any necessary interviews or new nominations for the unfilled position and bring '
                            'a nomination to the next chapter meeting where the process outlined in Article II, Section 2 of the bylaws will '
                            'once again be followed.'
                        ),
                    },
                ],
            },
            {
                'number': 'III',
                'title': 'Self-Governance',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Definition',
                        'content': (
                            'Self-governance is the responsibility to do the right thing, as dictated by Beta\'s principles. '
                            'It does not mean having the unquestioned right to do anything.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Kai Committee',
                        'content': (
                            '1. The Kai Committee should be chaired by the Kai Committee Chair and include an additional four (4) '
                            'or eight (8) members: one or two elected senior delegate(s), one or two elected junior delegate(s), '
                            'one or two elected sophomore delegate(s), and one or two at-large delegate(s).\n'
                            '   a. The size will be determined based on simple majority vote of the chapter. Members that are not '
                            'in Good Standing are ineligible to serve on the Kai Committee.\n'
                            '   b. Members of the Executive Board, other than the Vice President of Brotherhood, are ineligible for '
                            'the Kai Committee membership when the total number of Active Members in Good Standing is greater than forty (40).\n'
                            '   c. The Kai Committee Chair must be a member of the current Executive Board, usually the Vice President '
                            'of Brotherhood.\n'
                            '2. Kai Committee nominations will be proposed soon after the election of new executive officers.\n'
                            '3. In the case that a nominee is deemed unfit to fulfill the duties and responsibilities of the Kai Committee '
                            'by both the Vice President of Brotherhood and the Brotherhood Advisor, they can refer the candidate to the '
                            'Executive Board for review and potential removal from the ballot.\n'
                            '4. The Kai Committee is responsible for adjudicating all breaches of membership expectations and obligations '
                            'and acting as a mediating body.\n'
                            '5. The Kai Committee is responsible for acting as the steward of positive brotherhood and recognizing brothers '
                            'for their achievements.\n'
                            '6. The Kai Committee Chair must notify the committee of any and all pending referrals weekly and if a case '
                            'meeting is necessary.\n'
                            '7. If a member of the Kai Committee falls out of Good Standing while serving their term, they are automatically '
                            'removed from the Kai Committee, with election for a new delegate happening at the next regular chapter meeting.\n'
                            '8. The Kai Committee will handle all reported breaches to the Constitution or Bylaws and may take up unreported '
                            'cases where it believes a breach has taken place by a member of the chapter.\n'
                            '   a. All breaches of the Constitution or Bylaws should be taken as a serious matter by Kai Committee and the '
                            'proceedings of all cases related to it must reflect this seriousness.\n'
                            '   b. The Constitution and Bylaws Chair may be called as a witness to question as to whether, in his opinion, '
                            'a substantial breach has occurred.\n'
                            '   c. Should a member be found to be in breach of the Constitution or Bylaws the following sanctions should be '
                            'considered: revoking of a member\'s Good Standing status, fines, social probation, and/or removal from office(s). '
                            'Should the breach(es) be serious enough, a Trial by Chapter should be considered as an option by the committee.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Accountability Proceedings',
                        'content': (
                            '1. Case Recommendation and Referral\n'
                            '   a. Should an active member or new member violate membership expectations, any active or new member may '
                            'submit a Kai referral form to the Kai Committee Chair.\n'
                            '   b. The Kai Committee Chair reserves the right to request and gather more information from an individual '
                            'who has submitted a referral prior to convening the Kai Committee.\n'
                            '   c. The Kai Committee reserves the right to dismiss any case recommendation or referral that does not '
                            'constitute a significant break in membership expectations.\n'
                            '   d. If a member or members\' violations are clearly apparent, the Kai Committee may act without a written '
                            'case recommendation.\n'
                            '2. Kai Committee Hearing\n'
                            '   a. The Accused should be made aware of the charge(s) against him and the time, date, and location of the hearing.\n'
                            '   b. The Accused has the rights as outlined in Chapter XIII, Section 2, D of the Code of Beta Theta Pi.\n'
                            '   c. The Kai Committee will review the referral and testimony from all relevant parties.\n'
                            '   d. The committee formulates and recommends courses of action to take in accordance with the severity of the accusation.\n'
                            '   e. Members may appeal their sanctions from the Kai Committee at the next regular chapter meeting. If the chapter '
                            'agrees by a two-thirds vote the sanctions are inappropriate, then new sanctions will be determined by the Kai '
                            'Committee at their next meeting.\n'
                            '   f. An advisor should be present at all Kai Committee meetings, though they are not a voting member.\n'
                            '3. Violations and Sanctions\n'
                            '   a. For violations that include, but are not limited to: possession and/or use of any illegal drug, all forms '
                            'of hazing, committing a felony, reckless endangerment of any person, possession and/or consumption of alcohol '
                            'on fraternity property, DUI violation, or any other action deemed by the Kai Committee.\n'
                            '      i. Recommended sanctions may include, but are not limited to: expulsion from membership (pending Trial '
                            'by Chapter). If expulsion from membership is not passed by the chapter, the Kai Committee will apply a possible '
                            'sanction listed below.\n'
                            '   b. For violations that include, but are not limited to: failure to fulfill assigned risk management duty, '
                            'destruction of property, financial delinquency, any action defacing the name of Beta Theta Pi, Minor In '
                            'Possession, failure to fulfill assigned responsibilities, or a violation of any aspect of the Risk Management '
                            'Policy of Beta Theta Pi.\n'
                            '      i. Recommended sanctions may include, but are not limited to: removal from office, suspension, social '
                            'probation, recommended counseling, additional nights of sober brother duty, assessment of fees related to '
                            'damage caused, and any other sanctions found appropriate that relate to community service, service to the '
                            'chapter, or personal growth on the part of the brother in violation.\n'
                            '   c. For violations that may include, but are not limited to: any accidental property damage, attendance '
                            'policy violations, failure to make grades, personal injury or conduct in bad taste and unbecoming of a Beta.\n'
                            '      i. Recommended sanctions may include, but are not limited to: a formal apology, any repairs required, '
                            'fines, or education in the area of wrongdoing.\n'
                            '4. Failure to fulfill any sanctions will result in the assignment of more sanctions from the Kai Committee.'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Academic Assistance Plan',
                        'content': (
                            '1. All members are expected to maintain a Grade Point Average (GPA) at or above the All Men\'s Average (AMA) '
                            'or a 3.0, whichever is higher, each term.\n'
                            '2. The President, Vice President of Education, and the Chapter Counselor advisor will receive a final grade '
                            'report from each member following every term.\n'
                            '3. Terms of Academic Standing:\n'
                            '   a. Good Academic Standing: At or above the AMA or a 3.0, whichever is higher.\n'
                            '   b. Warning: Within 0.2 points of the AMA or between a 2.8 and a 2.99, whichever is higher.\n'
                            '   c. Probation One: Within 0.4 points of the AMA or between a 2.6 and a 2.79, whichever is higher.\n'
                            '   d. Probation Two: More than 0.4 points below the AMA or a 2.6.\n'
                            '4. Warning Status\n'
                            '   a. Members on warning status must meet with the Kai Committee to review their academic performance.\n'
                            '   b. Members on warning status will have five (5) required study hours per week.\n'
                            '   c. Members on warning status must meet with the academic assistance office on campus to develop a study plan.\n'
                            '   d. Two or more consecutive terms on warning status will result in the Member being moved to probation one.\n'
                            '5. Probation One\n'
                            '   a. Members on probation one must meet with the Kai Committee to review their academic performance.\n'
                            '   b. Members on probation one must meet with either the Vice President of Education or the Scholarship Chair '
                            'each month to review his academic standing and address any concerns.\n'
                            '   c. Members on probation one will have 10 required study hours per week.\n'
                            '   d. Members on probation one must meet with the academic assistance office on campus to develop a study plan.\n'
                            '   e. Members on probation one for more than one term in a row are automatically referred to the Kai Committee '
                            'with a recommendation of expulsion by Trial by Chapter.\n'
                            '   f. If a member is not removed via Trial by Chapter after consecutive semesters on probation one, suggested '
                            'sanctions include but are not limited to: social probation, suspension, additional academic assistance.\n'
                            '6. Probation Two\n'
                            '   a. Members on probation two are automatically referred to the Kai Committee with a recommendation of '
                            'expulsion by Trial by Chapter.\n'
                            '   b. If a member is not removed via Trial by Chapter after being on probation two, suggested sanctions include '
                            'but are not limited to: social probation, suspension, additional academic assistance.\n'
                            '7. Types of Academic Assistance\n'
                            '   a. Required study hours may be proctored and/or recorded by the Scholarship chair, Kai committee member, '
                            'or any other member of the Executive Board.\n'
                            '   b. Required educational sessions (e.g., time management, work-life balance, healthy study habits, etc.).\n'
                            '   c. Assigned tutor (internal or external).\n'
                            '   d. Office hour visits or meetings with professors.\n'
                            '   e. Meet with the on campus academic assistance office to develop a study plan.\n'
                            '   f. Pairing incoming members with a mentor in the same subject.'
                        ),
                    },
                ],
            },
            {
                'number': 'IV',
                'title': 'Financial',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Dues',
                        'content': (
                            '1. Annual dues for each member shall be set by the chapter and approved by a majority vote.\n'
                            '2. All active members are eligible to receive payment plan options for paying any fines or dues. A member '
                            'must request to be on a payment plan before a bill is issued. The Vice President of Finance has the authority '
                            'to evaluate each request to be on a payment plan. Requests for a payment plan will not be accommodated after '
                            'the bills are sent.\n'
                            '3. All members shall receive all General Fraternity fees, local dues, and other fees via Billhighway.\n'
                            '4. All members have two (2) weeks to pay the bill in full or make the first payment of a payment plan.\n'
                            '5. The Vice President of Finance will refer any member delinquent on his dues, fees, or other fines to the Kai Committee.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Budget',
                        'content': (
                            '1. The VP of Finance shall prepare a budget in collaboration with the Finance Committee and President.\n'
                            '2. The budget must be approved by the Finance Advisor and Chapter Counselor prior to it being voted upon by the chapter.\n'
                            '3. The chapter must have a budget approved by a simple majority vote for each semester prior to the final '
                            'chapter of the preceding term.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Spending',
                        'content': (
                            '1. All bills shall be paid by check or Billhighway card.\n'
                            '2. All funding requests must be submitted to the Vice President of Finance at least 48 hours in advance to the transaction.\n'
                            '3. All transactions must be approved by two of the following: Vice President of Finance, President, Finance Advisor, '
                            'or Chapter Counselor.'
                        ),
                    },
                ],
            },
            {
                'number': 'V',
                'title': 'Rituals and Ceremonies',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Formal Chapter',
                        'content': (
                            '1. Formal Chapter meetings must be held at least once a month, following the Ritual outline for such an event, along with:\n'
                            '   a. Badge Attire, including a tie and badge.\n'
                            '2. The first chapter meeting of each month should be designated as the Formal Chapter, at the discretion of '
                            'the chapter President. Additionally, the opening and closing chapter of each academic semester should be held as formal.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Pre-Initiation Practices',
                        'content': (
                            'All pre-initiation and initiation ceremonies shall follow the fraternity\'s prescribed Ritual and be conducted '
                            'within the chapter house or hall unless prior approval is obtained.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Ceremony Approval',
                        'content': (
                            'Local ritual ceremonies, not stated in the ritual book, must be submitted in writing to the District Chief '
                            'for pre-approval.'
                        ),
                    },
                ],
            },
            {
                'number': 'VI',
                'title': 'Executive Board Expectations',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Expectations of all Board Members',
                        'content': (
                            '1. All Executive Board members will:\n'
                            '   a. Attend the annual officer transition meeting. The tentative date, time and location should be provided '
                            'no later than two regularly scheduled chapter meetings preceding the Class Meetings.\n'
                            '   b. Attend all required campus and General Fraternity events and trainings.\n'
                            '   c. Attend regularly scheduled Chapter and Executive Board meetings.\n'
                            '   d. Prepare and give an officer report at each meeting.\n'
                            '   e. Understand and abide by the Code of Beta Theta Pi and the Beta Risk Management Policy.\n'
                            '   f. Understand and abide by this chapter\'s constitution, bylaws, and membership expectations.\n'
                            '   g. Understand and abide by all IFC policies.\n'
                            '   h. Read all messages and reports from Beta Theta Pi\'s Administrative Office(s).\n'
                            '   i. Respond in a timely manner for all requests from advisors, campus officials, General Fraternity '
                            'Officers, and Administrative Office Staff.\n'
                            '   j. Read and follow up on items in the Beta Brief, a monthly e-newsletter with reminders and action items for officers.\n'
                            '   k. Serve as a role model within the chapter and in the larger campus community by upholding all membership '
                            'expectations and living out the mission, vision, and values of Beta Theta Pi.\n'
                            '2. Should a member be unable to meet or neglect these responsibilities, he will automatically be released from '
                            'the duties and responsibilities of his office.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Individual Officer Responsibilities',
                        'content': (
                            '1. President\n'
                            '   a. Be the face of the fraternity between all Greek life and the general fraternity.\n'
                            '   b. Executive Board is your committee — preside over it and make sure that it is functioning in an organized manner.\n'
                            '   c. Chapter delegate for IFC and the General fraternity.\n'
                            '   d. Serve on the recruitment committee and make sure it is running in a functional manner.\n'
                            '   e. Lead chapter and chapter events in a well-structured and organized manner.\n'
                            '   f. Delegate with chapter advisors to discuss the direction of the chapter.\n'
                            '   g. Attend IFC council meetings, and relay information back to your Executive Board.\n'
                            '   h. Attend General Convention where you will represent your chapter as a delegate.\n'
                            '   i. Be a strong and understanding leader who puts the chapter first in thought.\n\n'
                            '2. Executive Vice President\n'
                            '   a. Organize the actions of the Vice Presidents by connecting them, when necessary, with one another and '
                            'with various university departments to aid them in their tasks.\n'
                            '   b. Troubleshoot and monitor the Vice Presidents\' projects.\n'
                            '   c. Know and understand all chapter policies and expectations, including chapter Constitution and Bylaws '
                            'and IFC Constitution and Bylaws.\n'
                            '   d. Understand Roberts Rules of Order and serve as parliamentarian during chapter meetings.\n'
                            '   e. Know the General Fraternities Standard Chapter Operating Expectations and work with other officers to '
                            'develop plans to reach Knox level for all categories.\n'
                            '   f. Stay current on relevant happenings of the North American Interfraternity Conference (NIC) and share '
                            'pertinent information to chapter delegation.\n'
                            '   g. Serve in place as President as needed in times of his absence/inability to serve.\n'
                            '   h. Represent Beta Theta Pi and the chapter to the campus community.\n'
                            '   i. Encourage participation in programming held by IFC or other Greek Councils on campus.\n'
                            '   j. Hold a training or review, at least once per school year, of Chapter Constitution and Bylaws with entire '
                            'chapter membership.\n'
                            '   k. Lead a process to review and revise the chapter\'s Constitution and Bylaws annually.\n'
                            '   l. Head the Constitution and Bylaws committee, ensuring that the membership meets the requirements listed.\n'
                            '   m. Host a Robert\'s Rules of Order training at least once per term.\n'
                            '   n. Participate in all transition meetings at the end of the term to onboard newly elected Executive Vice President.\n'
                            '   o. Coordinate required programs as required by the IFC, such as campus-based recognition or accreditation process.\n'
                            '   p. If no other member has been appointed or elected to such position, to be the IFC Delegate. The duties and '
                            'responsibilities as such are by default part of the EVP\'s duties and responsibilities.\n\n'
                            '3. Vice President of Brotherhood\n'
                            '   a. Maintain & develop brotherhood bonds.\n'
                            '   b. Hold at least one brotherhood event per semester.\n'
                            '   c. Aid in other brothers trying to put on events that include or involve brotherhood.\n'
                            '   d. Hold one Eye of Wooglin during the semester.\n'
                            '   e. Ensure Tailgates are applied for and properly planned and executed.\n'
                            '   f. Ensure rules are kept.\n'
                            '   g. Kai Committee — ensures that brothers are maintaining the rules of the college and Beta Theta Pi, also a '
                            'system to reward brothers for good behavior. Operations of the Kai Committee can be found in Article VI, '
                            'Section 1 (a) of the Bylaws of the Samford Chapter, the Alpha Mu of Beta Theta Pi.\n\n'
                            '4. Vice President of Recruitment\n'
                            '   a. Recruit PNMs to fraternity during the spring and fall.\n'
                            '   b. Organize and plan recruitment events during fall and spring recruitment.\n'
                            '   c. Set up one on ones with PNMs and brothers during fall and spring recruitment.\n'
                            '   d. Organize and run weekly recruitment committee meetings having the Vice President of Administration '
                            'reserve a location.\n'
                            '   e. Draft a recruitment committee to be voted on by the chapter.\n'
                            '   f. Organize tabling during recruitment seasons in fall and spring.\n'
                            '   g. Establish a tabling chair that helps run tabling, making sure brothers sign up to table.\n'
                            '   h. Coordinate with IFC to oversee how recruitment week will be taking place.\n'
                            '   i. Lead brothers in setting up and taking down for recruitment events.\n'
                            '   j. Lead and run recruitment workshop with chapter members.\n\n'
                            '5. Vice President of Risk Management\n'
                            '   a. Ensure the chapter upholds the most recent version of the Beta Theta Pi Risk Management Policy.\n'
                            '   b. Presiding over the Social Committee, which oversees planning, setting up and hosting major social events '
                            'for the chapter (i.e., semi-formal/formal events and mixers).\n'
                            '   c. Work alongside all other current VPs to ensure they follow safe event planning procedures.\n'
                            '   d. Handle insurance and all insurance related policies.\n'
                            '   e. Be sure all the brothers of its chapter uphold the "Good Samaritan Policy."\n'
                            '   f. Act as the chapter\'s "House Manager."\n\n'
                            '6. Vice President of Programming\n'
                            '   a. Keep track of and organize service hours from all the brothers in the chapter (this includes reporting '
                            'our total amount of service hours to the general fraternity).\n'
                            '   b. Organize at least one large fundraiser every semester for our philanthropy.\n'
                            '   c. Organize and offer service hour opportunities for brothers throughout the entire year.\n'
                            '   d. Organize time for the wellness chair to offer wellness hours that brothers can show up to if they need it.\n'
                            '   e. Organize and manage intramural teams if any of the brothers want to take part in one.\n\n'
                            '7. Vice President of Education\n'
                            '   a. Planning, organizing, and running pledge-education meetings.\n'
                            '   b. Maintaining and ensuring a well-kept chapter GPA.\n'
                            '   c. Providing brothers with tutoring and academic help.\n'
                            '   d. Maintaining and updating chapter educational materials.\n'
                            '   e. Connecting brothers with tutors both inside and outside the fraternity.\n'
                            '   f. Ensuring the security of chapter educational materials.\n'
                            '   g. Confirming Samford\'s honor code is kept by chapter members, and coordinating with Risk Management in '
                            'the event of a violation.\n'
                            '   h. Preventing and reducing the likelihood of hazing incidents. Coordinating with Risk Management as required.\n'
                            '   i. Coordinating with advisors and counselors to create new educational materials and provide new educational '
                            'opportunities to members.\n'
                            '   j. Coordinating with other executive members by necessity.\n\n'
                            '8. Vice President of Administration\n'
                            '   a. Recorder for both chapter and exec meetings (if in other committees, it can be designated to VP of Administration).\n'
                            '   b. Organize and maintain composites through companies including Collegiate Composites, including date, location, '
                            'and photoshoot time in order for brothers to be photographed.\n'
                            '   c. Organize and maintain all social platforms associated with the Beta Theta Pi Alpha Mu chapter (Instagram, '
                            'Facebook, etc.). This also includes designing social posts for said platforms.\n'
                            '   d. Design shirts (upon request) through companies such as Merch House for any event associated with the Alpha '
                            'Mu chapter (Recruitment, Philanthropy, Formals, Date Parties, etc.).\n'
                            '   e. Maintain and operate communications platforms for brothers (OurHouse, Discord, GroupMe). This includes '
                            'mastering the apps, knowing the ins and outs and knowing how to solve any technical problems (if they arise).\n'
                            '   f. Organize any "behind the scenes" details for events such as room reservations, time of reservation, any '
                            'other locations outside rooms needed for reservation, number of chairs and tables for reservation, specific items '
                            'needed for events, number of attendees, etc.\n\n'
                            '9. Vice President of Finance\n'
                            '   a. Troubleshoot Billhighway and ensure money is being sent to where it needs to go.\n'
                            '   b. Keep in touch with other VPs to ensure that they are within their budgets.\n'
                            '   c. Collect dues from brothers.\n'
                            '   d. Build payment plans to help brothers if needed.\n'
                            '   e. Ensure fees are paid.\n'
                            '   f. Both to general fraternity and to venues for events. Also includes loading the cards and creating checks '
                            'to reimburse brothers and stores.\n'
                            '   g. The finance committee helps go around collecting money. They also can help make large financial decisions.\n'
                            '   h. Organize a budget to keep the fraternity on track with revenues and expenses.'
                        ),
                    },
                ],
            },
            {
                'number': 'VII',
                'title': 'Committee Details',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Kai Committee',
                        'content': (
                            'a. Committee Outlines and Procedures:\n'
                            '   i. The Kai Committee shall handle all disciplinary reviews for all members and pledges. Penalties may '
                            'include but are not limited to probation, suspension, expulsion, or fines as appropriate.\n'
                            '   ii. The head of Kai, or secretary, shall keep a written record that organizes the minutes of all Kai '
                            'Committee meetings. The record must contain the following: Date of meetings, committee members present, '
                            'brothers called, discussion, and sanctions. This information is to be collected by the secretary of the '
                            'committee and is to be recorded consistently from year to year.\n'
                            '   iii. All Kai Committee meetings reviewing a member\'s conduct must adhere to the Rights of the Accused '
                            'as outlined in Chapter XIII, Section 2 (D) of the Code of Beta Theta Pi.\n'
                            '   iv. If deemed necessary or appropriate, the Kai Committee may approve a Trial by Chapter for an accused '
                            'member, as outlined in Chapter XIII, Section 4 of the Code of Beta Theta Pi.\n'
                            '   v. If a member of the Kai Committee is sent to the Kai Committee for a disciplinary action, their position '
                            'will be filled temporarily, pending the number of committee members falls below 5, and the VP of Risk '
                            'Management shall fill their place.\n'
                            '   vi. Should members of the Kai Committee be recused from their duties, the head of Kai shall appoint '
                            'suitable replacement(s) for the position. However, should the offenses be separate from each other, then '
                            'their trials remain separated and only the accused must temporarily recuse their seat for their trial.\n'
                            '   vii. Should the VP of Risk Management be unable to fill the vacancy, a suitable replacement member will '
                            'be appointed by the head of Kai.\n'
                            '   viii. Should the head of Kai be unable to oversee the Kai committee their position and duties shall be '
                            'temporarily filled by the Executive Vice President.\n'
                            '   ix. Should all previous measures be inadequate to fill the recused positions and duties of the head of '
                            'Kai will be given to the President.\n'
                            '   x. If all previous measures remain inadequate, the issue is escalated to a Trial of Officers.\n\n'
                            'b. Appeals:\n'
                            '   i. Kai Committee decisions can be appealed first to the chapter, then to the District Chief, and then '
                            'to the Board of Trustees and the General Convention if needed. As outlined in the General Fraternities\' '
                            'Constitution all Kai Committee appeals must be made within 10 days from the date of notice of a decision.\n\n'
                            'c. Trial of Officers:\n'
                            '   i. When Used: A Trial of Officers should be used when Article VII, Section 1 (a) of the Bylaws fails to '
                            'reach a resolution, or if a member of the Executive Board has charges brought against them that may warrant '
                            'their removal from office.\n'
                            '   ii. Voting Rights: Accused members do not have voting rights during the trial.\n'
                            '   iii. This trial should also be used if a member of the Executive Board needs to be removed from office or '
                            'if charges are brought against them that warrant such penalties.\n'
                            '   iv. The following are reasons why an executive office might need to be removed from their office, reasons '
                            'are not limited to:\n'
                            '      1. Neglect of Duties — An officer who consistently fails to perform their duties, such as not attending '
                            'meetings, failing to complete tasks, or neglecting important responsibilities.\n'
                            '      2. Violation of the Constitution or Bylaws — If a member breaches the rules or policies set in this '
                            'Constitution or Bylaws, the Code of Beta Theta Pi, or the Beta Theta Pi Risk Management policy. Examples can '
                            'include, but are not limited to, misuse of funds, disregard of established procedures, or abuse of authority.\n'
                            '      3. Misconduct or Unethical Behavior — If a member engages in unethical behavior, misconduct, or actions '
                            'that damage the image or reputation of either this chapter or Beta Theta Pi as an organization. Examples can '
                            'include, but are not limited to, harassment of any person, discrimination of any kind, or engaging in illegal activities.\n'
                            '      4. Conflict of Interest — If an officer is found to have a conflict of interest that compromises their '
                            'ability to perform their duties objectively.\n'
                            '      5. Insubordination — If a member refuses to follow directives or decisions made by higher governing bodies.\n'
                            '      6. Incapacity — If an officer is unable to fulfill their duties due to illness, injury, or other personal '
                            'circumstances. This must be done with sensitivity, recognizing the personal challenges faced by the officer.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Brotherhood Committee',
                        'content': (
                            'i. The Brotherhood Committee may include but is not limited to: Ritual Chair and Chorister, chaired by the '
                            'Vice President of Brotherhood.\n'
                            'ii. Responsible for organizing events that foster strong and positive brotherhood.\n'
                            'iii. Ensuring that chapter meetings and operations develop an atmosphere of genuine brotherhood.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Recruitment Committee',
                        'content': (
                            'i. Recruitment Committee Selection:\n'
                            '   1. Should be made up of an odd number of members, containing no less than five (5) members and no more '
                            'than nine (9) members plus the VP of Recruitment and President.\n'
                            '   2. The Vice President of Recruitment selects the committee membership, the President approves or disapproves '
                            'the members appointed. The VP of Recruitment should give a list of names that include two (2) alternate picks, '
                            'in case of a member declining an invitation to join the committee, a member leaving the committee, or a member '
                            'being dismissed from the committee. The alternate members do not need to be notified they were selected as '
                            'alternates unless a member of the primary selection declined to join the committee or leaves the committee at any point.\n'
                            '   3. The previous Vice President of Recruitment should be considered as long as they are a Good Standing active '
                            'member of the chapter.\n'
                            '   4. The Recruitment Committee\'s membership should reflect the different aspects and views of the chapter\'s members.\n'
                            '   5. Process for removing members of the Committee:\n'
                            '      a. Valid reason presented by the VP of Recruitment.\n'
                            '      b. Must be a hearing within the Kai Committee.\n'
                            '      c. After a hearing with the Kai Committee the Recruitment Committee will deliberate and decide by a '
                            'supermajority vote as to whether or not they should remove the member from the committee.\n'
                            '      d. Final decision is still within the Recruitment Committee\'s discretion.\n\n'
                            'ii. Meetings:\n'
                            '   1. The VP of Recruitment is the chair of the Recruitment Committee at all Recruitment Committee meetings.\n'
                            '   2. Prior to the beginning of a discussion about a PNM the Recruitment Committee chair must set a maximum '
                            'time limit for each speaker.\n'
                            '   3. Roberts Rules of Order must be strictly followed during all meetings of the committee.'
                        ),
                    },
                    {
                        'number': '4',
                        'title': 'Education Committee',
                        'content': (
                            'i. The chapter will follow the Standard New Member Orientation program as provided by the General Fraternity.\n'
                            'ii. Organize annual chapter-wide education regarding critical operational topics.\n'
                            'iii. Implementing the chapter\'s individual member development program including promoting attendance at '
                            'local and General Fraternity development programs.\n'
                            'iv. The Education Committee may include but is not limited to: Assistant New Member Educator, Member Education '
                            'Chair, Leadership and Development Chair, Diversity and Equity Chair, Scholarship Chair, Ritual Chair, and be '
                            'chaired by the Vice President of Education.'
                        ),
                    },
                    {
                        'number': '5',
                        'title': 'Risk Management Committee',
                        'content': (
                            'i. The Risk Management Committee may include but is not limited to: Social Chair and Wellness Chair, and '
                            'chaired by the Vice President of Risk Management.\n'
                            'ii. Ensures Beta Theta Pi\'s Risk Management Policy and Samford Universities Risk Management is implemented '
                            'at all events.\n'
                            '   1. If Samford University and General Fraternity policy conflict the chapter should follow the stricter of '
                            'the two documents.\n'
                            'iii. Educate members of Beta Theta Pi\'s Risk Management Policy.\n'
                            'iv. Maintain and educate members on crisis management policy.\n'
                            'v. Monitor and address all fire, health, and safety issues regarding member housing.\n'
                            'vi. Proactively address the issue of substance abuse through education or intervention.\n'
                            '   1. Provide members with various risk management programming.'
                        ),
                    },
                    {
                        'number': '6',
                        'title': 'Finance Committee',
                        'content': (
                            'i. The Finance Committee may include but is not limited to: Fundraising Chairman, chaired by the Vice '
                            'President of Finance.\n'
                            'ii. Preparing the annual budget and managing budget adherence.\n'
                            'iii. Issuing bills to members and collecting all fees.\n'
                            'iv. Paying all housing, local vendor, and General Fraternity bills promptly.\n'
                            'v. Maintaining complete financial records for the chapter.\n'
                            'vi. Supervising any fundraising efforts of the chapter.\n'
                            'vii. Overseeing housing operations including repair and cleaning costs.'
                        ),
                    },
                    {
                        'number': '7',
                        'title': 'Administration Committee',
                        'content': (
                            'i. The Administration Committee may include but is not limited to: Marketing Chair, Alumni Relations Chair, '
                            'and Archivist/Historian, chaired by the Vice President of Administration.\n'
                            'ii. Ensuring the chapter is up to date on all information by sending announcements to chapter members and '
                            'advisors in a timely manner.\n'
                            'iii. Submitting all membership and outline reporting to the General Fraternity within the established timeframe.\n'
                            'iv. Completing end of year reporting and award applications which include photos, press releases and letters '
                            'of recommendation by the required deadline.\n'
                            'v. Publishing periodic alumni and parent newsletters and distributing them to the appropriate audiences.\n'
                            '   1. A copy of each newsletter will be submitted to the Administrative Office.\n'
                            'vi. Planning and executing alumni events on a regular basis.\n'
                            'vii. Managing and producing social media posts that establish and maintain a positive brand for the chapter.\n'
                            'viii. Documenting chapter events and activities including, but not limited to, taking photos.'
                        ),
                    },
                    {
                        'number': '8',
                        'title': 'Programming Committee',
                        'content': (
                            'i. The Programming Committee may include but is not limited to: Service Chair, Philanthropy Chair, '
                            'Intramurals Chair and Special Events Chair, chaired by the Vice President of Programming.\n'
                            'ii. Scheduling and organizing intramural activities, social activities, community service, philanthropic '
                            'activities, and any other special events. Organizing participation in community-wide competitions such as '
                            'Greek Week/Homecoming.'
                        ),
                    },
                    {
                        'number': '9',
                        'title': 'Ritual Committee',
                        'content': (
                            'i. Ritual Committee Membership is to consist of the following:\n'
                            '   1. President\n'
                            '   2. VP of Brotherhood\n'
                            '   3. VP of Education\n'
                            '   4. Ritual Chair\n'
                            '   5. Chorister\n'
                            '   6. 2 other members of the chapter chosen by the Ritual Chair\n'
                            'ii. Ritual Committee is to specialize in memorization of the rituals, to a reasonable extent, and to be '
                            'able to teach the rituals to fellow brothers when necessary.\n'
                            'iii. When the Ritual Chair appoints the two (2) other members to the committee, the complete membership of '
                            'the committee must be presented to the chapter, however it does not need to be voted upon.'
                        ),
                    },
                    {
                        'number': '10',
                        'title': 'Constitution and Bylaws Committee',
                        'content': (
                            'a. Purpose:\n'
                            '   i. To put brothers in a position where they can observe any issues the chapter may have. If the issue '
                            'can be solved or is being caused by the constitution or bylaws this committee places these brothers in a '
                            'position where they can write new amendments to attempt to fix the issue(s) befalling the chapter.\n\n'
                            'b. Member Requirements:\n'
                            '   i. The committee will be comprised of exactly 2 members of the current executive board.\n'
                            '   ii. The committee does not need to be active at all times and only the Committee Chair needs to be filled '
                            'at all times. This being by a member elected to the position or by the EVP.\n'
                            '   iii. The committee should have a minimum membership of 5 currently active members of the chapter; the '
                            'exact number of members is discretionary by the Committee Chair.\n'
                            '   iv. Members of the committee should give a fair and accurate representation of the chapter and should '
                            'represent the diversity of its thoughts and ideas.\n\n'
                            'c. Duties and Responsibilities:\n'
                            '   i. All amendments, to the constitution or bylaws, that are made by the committee must pass a unanimous '
                            'vote from the committee, this vote occurring when all voting members of the committee are present.\n'
                            '      1) All members of the committee have full veto power over an amendment proposed or written by the '
                            'committee. This veto is only viable while the amendment is being passed by the committee, this being prior '
                            'to the amendment being brought to the Executive Board or to the chapter.\n'
                            '      2) When an amendment passes the committee, the amendment then follows the path of all proposed '
                            'amendments as defined in the bylaws.\n'
                            '      3) No amendments or changes to the Constitution or Bylaws can be made by the committee without the '
                            'amendment passing all requirements laid out in the bylaws.\n'
                            '   ii. The Executive Board can petition the committee to make or draft an amendment at any point.\n'
                            '   iii. The chapter or any individual members can petition the committee to make, draft, or revise an '
                            'amendment at any point.\n'
                            '   iv. The Constitution and Bylaws Committee can be petitioned to draft or review a resolution.\n'
                            '      1) Should the committee review a resolution brought, or written, by any non-committee member of the '
                            'chapter the committee should work with the resolution\'s author to make good faith amendments as needed.\n'
                            '      2) The committee should consider giving an endorsement of any resolutions brought to it by any member '
                            'after discussions and amendments are made.\n\n'
                            'd. The Constitution and Bylaws Committee is only required to post its minutes to the chapter at the end of '
                            'each semester. All minutes taken at all meetings should be published in an unredacted form.'
                        ),
                    },
                ],
            },
            {
                'number': 'VIII',
                'title': 'Committee Chair Details',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Committee Chair Responsibilities',
                        'content': (
                            '1. Social Chair\n'
                            '   a. Plans and coordinates social events for the chapter; helps foster engagement among members in '
                            'attending and planning social events.\n'
                            '   b. Responsible for organizing mixers, formals, and other gatherings; works with the VP of Risk '
                            'Management to ensure all events comply with chapter and university policies.\n\n'
                            '2. Tabling Chair\n'
                            '   a. Organizes and oversees all tabling efforts for the chapter, ensuring a strong presence during '
                            'recruitment drives, campus events, and philanthropy promotions.\n'
                            '   b. Coordinates schedules, materials, and member participation to effectively engage with students and '
                            'represent the chapter to the broader campus community.\n\n'
                            '3. Ritual Chair\n'
                            '   a. Rituals of the ritual chair fall under two categories, Education and Brotherhood. The Ritual Chair '
                            'will work with the respective Vice Presidents to complete these duties:\n'
                            '      i. Run all rituals for our chapter, in all ways.\n'
                            '      ii. Run the ritual committee.\n'
                            '      iii. Ensure that we have dates for all rituals at least a month in advance.\n'
                            '      iv. Ensure that we have everything we need for our rituals.\n'
                            '      v. Ensure that people are prepared for rituals.\n'
                            '      vi. Teach the chapter on the history and importance of the rituals and all parts of them.\n'
                            '      vii. Walk the chapter through the rituals prior to doing them.\n'
                            '      viii. Ensure that the chapter has practiced rituals before they are done.\n\n'
                            '4. Historian & Archivist Chair\n'
                            '   a. Preserves the chapter\'s history by documenting events, maintaining records, and organizing '
                            'historical materials.\n'
                            '   b. Oversees the chapter\'s archives and ensures the continuity of traditions and milestones through '
                            'proper record-keeping.\n\n'
                            '5. Health & Wellness Chair\n'
                            '   a. Set up hours for brothers to be able to come and talk with said wellness chair.\n'
                            '   b. Assist with the planning and setup of programming events.\n\n'
                            '6. Intramural Chair\n'
                            '   a. Organize intramural teams within the fraternity if brothers are interested.\n'
                            '   b. Assist with the planning and setup of programming events.\n\n'
                            '7. Service Chair\n'
                            '   a. Assist with the planning and setup of programming events.\n\n'
                            '8. Philanthropy Chair\n'
                            '   a. Keep ties with philanthropy.\n'
                            '   b. Assist with the planning and setup of programming events.\n\n'
                            '9. Chaplain\n'
                            '   a. Provides moral and spiritual guidance to the chapter.\n'
                            '   b. May choose to host optional events for brothers to attend; this may include but is not limited to a '
                            'weekly Bible Study.\n\n'
                            '10. DEI Chair\n'
                            '   a. Advocates for an inclusive and welcoming chapter environment. They lead initiatives that promote '
                            'diversity, ensure equitable treatment of all members, and educate the chapter on issues related to social '
                            'justice and inclusion.\n\n'
                            '11. Chorister\n'
                            '   a. Leads and organizes the musical traditions of the chapter, including songs and hymns for chapter '
                            'meetings, rituals, and events. They work to preserve and teach these traditions to ensure their continuity.\n\n'
                            '12. Scholarship Chair\n'
                            '   a. Uses resources of the chapter, university, or general fraternity to find scholarship opportunities '
                            'for all members of the brotherhood.\n'
                            '   b. Can work with the VP of Education to create opportunities to help brothers improve academic standing '
                            'or performance.\n\n'
                            '13. Kai Committee Chair\n'
                            '   a. The Kai Committee Chair is responsible for convening committee meetings, scheduling them as needed, '
                            'and ensuring all chapter members have easy access to a Kai Referral Form.\n'
                            '   b. The Chair presides over all Kai Committee meetings unless circumstances require another individual to '
                            'assume this role.\n'
                            '   c. When a member is referred to the Kai Committee, the Chair is responsible for contacting the individual '
                            'and ensuring their attendance at the meeting.\n'
                            '   d. Should the Kai Committee choose to elect someone who is not the VP of Brotherhood to the position, the '
                            'committee must have a member of the committee announce the chair at a normally scheduled chapter meeting.\n'
                            '   e. The default Kai Committee Chair is the Vice President of Brotherhood. However, the Kai Committee may '
                            'elect another member of the Executive Board to serve as Chair if deemed necessary by the committee.\n'
                            '      i. The Chair must be a member of the current Executive Board.\n'
                            '      ii. Members eligible for election must accept a nomination from a member of the committee to be '
                            'considered as a candidate for the position.\n'
                            '      iii. If the committee elects someone other than the Vice President of Brotherhood as Chair, a delegated '
                            'committee member must formally announce the new Chair at a regularly scheduled chapter meeting.\n'
                            '      iv. If the Kai Committee fails to elect a Chair within one week of the committee\'s membership being '
                            'confirmed by the chapter, the Vice President of Brotherhood will automatically assume the position of Chair.\n'
                            '      v. Once the Kai Committee elects a Chair, neither the Executive Board nor the chapter is required to '
                            'confirm the appointment. However, if the committee chooses, they may poll the chapter for feedback on the '
                            'appointment when presenting the new Chair at a chapter meeting.\n\n'
                            '14. Marketing & Advertising Chair\n'
                            '   a. Manages the chapter\'s public image by creating promotional materials, managing social media accounts, '
                            'and coordinating outreach efforts.\n'
                            '   b. Works to highlight the chapter\'s achievements and events to both internal and external audiences.\n\n'
                            '15. Constitution and Bylaws Chair\n'
                            '   a. The Constitution and Bylaws Committee will be headed by the Constitution and Bylaws Committee Chair. '
                            'If the position has not had someone appointed or elected to that position, then the duties and responsibilities '
                            'fall to the EVP.\n'
                            '   b. It is the duty of the Constitution and Bylaws Committee Chair to place members on the committee.\n'
                            '   c. When an amendment is brought to the chapter, from the Constitution and Bylaws Committee, the '
                            'Constitution and Bylaws Committee Chair will brief the chapter about the amendment and put the motion on the '
                            'floor to vote to pass the amendment.\n'
                            '   d. Know and understand the Constitution and Bylaws of the chapter.\n'
                            '   e. Know and understand the Code of Beta Theta Pi.\n'
                            '   f. Communicate with the Ritual Chair to ensure that all amendments, whether drafted or proposed, remain '
                            'consistent and do not interfere with Ritual or any proceedings described in the Ritual Book(s).\n'
                            '   g. Ensure that all committee, chapter, and special meetings follow the constitution and bylaws and its policies.'
                        ),
                    },
                ],
            },
            {
                'number': 'IX',
                'title': 'Advisors',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Membership',
                        'content': (
                            '1. An advisory team with a recommended minimum of five (5) members shall serve as coaches for the Executive '
                            'Board and shall provide general counsel for the chapter.\n'
                            '2. The Advisory team and its membership should be made up of the following:\n'
                            '   a. Core Advisors:\n'
                            '      i. Chapter Counselor – advising the President and Executive Vice President & IFC Delegate and '
                            'coordinating the advisory team.\n'
                            '      ii. Recruitment Advisor – advising the Vice President of Recruitment and Recruitment Committee.\n'
                            '      iii. Finance Advisor – advising the Vice President of Finance and Finance Committee.\n'
                            '      iv. Risk Management Advisor – advising the Vice President of Risk Management and Risk Management Committee.\n'
                            '      v. Member Education Advisor – advising the Vice President of Education and Education Committee.\n'
                            '   b. Additional Advisors:\n'
                            '      i. Brotherhood Advisor – advising the Vice President of Brotherhood, the Kai Committee and the '
                            'Brotherhood Committee.\n'
                            '      ii. Administration Advisor – advising the Vice President of Administration and Administration Committee.\n'
                            '      iii. Programming Advisor – advising the Vice President of Programming and Programming Committee.\n'
                            '      iv. Other – other advisors or advisory positions may include, but are not limited to: Philanthropy, '
                            'Service, Social, Alumni Relations, and Lore.\n'
                            '   c. General Fraternity Officers and Administrative Office staff have the primary responsibility to recruit '
                            'and fill vacancies on the advisory team to meet Samford University and General Fraternity requirements. '
                            'The chapter may assist in nominating potential advisors to fill any vacancies.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Purpose',
                        'content': (
                            'The purpose of this advisory team is to provide advice and assistance in chapter operations and development '
                            'of culture and identity, as well as a sustained link between multiple Executive Boards.'
                        ),
                    },
                ],
            },
            {
                'number': 'X',
                'title': 'Amendments to the Bylaws',
                'sections': [
                    {
                        'number': '1',
                        'title': 'Proposals',
                        'content': (
                            '1. Proposed amendments to these Bylaws must be submitted in writing to the chapter\'s Executive Board '
                            'for initial approval.\n'
                            '   a. This approval is done by a simple majority vote in favor of the amendment from the Executive Board.\n'
                            '2. Should the initial approval from the Executive Board fail, and the member still wishes to pursue the '
                            'amendment, the member can bring a motion to amend at the next chapter meeting.\n'
                            '3. Proposals that fail a chapter vote cannot be reintroduced to the chapter or Executive Board until '
                            '10-chapter meetings pass.\n'
                            '   a. All Articles, Sections, and Subsections included in a failed amendment are granted "Protected Status" '
                            'until the designated time period expires.\n'
                            '   b. During this protected period, no amendments may be proposed or passed that would alter any Article, '
                            'Section, or Subsection under Protected Status.\n'
                            '   c. "Protected Status" is defined as a restriction that prevents changes, including edits, additions, or '
                            'deletions, to the content of the affected Articles, Sections, or Subsections.\n'
                            '   d. The Vice President of Administration is responsible for recording the relevant dates.\n'
                            '   e. The Constitution and Bylaws Chair and the Vice President of Administration are responsible for ensuring '
                            'a proposal does not impinge on anything with a Protected Status.'
                        ),
                    },
                    {
                        'number': '2',
                        'title': 'Approval of Amendments',
                        'content': (
                            'Amendments must be approved by a simple majority of the active members present at a chapter, or special '
                            'meeting, where a quorum is present.'
                        ),
                    },
                    {
                        'number': '3',
                        'title': 'Amendment Authority',
                        'content': (
                            '1. All amendments in these Bylaws are subordinate to the Code of Beta, the Interfraternity Council, '
                            'Samford University, and any applicable municipal, state, or federal laws.\n'
                            '2. If any amendment in these Bylaws conflicts with the rules or regulations of any aforementioned '
                            'organizations or authorities, that amendment will be considered null and void.\n'
                            '3. Members cannot be held liable to amendments that are nulled or voided and charges cannot be brought '
                            'against a member for failing to follow such amendment.'
                        ),
                    },
                ],
            },
        ],
    },
    {
        'doc_type': 'appendix',
        'title': 'Appendix',
        'display_order': 30,
        'articles': [
            {
                'number': '1',
                'title': 'Executive Interest Form — Required Fields',
                'sections': [
                    {
                        'number': '1',
                        'title': '',
                        'content': (
                            'The Executive Interest Form must be completed by any member wishing to be considered for an '
                            'Executive Board position. The form must be made available to all active members, and notice of '
                            'its publication must be sent to the full chapter. The form must include the following fields:\n\n'
                            '1. Full Name\n'
                            '2. Member ID (roll number)\n'
                            '3. Current GPA and Academic Standing Level (see Section 2 of the Appendix)\n'
                            '4. Position(s) of Interest — the member must list the position(s) he is applying for, in order of preference\n'
                            '5. Current Semester and Expected Graduation Date — to confirm he will not graduate before the end of the term\n'
                            '6. Outstanding Fines or Dues — disclosure of any outstanding financial obligations to the chapter or General Fraternity\n'
                            '7. Outstanding Kai Cases — disclosure of any pending or unresolved Kai Committee cases\n'
                            '8. Relevant Leadership Experience — a brief summary of past leadership roles, inside or outside the chapter\n'
                            '9. Statement of Interest — a brief written statement (no more than one page) describing why the member is '
                            'seeking the position(s) and what he hopes to accomplish during his term\n\n'
                            'The Slating Committee Chair (President, or his designated replacement per Article II, Section 2 (2)) is '
                            'responsible for collecting completed forms and distributing them to Slating Committee members prior to interviews.'
                        ),
                    },
                ],
            },
            {
                'number': '2',
                'title': 'GPA Level Groupings for Slating',
                'sections': [
                    {
                        'number': '1',
                        'title': '',
                        'content': (
                            'For the purposes of officer slating, all applicants are assigned to one of three (3) academic levels based on '
                            'their current GPA. The Slating Committee must prioritize Level 1 applicants before considering Level 2 or Level 3. '
                            'The levels are defined as follows:\n\n'
                            'Level 1 — Good Academic Standing\n'
                            '   A member is in Level 1 if his GPA is at or above the All Men\'s Average (AMA) at Samford University '
                            'or a 3.0, whichever is higher. Members in Level 1 are given first consideration by the Slating Committee '
                            'before any members in Level 2 or Level 3 are evaluated.\n\n'
                            'Level 2 — Academic Warning\n'
                            '   A member is in Level 2 if his GPA falls within 0.2 grade points of the AMA or between a 2.8 and a 2.99, '
                            'whichever is higher. The Slating Committee may consider Level 2 applicants only after exhausting Level 1 '
                            'candidates for a given position. A supermajority vote of the Slating Committee is required to bypass a '
                            'Level 2 applicant (per Article II, Section 2 (7)(c)(i)).\n\n'
                            'Level 3 — Academic Probation\n'
                            '   A member is in Level 3 if his GPA falls more than 0.2 grade points below the AMA or below a 2.8, '
                            'whichever represents the stricter standard. This includes members on Probation One or Probation Two as defined '
                            'in Article III, Section 4 of the Bylaws. Level 3 applicants are considered only after all Level 1 and Level 2 '
                            'options have been exhausted. A supermajority vote of the Slating Committee is required to bypass a Level 3 '
                            'applicant.\n\n'
                            'Note: Regardless of academic level, all applicants must also satisfy the universal eligibility requirements '
                            'listed in Article II, Section 2 (7)(c)(iv) of the Bylaws (no outstanding fines or dues, no outstanding Kai '
                            'cases, active during the following term, and not graduating before the term ends).'
                        ),
                    },
                ],
            },
        ],
    },
]
