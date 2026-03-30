#!/usr/bin/env python3
"""
Refresh reservations in data.json by parsing SpotHopper emails from Gmail.
Runs hourly via GitHub Actions to keep reservation data current.
"""

import imaplib
import email
import json
import re
import os
import sys
from datetime import datetime, timedelta
from email.header import decode_header

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
DATA_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.json")

# How many days back to search for reservation emails
LOOKBACK_DAYS = 7


def connect_gmail():
    """Connect to Gmail via IMAP."""
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")
    return mail


def search_spothopper_emails(mail, since_date):
    """Search for SpotHopper reservation emails since a given date."""
    date_str = since_date.strftime("%d-%b-%Y")
    # Search for new reservation requests
    _, new_ids = mail.search(None, f'(FROM "spothopperapp.com" SUBJECT "New Reservation Request" SINCE {date_str})')
    # Search for cancellations
    _, cancel_ids = mail.search(None, f'(FROM "spothopperapp.com" SUBJECT "canceled" SINCE {date_str})')
    # Search for confirmed (to catch updates in threads)
    _, confirm_ids = mail.search(None, f'(FROM "spothopperapp.com" SUBJECT "Reservation Confirmed" SINCE {date_str})')
    return {
        "new": new_ids[0].split() if new_ids[0] else [],
        "canceled": cancel_ids[0].split() if cancel_ids[0] else [],
        "confirmed": confirm_ids[0].split() if confirm_ids[0] else [],
    }


def get_email_body(msg):
    """Extract plain text body from email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
                except:
                    pass
            elif ctype == "text/html" and not body:
                try:
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                except:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except:
            body = str(msg.get_payload())
    return body


def parse_new_reservation(body, subject=""):
    """Parse a SpotHopper 'New Reservation Request' email body."""
    rez = {}

    # DATE REQUESTED
    m = re.search(r"DATE REQUESTED\s+(.+?)(?:\s{2,}|HEADCOUNT)", body, re.DOTALL)
    if m:
        rez["date_raw"] = m.group(1).strip()

    # HEADCOUNT
    m = re.search(r"HEADCOUNT\s+(\d+)", body)
    if m:
        rez["guests"] = int(m.group(1))

    # NAME
    m = re.search(r"NAME\s+(.+?)(?:\s{2,}|EMAIL)", body, re.DOTALL)
    if m:
        rez["name"] = m.group(1).strip()

    # PHONE
    m = re.search(r"PHONE\s+\(?(\d{3})\)?\s*[\-\.]?\s*(\d{3})\s*[\-\.]?\s*(\d{4})", body)
    if m:
        rez["phone"] = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
    else:
        rez["phone"] = ""

    # SPACE
    m = re.search(r"SPACE\s+(\w+)", body)
    if m:
        rez["space"] = m.group(1).strip()

    # STATUS
    m = re.search(r"STATUS\s+(\w+)", body)
    if m:
        rez["status"] = m.group(1).strip().lower()
    else:
        rez["status"] = "confirmed"

    # DESCRIPTION
    m = re.search(r"DESCRIPTION\s+(.+?)(?:\s{2,}View Request|$)", body, re.DOTALL)
    if m:
        desc = m.group(1).strip()
        # Clean up whitespace
        desc = re.sub(r"\s+", " ", desc)
        rez["notes"] = desc
    else:
        rez["notes"] = ""

    # Parse the date and time from date_raw
    if "date_raw" in rez:
        rez.update(parse_date_time(rez["date_raw"]))

    rez["source"] = "SpotHopper"
    return rez


def parse_date_time(date_raw):
    """Parse date string like 'Tuesday, Mar 31st, 2026 at 4:30 PM'."""
    result = {}
    # Remove ordinal suffixes
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_raw)
    # Try to extract time
    time_match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", cleaned, re.IGNORECASE)
    if time_match:
        result["time"] = time_match.group(1).strip().upper()

    # Try to parse full date
    # Format: "Tuesday, Mar 31, 2026 at 4:30 PM"
    date_match = re.search(r"(\w+,\s+\w+\s+\d+,?\s+\d{4})", cleaned)
    if date_match:
        date_str = date_match.group(1)
        for fmt in ["%A, %b %d, %Y", "%A, %b %d %Y", "%a, %b %d, %Y", "%a, %b %d %Y"]:
            try:
                dt = datetime.strptime(date_str, fmt)
                result["date_iso"] = dt.strftime("%Y-%m-%d")
                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                result["date_display"] = f"{days[dt.weekday()]}, {months[dt.month-1]} {dt.day}"
                break
            except ValueError:
                continue

    return result


def parse_cancellation(body, subject=""):
    """Parse a SpotHopper cancellation email."""
    # "The reservation scheduled for Monday, Mar 30th, 2026 at 4:00 PM has been canceled by Raychel Traubman"
    m = re.search(
        r"reservation scheduled for (.+?) has been canceled by (.+?)(?:\s+on|\s*\.|\s*$)",
        body, re.IGNORECASE
    )
    if m:
        date_time_raw = m.group(1).strip()
        name = m.group(2).strip()
        result = {"name": name, "canceled": True}
        result.update(parse_date_time(date_time_raw))
        return result
    return None


def load_data_json():
    """Load the current data.json file."""
    try:
        with open(DATA_JSON_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading data.json: {e}")
        return None


def save_data_json(data):
    """Save updated data.json file."""
    with open(DATA_JSON_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved data.json with updated reservations")


def build_reservation_data(reservations):
    """Organize reservations into today/tomorrow/upcoming structure."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    today_bookings = []
    tomorrow_bookings = []
    upcoming_bookings = []

    for r in reservations:
        if r.get("canceled"):
            continue
        date_iso = r.get("date_iso", "")
        booking = {
            "time": r.get("time", ""),
            "name": r.get("name", "Unknown"),
            "guests": r.get("guests", 0),
            "status": r.get("status", "confirmed"),
            "source": r.get("source", "SpotHopper"),
            "phone": r.get("phone", ""),
            "notes": r.get("notes", ""),
        }

        if date_iso == today:
            today_bookings.append(booking)
        elif date_iso == tomorrow:
            tomorrow_bookings.append(booking)
        elif date_iso > today:
            booking["date"] = r.get("date_display", date_iso)
            upcoming_bookings.append(booking)

    # Sort by time
    for lst in [today_bookings, tomorrow_bookings, upcoming_bookings]:
        lst.sort(key=lambda x: x.get("time", ""))

    # Format dates for display
    today_dt = datetime.now()
    tomorrow_dt = today_dt + timedelta(days=1)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    today_display = f"{days[today_dt.weekday()]}, {months[today_dt.month-1]} {today_dt.day}"
    tomorrow_display = f"{days[tomorrow_dt.weekday()]}, {months[tomorrow_dt.month-1]} {tomorrow_dt.day}"

    return {
        "lastUpdated": today,
        "source": "All Sources",
        "today": {
            "date": today_display,
            "totalGuests": sum(b["guests"] for b in today_bookings),
            "bookings": today_bookings,
        },
        "tomorrow": {
            "date": tomorrow_display,
            "totalGuests": sum(b["guests"] for b in tomorrow_bookings),
            "bookings": tomorrow_bookings,
        },
        "upcoming": upcoming_bookings,
    }


def main():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD environment variables required")
        sys.exit(1)

    print(f"Connecting to Gmail as {GMAIL_USER}...")
    mail = connect_gmail()

    since = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    print(f"Searching SpotHopper emails since {since.strftime('%Y-%m-%d')}...")
    email_ids = search_spothopper_emails(mail, since)

    reservations = []
    cancellations = []

    # Parse new reservation emails
    print(f"Found {len(email_ids['new'])} new reservation emails")
    for eid in email_ids["new"]:
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        body = get_email_body(msg)
        subject = str(msg.get("Subject", ""))
        rez = parse_new_reservation(body, subject)
        if rez.get("name") and rez.get("date_iso"):
            reservations.append(rez)
            print(f"  + {rez['name']} — {rez.get('date_display', '')} at {rez.get('time', '?')}, {rez.get('guests', '?')} guests")

    # Parse cancellation emails
    print(f"Found {len(email_ids['canceled'])} cancellation emails")
    for eid in email_ids["canceled"]:
        _, msg_data = mail.fetch(eid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        body = get_email_body(msg)
        cancel = parse_cancellation(body)
        if cancel:
            cancellations.append(cancel)
            print(f"  - CANCELED: {cancel['name']}")

    # Apply cancellations
    for cancel in cancellations:
        for rez in reservations:
            if (rez.get("name", "").lower() == cancel.get("name", "").lower() and
                rez.get("date_iso") == cancel.get("date_iso")):
                rez["canceled"] = True
                print(f"  Applied cancellation for {cancel['name']}")

    # Deduplicate by name + date + time (keep latest)
    seen = {}
    for r in reservations:
        key = f"{r.get('name', '').lower()}_{r.get('date_iso', '')}_{r.get('time', '')}"
        seen[key] = r
    reservations = list(seen.values())

    print(f"\nTotal active reservations: {len([r for r in reservations if not r.get('canceled')])}")

    # Load current data.json and update reservations
    data = load_data_json()
    if data is None:
        print("ERROR: Could not load data.json")
        sys.exit(1)

    data["reservations"] = build_reservation_data(reservations)
    save_data_json(data)

    mail.logout()
    print("Done!")


if __name__ == "__main__":
    main()
