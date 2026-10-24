# SPDX-License-Identifier: AGPL-3.0-or-later
from app.mail.text import REPLY_MARKER, clean_subject, find_ticket_id, strip_quoted


def test_find_ticket_id():
    assert find_ticket_id("RE: [#4821] Switch port flapping") == 4821
    assert find_ticket_id("Switch port #4821 flapping") is None
    assert find_ticket_id("no tag") is None


def test_clean_subject():
    assert clean_subject("RE: FW: [#12] Printer down") == "Printer down"
    assert clean_subject("AW: Re:  Printer   down") == "Printer down"
    assert clean_subject("Re: ") == "(no subject)"
    # Only leading prefixes are prefixes.
    assert clean_subject("Area: 2nd floor") == "Area: 2nd floor"


def test_strip_at_our_marker():
    body = f"Still broken.\n\n{REPLY_MARKER}\n\nJ. Park wrote: try a reboot"
    assert strip_quoted(body) == "Still broken."


def test_strip_outlook_header_block():
    body = (
        "Rebooted, no change.\r\n\r\n"
        "________________________________\r\n"
        "From: Service Desk <helpdesk@corp.local>\r\n"
        "Sent: Monday, 6 October 2026 09:12\r\n"
        "To: R. Alvarez\r\n"
        "Subject: [#4821] Switch port flapping\r\n\r\n"
        "Please reboot."
    )
    assert strip_quoted(body) == "Rebooted, no change."


def test_strip_outlook_without_separator_line():
    body = "Thanks!\n\nFrom: Service Desk <helpdesk@corp.local>\nSent: Monday\nTo: me\n\nold"
    assert strip_quoted(body) == "Thanks!"


def test_strip_gmail_style_and_angle_quotes():
    body = "Works now.\n\nOn Mon, 6 Oct 2026 at 09:12, Service Desk <helpdesk@corp.local>\nwrote:\n> Please reboot."
    assert strip_quoted(body) == "Works now."
    assert strip_quoted("Yes\n> did you reboot?\n> thanks") == "Yes"


def test_original_message_separator():
    assert strip_quoted("ok\n-----Original Message-----\nFrom: x") == "ok"


def test_from_line_alone_is_not_a_quote():
    body = "From: the 2nd floor, all desks drop.\nIt started today."
    assert strip_quoted(body) == body


def test_everything_quoted_keeps_original():
    body = f"{REPLY_MARKER}\nold stuff"
    assert strip_quoted(body) == body
