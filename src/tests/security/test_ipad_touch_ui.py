"""
09-06-26 — iPad reported as "not well optimized": the tab dropdown menu
didn't open, and the document viewer UI was "really bad."

Both turned out to be the same category of bug — a breakpoint/detection
mismatch that a mouse-and-keyboard desktop or a small phone would never hit,
but a mid-size touch tablet lands right in the gap of.

## Bug 1 — the mobile nav menu could never open from 1024px-1279px

`#mobile-menu-button` (the hamburger) is `xl:hidden` — visible below 1280px.
The panel it toggles, `#mobile-menu`, was `hidden lg:hidden` — CSS forces
`display:none` on it from 1024px up, regardless of the JS `hidden` class
toggle. So any viewport from 1024px-1279px showed a clickable button whose
menu could never actually appear. Most iPads in landscape (regular iPad:
1024px, iPad Air 11": ~1180px) fall exactly in that dead zone; iPad Pro
12.9" landscape (1366px) clears it into the desktop nav instead. Portrait
iPads (768px-834px) were never affected — `lg` is 1024px, well above that.

Fixed by matching the panel's breakpoint to the button's: both `xl:hidden`.

## Bug 2 — iPads got the iframe PDF viewer, which WebKit can't scroll

`view_document.html` renders PDFs two ways: an `<iframe>` for "desktop"
(`hidden md:block`, i.e. shown from 768px up) and pre-rendered page images
for "mobile" (`md:hidden`, shown below 768px) — because WebKit has never
reliably supported scrolling a PDF embedded in an iframe, which is exactly
why the image-rendered path exists. But iPadOS 13+ requests desktop sites by
default, so Safari's UA string on a modern iPad reports as a plain
"Macintosh" — no "iPad" token to detect — and every iPad is comfortably
>=768px wide even in portrait. The `md` viewport check routed every iPad
straight into the one PDF viewer WebKit can't scroll, which is the "document
UI is really bad" symptom.

Fixed with an Apple-touch detection script that overrides the viewport-based
choice: the classic UA match (`/iPad|iPhone|iPod/`) for older/mobile-UA
cases, OR'd with the `navigator.platform === 'MacIntel' &&
navigator.maxTouchPoints > 1` trick, which is what actually catches a modern
iPad's spoofed-as-desktop UA (a real Mac reports `maxTouchPoints === 0`).

These are structural tests (no JS runner in this repo) — they assert the
mechanism is present and wired correctly, the same convention as
`test_bfcache_reload.py::TheBfcacheReloadExistsTests`.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TheMobileMenuBreakpointMatchesItsButtonTests(SimpleTestCase):
    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_toggle_button_is_xl_hidden(self):
        self.assertIn('id="mobile-menu-button" class="xl:hidden', self.base)

    def test_the_panel_hides_at_the_same_breakpoint_as_the_button(self):
        """
        ⚠️ THE REGRESSION THIS GUARDS AGAINST: the panel used to say
        `hidden lg:hidden` (1024px) while the button says `xl:hidden`
        (1280px). Any viewport from 1024px-1279px — most iPads in
        landscape — showed a button that could never open its own menu,
        because CSS forced display:none on the panel from 1024px up no
        matter what the JS `hidden` class toggle did.
        """
        self.assertIn('id="mobile-menu" class="hidden xl:hidden', self.base)
        self.assertNotIn('id="mobile-menu" class="hidden lg:hidden', self.base)

    def test_the_js_toggle_still_targets_both_elements(self):
        self.assertIn("getElementById('mobile-menu-button')", self.base)
        self.assertIn("getElementById('mobile-menu')", self.base)


class TheIpadPdfViewerFixExistsTests(SimpleTestCase):
    def setUp(self):
        self.doc = (Path(settings.BASE_DIR) / 'templates' / 'view_document.html').read_text(encoding='utf-8')

    def test_the_iframe_and_mobile_view_have_stable_ids(self):
        self.assertIn('id="pdf-desktop-iframe"', self.doc)
        self.assertIn('id="pdf-mobile-view"', self.doc)

    def test_it_still_checks_the_classic_ios_user_agent(self):
        self.assertIn(r'/iPad|iPhone|iPod/.test(navigator.userAgent)', self.doc)

    def test_it_also_checks_the_modern_ipados_spoofed_ua_case(self):
        """
        ⚠️ THE PART THAT ACTUALLY MATTERS FOR A REAL IPAD: iPadOS 13+
        reports as "MacIntel" in `navigator.platform`, indistinguishable
        from a real Mac by user agent alone. The `maxTouchPoints > 1`
        check is what tells them apart (a real Mac reports 0). Losing
        this half silently reintroduces the bug for every iPad running a
        current iPadOS, while still looking correct on an older device or
        in a simulator that reports the classic UA string.
        """
        self.assertIn("navigator.platform === 'MacIntel'", self.doc)
        self.assertIn('navigator.maxTouchPoints > 1', self.doc)

    def test_detecting_an_apple_touch_device_removes_the_iframe(self):
        stripped = re.sub(r'<!--.*?-->', '', self.doc, flags=re.DOTALL)
        detect_at = stripped.index('isAppleTouch')
        remove_at = stripped.index("getElementById('pdf-desktop-iframe')")
        self.assertLess(detect_at, remove_at)
        self.assertIn('iframe.remove()', stripped)

    def test_detecting_an_apple_touch_device_reveals_the_prerendered_view(self):
        self.assertIn("mobileView.classList.remove('md:hidden')", self.doc)

    def test_the_prerendered_images_are_always_built_server_side(self):
        """
        Confirms this fix needs no new data: `pdf_images` is computed
        unconditionally for every PDF, not gated on viewport, so the
        mobile-view content this script reveals is already in the DOM —
        this is purely a display-toggle fix, not a data-availability one.
        """
        view_document_py = (
            Path(settings.BASE_DIR) / 'src' / 'view' / 'view_document.py'
        ).read_text(encoding='utf-8')
        self.assertIn('pdf_images = convert_pdf_to_images', view_document_py)

    def test_the_detection_script_is_csp_safe(self):
        """Every inline script in this file uses the request-scoped nonce —
        confirms the new script follows the same convention rather than
        silently needing a CSP exemption."""
        scripts_after_pdf_ids = self.doc.split('id="pdf-mobile-view"', 1)[1]
        self.assertIn('<script nonce="{{ request.csp_nonce }}">', scripts_after_pdf_ids)
