#!/usr/bin/env python3
"""
Brewhouse Morning Report Generator
Reads Toast and Sling emails from Gmail via IMAP and generates the HTML report.

Required environment variables:
  GMAIL_USER         - Gmail address (e.g. mojobrewhouse@gmail.com)
  GMAIL_APP_PASSWORD - Gmail App Password (from Google Account > Security > App Passwords)
"""

import imaplib
import email
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# ──────────────────────────────────────────────
# Gmail connection
# ──────────────────────────────────────────────

GMAIL_USER = os.environ.get('GMAIL_USER', 'mojobrewhouse@gmail.com')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

def connect_gmail():
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    return mail

def decode_subject(raw):
    parts = decode_header(raw or '')
    out = ''
    for part, enc in parts:
        if isinstance(part, bytes):
            out += part.decode(enc or 'utf-8', errors='replace')
        else:
            out += str(part)
    return out

def get_html_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode('utf-8', errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode('utf-8', errors='replace')
    return ''

def fetch_emails(mail, days_back=2):
    """Fetch all emails from the last N days."""
    mail.select('INBOX')
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime('%d-%b-%Y')
    _, ids = mail.search(None, f'SINCE {since}')
    emails = []
    for mid in ids[0].split():
        try:
            _, data = mail.fetch(mid, '(RFC822)')
            msg = email.message_from_bytes(data[0][1])
            emails.append({
                'subject': decode_subject(msg.get('Subject', '')),
                'sender':  msg.get('From', ''),
                'date':    msg.get('Date', ''),
                'body':    get_html_body(msg),
            })
        except Exception as e:
            print(f'  Warning: could not parse email {mid}: {e}')
    return emails

# ──────────────────────────────────────────────
# Toast email parsing
# ──────────────────────────────────────────────

def is_toast_email(e):
    s = e['sender'].lower()
    subj = e['subject'].lower()
    return ('toast' in s or 'toasttab' in s or
            'toast' in subj or 'daily summary' in subj or
            'group summary' in subj or 'performance summary' in subj)

def parse_number(text, pattern):
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).replace(',', '').strip()
    return None

def parse_toast(body):
    data = {}
    if not body:
        return data

    # Strip HTML tags for plain-text searching
    if BS4_AVAILABLE:
        soup = BeautifulSoup(body, 'lxml')
        text = soup.get_text(separator=' ')
    else:
        text = re.sub(r'<[^>]+>', ' ', body)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)

    patterns = {
        'net_sales':   r'Net Sales?\s*\$?\s*([\d,]+\.?\d*)',
        'gross_sales': r'Gross Sales?\s*\$?\s*([\d,]+\.?\d*)',
        'guests':      r'(?:Guests?|Covers?)\s*([\d,]+)',
        'orders':      r'(?:Checks?|Orders?)\s*([\d,]+)',
        'labor_pct':   r'Labor\s*([\d.]+)\s*%',
        'discounts':   r'Discounts?\s*([\d.]+)\s*%',
        'voids':       r'Voids?\s*([\d]+)',
    }

    for key, pat in patterns.items():
        val = parse_number(text, pat)
        if val:
            data[key] = val

    # Derive averages
    try:
        ns = float(data.get('net_sales', 0))
        g  = int(data.get('guests', 0))
        o  = int(data.get('orders', 0))
        if ns and g and 'avg_per_guest' not in data:
            data['avg_per_guest'] = f'{ns/g:.2f}'
        if ns and o and 'avg_per_order' not in data:
            data['avg_per_order'] = f'{ns/o:.2f}'
    except Exception:
        pass

    return data

# ──────────────────────────────────────────────
# Sling email parsing
# ──────────────────────────────────────────────

def is_sling_email(e):
    s = e['sender'].lower()
    subj = e['subject'].lower()
    return ('sling' in s or 'getsling' in s or
            'sling' in subj or 'schedule' in subj or 'shift' in subj)

ROLE_MAP = {
    'general manager': 'role-gm',   'gm': 'role-gm',
    'chef':  'role-chef',  'head chef': 'role-chef',
    'cook':  'role-cook',  'line cook': 'role-cook',  'prep cook': 'role-cook',
    'bartender': 'role-bartender',  'bar': 'role-bartender',
    'server': 'role-server', 'waiter': 'role-server', 'waitress': 'role-server',
    'busser': 'role-busser', 'bus': 'role-busser',
    'host': 'role-server', 'hostess': 'role-server',
}
ROLE_EMOJI = {
    'role-gm': '⭐', 'role-chef': '👨‍🍳', 'role-cook': '🍳',
    'role-bartender': '🍸', 'role-server': '🍽️', 'role-busser': '🧹',
}

def role_class(role_text):
    rl = role_text.lower()
    for k, v in ROLE_MAP.items():
        if k in rl:
            return v
    return 'role-server'

def parse_sling(body):
    data = {'staff': [], 'total_hours': None, 'overtime': 0, 'uncovered': 0}
    if not body or not BS4_AVAILABLE:
        return data

    soup = BeautifulSoup(body, 'lxml')
    text = re.sub(r'\s+', ' ', soup.get_text(separator=' '))

    # Total hours
    m = re.search(r'Total\s+Hours?\s*:?\s*([\d.]+)', text, re.IGNORECASE)
    if m:
        data['total_hours'] = m.group(1)

    # Overtime
    m = re.search(r'Overtime\s*:?\s*([\d]+)', text, re.IGNORECASE)
    if m:
        data['overtime'] = int(m.group(1))

    # Uncovered shifts
    m = re.search(r'Uncovered\s*:?\s*([\d]+)', text, re.IGNORECASE)
    if m:
        data['uncovered'] = int(m.group(1))

    # Staff from tables
    seen = set()
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 2:
                name = cells[0].get_text(strip=True)
                role = cells[1].get_text(strip=True) if len(cells) > 1 else 'Staff'
                # Skip header-like rows
                if (name and len(name) > 2
                        and name.lower() not in ('name', 'employee', 'staff', 'team member')
                        and name not in seen):
                    seen.add(name)
                    data['staff'].append({'name': name, 'role': role})

    # Fallback: look for Name / Role patterns in text
    if not data['staff']:
        matches = re.findall(
            r'([A-Z][a-z]+(?: [A-Z][a-z]+)+)\s+[–\-]\s+([A-Za-z ]+)',
            text
        )
        for name, role in matches:
            if name not in seen:
                seen.add(name)
                data['staff'].append({'name': name, 'role': role.strip()})

    return data

# ──────────────────────────────────────────────
# HTML generation
# ──────────────────────────────────────────────

HTML_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Georgia', serif; background: #1a1008; color: #f0e8d8; min-height: 100vh; }
.header { background: linear-gradient(135deg, #2d1a06, #4a2c0a); border-bottom: 3px solid #c8841a; padding: 24px 32px; display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 1.8rem; color: #f0c060; letter-spacing: 1px; }
.header .date { color: #c8a060; font-size: 1rem; font-family: sans-serif; }
.container { max-width: 900px; margin: 0 auto; padding: 28px 20px; display: flex; flex-direction: column; gap: 24px; }
.card { background: #2a1a08; border: 1px solid #4a3010; border-radius: 12px; overflow: hidden; }
.card-header { padding: 14px 20px; display: flex; align-items: center; gap: 10px; font-size: 1.05rem; font-weight: bold; font-family: sans-serif; letter-spacing: 0.5px; }
.card-body { padding: 18px 20px; }
.card-header .icon { font-size: 1.2rem; }
.alert-red .card-header { background: #5a1010; color: #ff9090; border-bottom: 2px solid #c03030; }
.alert-yellow .card-header { background: #4a3800; color: #ffe080; border-bottom: 2px solid #c09010; }
.alert-green .card-header { background: #0a3a18; color: #80f0a0; border-bottom: 2px solid #20a040; }
.alert-blue .card-header { background: #0a2040; color: #80c0ff; border-bottom: 2px solid #2060c0; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 14px; }
.metric { background: #1a1008; border: 1px solid #4a3010; border-radius: 8px; padding: 12px 14px; text-align: center; }
.metric .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 1px; color: #a08050; font-family: sans-serif; margin-bottom: 6px; }
.metric .value { font-size: 1.4rem; font-weight: bold; color: #f0c060; }
.metric .sub { font-size: 0.75rem; color: #a08050; font-family: sans-serif; margin-top: 3px; }
.metric .up { color: #50e070; }
.metric .down { color: #e05050; }
.staff-table { width: 100%; border-collapse: collapse; font-family: sans-serif; font-size: 0.9rem; }
.staff-table th { background: #1a1008; color: #a08050; text-align: left; padding: 8px 12px; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #3a2510; }
.staff-table td { padding: 9px 12px; border-bottom: 1px solid #3a2510; color: #e0d0b0; }
.staff-table tr:last-child td { border-bottom: none; }
.role-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: bold; }
.role-bartender { background: #2a1040; color: #c080ff; }
.role-chef { background: #1a2800; color: #80e040; }
.role-cook { background: #1a2800; color: #a0e060; }
.role-server { background: #001828; color: #60c0ff; }
.role-busser { background: #281800; color: #f0a040; }
.role-gm { background: #1a0a30; color: #e080ff; }
.section-divider { height: 1px; background: linear-gradient(90deg, transparent, #4a3010, transparent); margin: 4px 0; }
.headline { background: linear-gradient(135deg, #0a2808, #0d3a0a); border: 2px solid #20a040; border-radius: 12px; padding: 20px 24px; text-align: center; }
.headline h2 { font-size: 1.4rem; color: #80f090; margin-bottom: 8px; }
.headline p { color: #b0e0b8; font-size: 0.95rem; line-height: 1.6; font-family: sans-serif; }
.flag-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.flag { padding: 4px 12px; border-radius: 14px; font-size: 0.78rem; font-family: sans-serif; font-weight: bold; }
.flag-good { background: #0a3010; color: #60e080; border: 1px solid #20a040; }
.flag-warn { background: #3a2800; color: #f0c060; border: 1px solid #c09020; }
.flag-bad { background: #3a0a08; color: #ff8070; border: 1px solid #c03020; }
.link-btn { display: inline-block; margin-top: 10px; padding: 7px 18px; background: #4a2c0a; color: #f0c060; border-radius: 6px; text-decoration: none; font-family: sans-serif; font-size: 0.82rem; border: 1px solid #c8841a; }
"""

def fmt_money(val):
    try:
        return f'${float(val):,.0f}'
    except Exception:
        return val or 'N/A'

def fmt_val(val, suffix=''):
    return f'{val}{suffix}' if val else 'N/A'

def build_staff_rows(staff):
    if not staff:
        return '<tr><td colspan="2" style="color:#806040;font-style:italic;">Staff data not yet available — check back after Sling email arrives (~7 AM)</td></tr>'
    rows = ''
    for s in staff:
        cls = role_class(s.get('role', ''))
        emoji = ROLE_EMOJI.get(cls, '👤')
        rows += f'<tr><td>{s["name"]}</td><td><span class="role-badge {cls}">{emoji} {s["role"]}</span></td></tr>\n'
    return rows

def generate_html(toast, sling, report_date):
    today_str   = report_date.strftime('%A, %B %d, %Y').upper()
    month_year  = report_date.strftime('%B %d, %Y')
    yest        = report_date - timedelta(days=1)
    yest_str    = yest.strftime('%A, %B %d')
    yest_short  = yest.strftime('%B %d')

    net     = toast.get('net_sales')
    gross   = toast.get('gross_sales')
    guests  = toast.get('guests')
    orders  = toast.get('orders')
    avg_g   = toast.get('avg_per_guest')
    avg_o   = toast.get('avg_per_order')
    labor   = toast.get('labor_pct')
    disct   = toast.get('discounts')
    voids   = toast.get('voids')

    net_fmt   = fmt_money(net)
    gross_fmt = fmt_money(gross)

    try:
        labor_ok = labor and float(labor) <= 20
    except Exception:
        labor_ok = False
    labor_color = '#50e070' if labor_ok else '#e05050'
    labor_note  = '✓ Excellent' if labor_ok else '⚠ Review'

    staff      = sling.get('staff', [])
    tot_hrs    = sling.get('total_hours') or '—'
    overtime   = sling.get('overtime', 0)
    uncovered  = sling.get('uncovered', 0)
    staff_ct   = len(staff) if staff else '?'
    staff_rows = build_staff_rows(staff)

    labor_flag  = f'✓ Labor {labor}% — Excellent' if labor_ok else f'⚠ Labor {fmt_val(labor, "%")}'
    labor_fcls  = 'flag-good' if labor_ok else 'flag-warn'
    cov_flag    = f'✓ {staff_ct} staff / 0 gaps today' if uncovered == 0 else f'⚠ {uncovered} uncovered shift(s)'
    cov_fcls    = 'flag-good' if uncovered == 0 else 'flag-bad'

    headline_labor = f'{labor}%' if labor else 'see below'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Brewhouse Morning Report — {month_year}</title>
<style>{HTML_STYLE}</style>
</head>
<body>

<div class="header">
  <div>
    <div class="date">{today_str}</div>
    <h1>🍺 Brewhouse Morning Report</h1>
  </div>
  <div style="text-align:right; font-family:sans-serif; font-size:0.85rem; color:#c8a060;">
    Auto-generated from<br>Toast · Sling · Google · Yelp
  </div>
</div>

<div class="container">

  <!-- HEADLINE -->
  <div class="headline">
    <h2>🍺 {yest_str} — {net_fmt} Net</h2>
    <p>Yesterday brought in <strong>{net_fmt}</strong> net with <strong>{fmt_val(guests)} guests</strong>
    through the door. Labor at <strong>{headline_labor}</strong>.
    Today you have <strong>{staff_ct} staff scheduled</strong>.</p>
    <div class="flag-row" style="justify-content:center; margin-top:14px;">
      <span class="flag {labor_fcls}">{labor_flag}</span>
      <span class="flag {cov_fcls}">{cov_flag}</span>
    </div>
  </div>

  <!-- TOAST SALES -->
  <div class="card alert-green">
    <div class="card-header">
      <span class="icon">🍞</span> Toast — {yest_short} Sales
    </div>
    <div class="card-body">
      <div class="metrics-grid">
        <div class="metric">
          <div class="label">Net Sales</div>
          <div class="value">{net_fmt}</div>
          <div class="sub">yesterday</div>
        </div>
        <div class="metric">
          <div class="label">Gross Sales</div>
          <div class="value">{gross_fmt}</div>
          <div class="sub">incl. discounts</div>
        </div>
        <div class="metric">
          <div class="label">Guests</div>
          <div class="value">{fmt_val(guests)}</div>
        </div>
        <div class="metric">
          <div class="label">Orders</div>
          <div class="value">{fmt_val(orders)}</div>
          <div class="sub">{fmt_money(avg_o)} avg/order</div>
        </div>
        <div class="metric">
          <div class="label">Avg / Guest</div>
          <div class="value">{fmt_money(avg_g)}</div>
        </div>
        <div class="metric">
          <div class="label">Labor %</div>
          <div class="value" style="color:{labor_color};">{fmt_val(labor, '%')}</div>
          <div class="sub up">{labor_note}</div>
        </div>
        <div class="metric">
          <div class="label">Discounts</div>
          <div class="value" style="font-size:1.1rem;">{fmt_val(disct, '%')}</div>
          <div class="sub">of gross</div>
        </div>
        <div class="metric">
          <div class="label">Voids</div>
          <div class="value" style="font-size:1.1rem;">{fmt_val(voids, ' items')}</div>
          <div class="sub">review if recurring</div>
        </div>
      </div>
    </div>
  </div>

  <!-- SLING STAFFING -->
  <div class="card alert-blue">
    <div class="card-header">
      <span class="icon">👥</span> Sling — Today's Staff
    </div>
    <div class="card-body">
      <div class="metrics-grid" style="margin-bottom:16px;">
        <div class="metric">
          <div class="label">Scheduled</div>
          <div class="value">{staff_ct}</div>
          <div class="sub">employees</div>
        </div>
        <div class="metric">
          <div class="label">Total Hours</div>
          <div class="value">{tot_hrs}</div>
        </div>
        <div class="metric">
          <div class="label">Overtime</div>
          <div class="value" style="color:{'#50e070' if overtime == 0 else '#e05050'};">{overtime}</div>
          <div class="sub {'up' if overtime == 0 else 'down'}">{'✓ None' if overtime == 0 else '⚠ Review'}</div>
        </div>
        <div class="metric">
          <div class="label">Uncovered</div>
          <div class="value" style="color:{'#50e070' if uncovered == 0 else '#e05050'};">{uncovered}</div>
          <div class="sub {'up' if uncovered == 0 else 'down'}">{'✓ All filled' if uncovered == 0 else '⚠ Fill shifts'}</div>
        </div>
      </div>
      <table class="staff-table">
        <thead><tr><th>Name</th><th>Role</th></tr></thead>
        <tbody>
          {staff_rows}
        </tbody>
      </table>
    </div>
  </div>

  <div style="text-align:center; font-family:sans-serif; font-size:0.75rem; color:#604020; padding-bottom:20px;">
    Brewhouse Morning Report · Auto-generated daily from Toast &amp; Sling emails · {month_year}
  </div>

</div>
</body>
</html>'''

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    report_date = datetime.utcnow()
    print(f'Generating report for {report_date.strftime("%B %d, %Y")}')

    toast_data = {}
    sling_data = {'staff': [], 'total_hours': None, 'overtime': 0, 'uncovered': 0}

    if GMAIL_APP_PASSWORD:
        try:
            print('Connecting to Gmail...')
            mail = connect_gmail()
            all_emails = fetch_emails(mail, days_back=2)
            print(f'Found {len(all_emails)} recent emails')

            for e in all_emails:
                if is_toast_email(e):
                    print(f'  [Toast] {e["subject"][:80]}')
                    parsed = parse_toast(e['body'])
                    for k, v in parsed.items():
                        if v and not toast_data.get(k):
                            toast_data[k] = v

            for e in all_emails:
                if is_sling_email(e):
                    print(f'  [Sling] {e["subject"][:80]}')
                    parsed = parse_sling(e['body'])
                    if parsed['staff']:
                        sling_data = parsed
                        break

            mail.logout()
            print(f'Toast data: {toast_data}')
            print(f'Sling staff count: {len(sling_data["staff"])}')
        except Exception as exc:
            print(f'Gmail error: {exc}')
    else:
        print('No GMAIL_APP_PASSWORD set — generating placeholder report')

    html = generate_html(toast_data, sling_data, report_date)

    # Write to repo root
    out = Path(__file__).parent.parent / 'Brewhouse_Morning_Report.html'
    out.write_text(html, encoding='utf-8')
    print(f'Report written → {out}')

if __name__ == '__main__':
    main()
