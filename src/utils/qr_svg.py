"""
Shared QR-code-to-SVG rendering.

Used by both the officer-only check-in image endpoint and the public embed
endpoint (src/view/officer/event_attendance.py's qr_checkin_image,
src/view/event_checkin.py's event_checkin_embed_image) — the same technique
src/view/two_factor.py already uses for the TOTP enrolment QR. One function
so there is exactly one place that knows how a URL becomes a QR image,
rather than the same six lines copied into a second view.
"""
import io

import qrcode
import qrcode.image.svg


def render_qr_svg(data):
    """`data` (a URL, typically) rendered as an SVG QR code, as raw bytes."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    img.save(stream)
    return stream.getvalue()


#: Shown by the embed endpoint when no check-in window is currently open —
#: an embed link is pasted into a slide deck once and reused every week, so
#: it needs SOMETHING to display between meetings and before an officer opens
#: a window, rather than a broken-image icon.
WAITING_PLACEHOLDER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="280" height="280" viewBox="0 0 280 280">
  <rect width="280" height="280" fill="#f3f4f6"/>
  <rect x="8" y="8" width="264" height="264" fill="none" stroke="#d1d5db" stroke-width="2" stroke-dasharray="10,6"/>
  <text x="140" y="130" text-anchor="middle" font-family="sans-serif" font-size="16" fill="#6b7280">Check-in</text>
  <text x="140" y="154" text-anchor="middle" font-family="sans-serif" font-size="16" fill="#6b7280">not open yet</text>
</svg>
"""
