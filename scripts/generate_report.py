#!/usr/bin/env python3
"""
Brewhouse Morning Report Generator
- Reads Toast + Sling emails from Gmail via IMAP
- Reads Google Ads report email from Gmail
- Scrapes Yelp Ads dashboard via Playwright
- Generates HTML morning report and writes to repo root
"""

import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ── Gmail connection ──────────────────────────────────────────────────────────

def connect_gmail():
    user = os.environ.get('GMAIL_USER', '')
    pwd  = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not user or not pwd:
        print("WARNING: No Gmail credentials found. Using fallback data.")
        return None
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(user, pwd)
        return mail
    except Exception as e:
        print(f"WARNING: Gmail login failed: {e}. Using fallback data.")
        return None

def fetch_emails(mail, days_back=3):
    """Fetch all emails from the last N days."""
    if not mail:
        return []
    mail.select('inbox')
    since = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
    _, data = mail.search(None, f'(SINCE {since})')
    emails = []
    for num in data[0].split():
        _, msg_data = mail.fetch(num, '(RFC822)')
        msg = email.message_from_bytes(msg_data[0][1])
        subject, enc = decode_header(msg['Subject'])[0]
        if isinstance(subject, bytes):
            subject = subject.decode(enc or 'utf-8', errors='replace')
        sender = msg.get('From', '')
        # Prefer plain text body; fallback to HTML
        plain_body = ''
        html_body  = ''
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                decoded = payload.decode('utf-8', errors='replace')
                if ct == 'text/plain':
                    plain_body += decoded
                elif ct == 'text/html':
                    html_body += decoded
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                ct = msg.get_content_type()
                if ct == 'text/plain':
                    plain_body = payload.decode('utf-8', errors='replace')
                else:
                    html_body = payload.decode('utf-8', errors='replace')
        # Use plain text if available; otherwise strip HTML
        if plain_body.strip():
            body = plain_body
        else:
            soup = BeautifulSoup(html_body, 'lxml')
            body = soup.get_text(' ', strip=True)
        emails.append({'subject': subject, 'sender': sender, 'body': body, 'html_body': html_body})
    return emails

# ── Toast parsing ─────────────────────────────────────────────────────────────

def is_toast_location_email(e):
    """Match the detailed single-location daily summary (not the Group email)."""
    sender = e['sender'].lower()
    subj   = e['subject'].lower()
    if 'toasttab' not in sender and ('toast' not in sender):
        return False
    # The per-location email contains the street address
    return '229 west montecito' in subj

def is_toast_email(e):
    """Broad match — catches any Toast summary email."""
    sender = e['sender'].lower()
    subj   = e['subject'].lower()
    return ('toasttab' in sender) and any(k in subj for k in [
        'daily performance', 'performance summary', 'group -', 'santa barbara -'
    ])

def parse_toast(body, html_body=''):
    """
    Parse Toast Daily Performance Summary email.
    Format (after HTML-stripping):
      Net sales $4,858.45 SDLW ...
      Gross sales $5,089.45 SDLW ...
      Guests / covers 220 SDLW ...
      Orders 127 ...
      Avg order $38.26 ...
      Avg spend / guest $22.08 ...
      Hourly labor cost % of net 15.5% ...
      Discounts $231.00 ...
      Voids $80.08 ...
    """
    # If we have the raw HTML, strip it with BS4 for cleaner text
    if html_body:
        soup = BeautifulSoup(html_body, 'lxml')
        text = soup.get_text(' ', strip=True)
    else:
        text = body

    def find_val(patterns, default='--'):
        for p in patterns:
            m = re.search(p, text, re.I)
            if m:
                return m.group(1).replace(',', '')
        return default

    return {
        'net_sales':   find_val([
            r'Net\s+sales\s+\$\s*([\d,]+\.?\d*)',
            r'Net\s+sales?\s+([\d,]+\.?\d*)',
        ]),
        'gross_sales': find_val([
            r'Gross\s+sales\s+\$\s*([\d,]+\.?\d*)',
            r'Gross\s+sales?\s+([\d,]+\.?\d*)',
        ]),
        'guests':      find_val([
            r'Guests?\s*/\s*covers?\s+([\d,]+)',
            r'Guests?\s+(\d+)\s+SDLW',
        ]),
        'orders':      find_val([
            r'\bOrders\s+(\d+)\s+SDLW',
            r'\bOrders\s+(\d+)',
        ]),
        'avg_order':   find_val([
            r'Avg\s+order\s+\$\s*([\d,]+\.?\d*)',
            r'Avg\.?\s+order\s+([\d,]+\.?\d*)',
        ]),
        'avg_guest':   find_val([
            r'Avg\s+spend\s*/\s*guest\s+\$\s*([\d,]+\.?\d*)',
            r'Avg\s+spend\s*/\s*guest\s+([\d,]+\.?\d*)',
        ]),
        'labor_pct':   find_val([
            r'Hourly\s+labor\s+cost\s+%\s+of\s+net\s+([\d.]+)',
            r'labor\s+cost\s+%\s+of\s+net\s+([\d.]+)',
        ]),
        'discounts':   find_val([
            r'Discounts\s+\$\s*([\d,]+\.?\d*)',
            r'Discounts\s+([\d,]+\.?\d*)',
        ]),
        'voids':       find_val([
            r'Voids\s+\$\s*([\d,]+\.?\d*)',
            r'Voids\s+([\d,]+\.?\d*)',
        ]),
    }

# ── Sling parsing ─────────────────────────────────────────────────────────────

def is_sling_email(e):
    s = (e['subject'] + e['sender']).lower()
    return any(k in s for k in ['sling', 'getsling'])

ROLE_MAP = {
    'gm': 'GM', 'general manager': 'GM',
    'chef': 'Chef', 'head chef': 'Chef', 'executive chef': 'Chef',
    'cook': 'Cook', 'line cook': 'Cook', 'prep cook': 'Cook',
    'bartender': 'Bartender', 'bar': 'Bartender',
    'server': 'Server', 'waiter': 'Server', 'waitress': 'Server',
    'busser': 'Busser', 'busboy': 'Busser', 'food runner': 'Busser',
    'host': 'Host', 'hostess': 'Host',
}

def normalize_role(raw):
    r = raw.lower().strip()
    for k, v in ROLE_MAP.items():
        if k in r:
            return v
    return raw.title()

def parse_sling(body, html_body=''):
    """
    Parse Sling daily report email.
    Plain-text body format:
      Rise and shine Joal
      10 employees are scheduled to work a total of 46.74 hours today
      Isael Salvador Narciso (Cook) at (The Brewhouse Santa Barbara)
      Casey Smith (General Manager) at (The Brewhouse Santa Barbara)
      ...
    """
    # Use plain text if available
    if body.strip():
        text = body
    else:
        soup = BeautifulSoup(html_body, 'lxml')
        text = soup.get_text('\n', strip=True)

    staff = []
    # Pattern: "Name (Role) at (Location)"
    for line in re.split(r'[\r\n]+', text):
        line = line.strip()
        m = re.match(r'^([A-Z][a-zA-Z]+(?:\s+[A-Za-z][a-zA-Z]*)+)\s*\(([^)]+)\)\s+at\s+\(', line)
        if m:
            name = m.group(1).strip()
            role = normalize_role(m.group(2))
            staff.append({'name': name, 'role': role, 'hours': '--'})

    # Total scheduled hours
    total_hours = '--'
    m = re.search(r'total of\s+([\d.]+)\s+hours', text, re.I)
    if m:
        total_hours = m.group(1)

    # Overtime hours
    overtime = '0'
    m = re.search(r'([\d.]+)\s+[Oo]vertime\s+hours', text)
    if not m:
        m = re.search(r'[Oo]vertime\s+hours\s+([\d.]+)', text)
    if m:
        overtime = m.group(1)

    # Uncovered shifts
    uncovered = '0'
    m = re.search(r'(\d+)\s+[Uu]ncovered\s+shifts', text)
    if m:
        uncovered = m.group(1)

    return {'staff': staff, 'total_hours': total_hours, 'overtime': overtime, 'uncovered': uncovered}

# ── Google Ads email parsing ──────────────────────────────────────────────────

def is_google_ads_email(e):
    s = (e['subject'] + e['sender']).lower()
    return any(k in s for k in ['google ads', 'adwords', 'campaign performance', 'ads-account-noreply'])

def parse_google_ads(body):
    """
    The Google Ads notification email only contains a link to download the report —
    actual metrics are not embedded in the email body.
    We mark it as 'link_only' so the HTML can show a useful message.
    """
    # Try to extract the report view link
    m = re.search(r'https://ads\.google\.com/aw/reporting/reporteditor/view\?[^\s<>"]+', body)
    report_link = m.group(0) if m else ''
    return {
        'spend': '--', 'impressions': '--', 'clicks': '--',
        'ctr': '--', 'avg_cpc': '--', 'conversions': '--',
        'cost_per_conv': '--', 'source': 'link_only',
        'report_link': report_link,
    }

# ── Yelp Ads scraping via Playwright ─────────────────────────────────────────

def scrape_yelp_ads():
    """Log into biz.yelp.com and scrape the Ads dashboard."""
    yelp_user = os.environ.get('YELP_USER', '')
    yelp_pass = os.environ.get('YELP_PASSWORD', '')
    if not yelp_user or not yelp_pass:
        print("WARNING: No Yelp credentials. Using fallback data.")
        return _yelp_fallback()

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("WARNING: Playwright not installed.")
        return _yelp_fallback()

    result = _yelp_fallback()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            )
            page = ctx.new_page()

            # Log in
            page.goto('https://biz.yelp.com/login', wait_until='networkidle', timeout=30000)
            page.fill('input[type="email"]', yelp_user)
            page.fill('input[type="password"]', yelp_pass)
            page.click('button[type="submit"]')
            page.wait_for_load_state('networkidle', timeout=20000)

            # Navigate to Ads dashboard
            page.goto('https://biz.yelp.com/ads', wait_until='networkidle', timeout=20000)
            page.wait_for_timeout(3000)

            content = page.content()
            soup = BeautifulSoup(content, 'lxml')
            text = soup.get_text(' ', strip=True)

            def grab(patterns):
                for p in patterns:
                    m = re.search(p, text, re.I)
                    if m:
                        return m.group(1).replace(',', '')
                return '--'

            result = {
                'impressions':  grab([r'Impressions\D+([\d,]+\.?\d*[kKmM]?)', r'([\d,]+\.?\d*[kKmM]?)\s+impressions']),
                'page_visits':  grab([r'Page\s+visits\D+([\d,]+)', r'([\d,]+)\s+page\s+visits']),
                'leads':        grab([r'Leads\D+([\d,]+)', r'([\d,]+)\s+leads']),
                'spend':        grab([r'spend\D+\$?([\d,]+\.?\d*)', r'\$([\d,]+\.?\d*)\s+spend']),
                'period':       'Last 30 days',
                'source':       'live'
            }
            browser.close()
            print(f"Yelp: impressions={result['impressions']}, visits={result['page_visits']}, leads={result['leads']}")
    except Exception as ex:
        print(f"WARNING: Yelp scrape failed: {ex}")
        result = _yelp_fallback()
    return result

def _yelp_fallback():
    return {'impressions': '--', 'page_visits': '--', 'leads': '--', 'spend': '--', 'period': 'Last 30 days', 'source': 'fallback'}

# ── HTML generation ───────────────────────────────────────────────────────────

ROLE_EMOJI = {
    'GM': '👑', 'Chef': '👨‍🍳', 'Cook': '🍳',
    'Bartender': '🍺', 'Server': '🍽️', 'Busser': '🧹', 'Host': '🤝',
}
ROLE_CLASS = {
    'GM': 'gm', 'Chef': 'chef', 'Cook': 'cook',
    'Bartender': 'bartender', 'Server': 'server', 'Busser': 'busser', 'Host': 'host',
}

def generate_html(toast, sling, google_ads, yelp_ads, report_date):
    day_name  = report_date.strftime('%A').upper()
    month_day = report_date.strftime('%B %d, %Y').upper()

    # Staff rows
    staff_rows = ''
    for s in sling.get('staff', []):
        role    = s.get('role', 'Staff')
        emoji   = ROLE_EMOJI.get(role, '👤')
        cls     = ROLE_CLASS.get(role, 'staff')
        hrs_raw = s.get('hours', '--')
        try:
            hrs_val = float(hrs_raw)
            hrs_fmt = f"{hrs_val:.2f} hrs"
        except:
            hrs_fmt = hrs_raw
        staff_rows += f'''
        <tr>
          <td><span class="role-badge {cls}">{emoji} {role}</span></td>
          <td>{s.get("name","")}</td>
          <td>{hrs_fmt}</td>
        </tr>'''

    if not staff_rows:
        staff_rows = '<tr><td colspan="3" style="text-align:center;color:#888">No schedule data available</td></tr>'

    # Format Toast values
    def fmt_dollar(v):
        if v == '--':
            return '--'
        try:
            return f"{float(v):,.2f}"
        except:
            return v

    def fmt_int(v):
        if v == '--':
            return '--'
        try:
            return f"{int(float(v)):,}"
        except:
            return v

    t_net    = fmt_dollar(toast['net_sales'])
    t_gross  = fmt_dollar(toast['gross_sales'])
    t_guests = fmt_int(toast['guests'])
    t_orders = fmt_int(toast['orders'])
    t_avg_o  = fmt_dollar(toast['avg_order'])
    t_avg_g  = fmt_dollar(toast['avg_guest'])
    t_labor  = toast['labor_pct']
    if t_labor != '--' and '%' not in str(t_labor):
        t_labor = t_labor + '%'
    t_voids  = fmt_dollar(toast['voids'])
    labor_color = 'red' if toast['labor_pct'] != '--' and float(str(toast['labor_pct']).replace('%','').strip() or 0) > 30 else 'amber'

    # Google Ads section
    gads_spend  = f"${float(google_ads['spend']):,.2f}"  if google_ads['spend'] != '--' else '--'
    gads_impr   = f"{int(google_ads['impressions']):,}"  if google_ads['impressions'] != '--' else '--'
    gads_clicks = f"{int(google_ads['clicks']):,}"       if google_ads['clicks'] != '--' else '--'
    gads_ctr    = f"{google_ads['ctr']}%"                if google_ads['ctr'] != '--' else '--'
    gads_cpc    = f"${float(google_ads['avg_cpc']):,.2f}" if google_ads['avg_cpc'] != '--' else '--'
    gads_conv   = google_ads.get('conversions','--')

    src = google_ads.get('source', 'fallback')
    if src == 'email':
        gads_badge = '<span class="live-badge">📧 Email</span>'
    elif src == 'link_only':
        report_link = google_ads.get('report_link', '')
        link_html = f'<a href="{report_link}" target="_blank" style="color:var(--blue);font-size:0.72rem;text-decoration:none">📄 View Report →</a>' if report_link else ''
        gads_badge = f'<span class="info-badge">📧 Notification</span> {link_html}'
    else:
        gads_badge = '<span class="fallback-badge">⚠️ No Data</span>'

    # Yelp Ads section
    yelp_impr   = yelp_ads.get('impressions','--')
    yelp_visits = yelp_ads.get('page_visits','--')
    yelp_leads  = yelp_ads.get('leads','--')
    yelp_spend  = f"${float(yelp_ads['spend']):,.2f}" if yelp_ads.get('spend','--') not in ('--','') else '--'
    yelp_period = yelp_ads.get('period','Last 30 days')
    yelp_badge  = '<span class="live-badge">🔴 Live</span>' if yelp_ads.get('source') == 'live' else '<span class="fallback-badge">⚠️ No Data</span>'

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Brewhouse Morning Report — {report_date.strftime("%B %d, %Y")}</title>
<style>
  :root {{
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #f0a500;
    --green: #3fb950; --red: #f85149; --blue: #58a6ff;
    --purple: #bc8cff; --orange: #ffa657;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 20px; }}
  .header {{ text-align: center; padding: 32px 20px 24px; border-bottom: 1px solid var(--border); margin-bottom: 28px; }}
  .header h1 {{ font-size: 2rem; color: var(--accent); letter-spacing: 3px; text-transform: uppercase; }}
  .date {{ font-size: 0.9rem; color: var(--muted); margin-top: 6px; letter-spacing: 2px; }}
  .section {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }}
  .section-title {{ font-size: 1rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 2px; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; }}
  .metric {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }}
  .metric-value {{ font-size: 1.6rem; font-weight: 700; color: var(--green); margin-bottom: 4px; }}
  .metric-label {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }}
  .metric.amber .metric-value {{ color: var(--accent); }}
  .metric.blue .metric-value {{ color: var(--blue); }}
  .metric.red .metric-value {{ color: var(--red); }}
  .metric.purple .metric-value {{ color: var(--purple); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.72rem; letter-spacing: 1px; padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #1c2128; }}
  tr:last-child td {{ border-bottom: none; }}
  .role-badge {{ padding: 3px 9px; border-radius: 20px; font-size: 0.78rem; font-weight: 600; white-space: nowrap; }}
  .role-badge.gm {{ background: #4a1942; color: var(--purple); }}
  .role-badge.chef {{ background: #1a3a2a; color: var(--green); }}
  .role-badge.cook {{ background: #1a3a2a; color: #85e89d; }}
  .role-badge.bartender {{ background: #1a2a3a; color: var(--blue); }}
  .role-badge.server {{ background: #3a2a1a; color: var(--orange); }}
  .role-badge.busser {{ background: #2a2a2a; color: var(--muted); }}
  .role-badge.host {{ background: #1a3a3a; color: #79c0ff; }}
  .ads-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media(max-width: 640px) {{ .ads-grid {{ grid-template-columns: 1fr; }} }}
  .ads-platform {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .ads-platform-title {{ font-size: 0.85rem; font-weight: 700; color: var(--text); margin-bottom: 14px; display: flex; align-items: center; gap: 8px; justify-content: space-between; flex-wrap: wrap; }}
  .live-badge {{ background: #1a3a2a; color: var(--green); font-size: 0.7rem; padding: 2px 8px; border-radius: 20px; }}
  .fallback-badge {{ background: #2a2010; color: var(--orange); font-size: 0.7rem; padding: 2px 8px; border-radius: 20px; }}
  .info-badge {{ background: #1a2a3a; color: var(--blue); font-size: 0.7rem; padding: 2px 8px; border-radius: 20px; }}
  .ads-metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .ads-metric {{ text-align: center; padding: 10px; background: var(--card); border-radius: 6px; }}
  .ads-metric-value {{ font-size: 1.2rem; font-weight: 700; color: var(--blue); }}
  .ads-metric-label {{ font-size: 0.68rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin-top: 2px; }}
  .footer {{ text-align: center; color: var(--muted); font-size: 0.78rem; margin-top: 32px; padding-top: 20px; border-top: 1px solid var(--border); }}
  .tag {{ display: inline-block; font-size: 0.72rem; padding: 2px 8px; border-radius: 10px; margin-left: 8px; }}
  .tag.warning {{ background: #2a1a1a; color: var(--red); }}
  .tag.ok {{ background: #1a2a1a; color: var(--green); }}
</style>
</head>
<body>
<div class="header">
  <h1>🍺 The Brewhouse</h1>
  <div class="date">{day_name} &nbsp;·&nbsp; {month_day}</div>
  <div style="color:var(--muted);font-size:0.78rem;margin-top:8px;">MORNING REPORT</div>
</div>

<!-- SALES (TOAST) -->
<div class="section">
  <div class="section-title">📊 Sales Summary <span style="font-size:0.7rem;color:var(--muted);font-weight:400">(Toast)</span></div>
  <div class="metrics">
    <div class="metric">
      <div class="metric-value">${t_net}</div>
      <div class="metric-label">Net Sales</div>
    </div>
    <div class="metric amber">
      <div class="metric-value">${t_gross}</div>
      <div class="metric-label">Gross Sales</div>
    </div>
    <div class="metric blue">
      <div class="metric-value">{t_guests}</div>
      <div class="metric-label">Guests</div>
    </div>
    <div class="metric blue">
      <div class="metric-value">{t_orders}</div>
      <div class="metric-label">Orders</div>
    </div>
    <div class="metric">
      <div class="metric-value">${t_avg_o}</div>
      <div class="metric-label">Avg / Order</div>
    </div>
    <div class="metric">
      <div class="metric-value">${t_avg_g}</div>
      <div class="metric-label">Avg / Guest</div>
    </div>
    <div class="metric {labor_color}">
      <div class="metric-value">{t_labor}</div>
      <div class="metric-label">Labor %</div>
    </div>
    <div class="metric purple">
      <div class="metric-value">${t_voids}</div>
      <div class="metric-label">Voids</div>
    </div>
  </div>
</div>

<!-- SCHEDULE (SLING) -->
<div class="section">
  <div class="section-title">📅 Today's Schedule <span style="font-size:0.7rem;color:var(--muted);font-weight:400">(Sling)</span>
    {'<span class="tag warning">⚠ ' + sling["overtime"] + ' OT hrs</span>' if sling.get("overtime","0") not in ("0","--") else ''}
    {'<span class="tag warning">⚠ ' + sling["uncovered"] + ' Uncovered</span>' if sling.get("uncovered","0") not in ("0","--") else ''}
    {'<span class="tag ok">✓ All covered</span>' if sling.get("uncovered","0") in ("0","--") else ''}
  </div>
  <table>
    <thead><tr><th>Role</th><th>Name</th><th>Hours</th></tr></thead>
    <tbody>{staff_rows}</tbody>
  </table>
  <div style="margin-top:14px;color:var(--muted);font-size:0.82rem;">
    Total scheduled: <strong style="color:var(--text)">{sling.get('total_hours','--')} hrs</strong>
    &nbsp;·&nbsp; Overtime: <strong style="color:{'var(--red)' if sling.get('overtime','0') not in ('0','--') else 'var(--green)'}">{sling.get('overtime','0')} hrs</strong>
    &nbsp;·&nbsp; Uncovered shifts: <strong style="color:{'var(--red)' if sling.get('uncovered','0') not in ('0','--') else 'var(--green)'}">{sling.get('uncovered','0')}</strong>
  </div>
</div>

<!-- ADS PERFORMANCE -->
<div class="section">
  <div class="section-title">📣 Ad Performance</div>
  <div class="ads-grid">

    <!-- Google Ads -->
    <div class="ads-platform">
      <div class="ads-platform-title">
        <span>🔵 Google Ads — Yesterday</span>
        {gads_badge}
      </div>
      <div class="ads-metrics">
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_spend}</div>
          <div class="ads-metric-label">Spend</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_impr}</div>
          <div class="ads-metric-label">Impressions</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_clicks}</div>
          <div class="ads-metric-label">Clicks</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_ctr}</div>
          <div class="ads-metric-label">CTR</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_cpc}</div>
          <div class="ads-metric-label">Avg CPC</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{gads_conv}</div>
          <div class="ads-metric-label">Conversions</div>
        </div>
      </div>
    </div>

    <!-- Yelp Ads -->
    <div class="ads-platform">
      <div class="ads-platform-title">
        <span>🔴 Yelp Ads — {yelp_period}</span>
        {yelp_badge}
      </div>
      <div class="ads-metrics">
        <div class="ads-metric">
          <div class="ads-metric-value">{yelp_spend}</div>
          <div class="ads-metric-label">Spend</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{yelp_impr}</div>
          <div class="ads-metric-label">Impressions</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{yelp_visits}</div>
          <div class="ads-metric-label">Page Visits</div>
        </div>
        <div class="ads-metric">
          <div class="ads-metric-value">{yelp_leads}</div>
          <div class="ads-metric-label">Leads</div>
        </div>
      </div>
    </div>

  </div>
</div>

<div class="footer">
  Generated automatically · {datetime.now().strftime('%B %d, %Y at %I:%M %p UTC')} · The Brewhouse Morning Report
</div>
</body>
</html>'''
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    report_date = datetime.now() - timedelta(days=1)  # Yesterday's data
    print(f"Generating report for {report_date.strftime('%B %d, %Y')}...")

    # Default fallback data
    toast = {
        'net_sales': '--', 'gross_sales': '--', 'guests': '--', 'orders': '--',
        'avg_order': '--', 'avg_guest': '--', 'labor_pct': '--',
        'discounts': '--', 'voids': '--'
    }
    sling = {'staff': [], 'total_hours': '--', 'overtime': '0', 'uncovered': '0'}
    google_ads = {
        'spend': '--', 'impressions': '--', 'clicks': '--', 'ctr': '--',
        'avg_cpc': '--', 'conversions': '--', 'cost_per_conv': '--', 'source': 'fallback',
        'report_link': '',
    }

    # Gmail
    mail = connect_gmail()
    if mail:
        emails = fetch_emails(mail, days_back=3)
        print(f"Fetched {len(emails)} emails from Gmail")

        # Track best emails found: prefer most specific / most recent
        best_toast_location = None   # "229 West Montecito" email
        best_toast_group    = None   # Group summary email
        best_sling          = None
        best_gads           = None

        for e in emails:
            subj = e['subject']
            if is_toast_location_email(e):
                print(f"  Found Toast location email: {subj}")
                best_toast_location = e   # keep updating — last = most recent
            elif is_toast_email(e):
                print(f"  Found Toast group email: {subj}")
                best_toast_group = e
            if is_sling_email(e):
                print(f"  Found Sling email: {subj}")
                best_sling = e
            if is_google_ads_email(e):
                print(f"  Found Google Ads email: {subj}")
                best_gads = e

        # Use the best Toast email available
        chosen_toast = best_toast_location or best_toast_group
        if chosen_toast:
            print(f"  Parsing Toast: {chosen_toast['subject']}")
            toast = parse_toast(chosen_toast['body'], chosen_toast.get('html_body', ''))
            print(f"  Toast parsed → net_sales={toast['net_sales']}, guests={toast['guests']}, orders={toast['orders']}")

        if best_sling:
            print(f"  Parsing Sling: {best_sling['subject']}")
            sling = parse_sling(best_sling['body'], best_sling.get('html_body', ''))
            print(f"  Sling parsed → {len(sling['staff'])} staff, {sling['total_hours']} hrs")

        if best_gads:
            print(f"  Parsing Google Ads: {best_gads['subject']}")
            google_ads = parse_google_ads(best_gads['body'])
            google_ads['source'] = 'link_only'

        mail.logout()

    # Yelp (Playwright scrape)
    print("Scraping Yelp Ads dashboard...")
    yelp_ads = scrape_yelp_ads()

    # Generate and write HTML
    html = generate_html(toast, sling, google_ads, yelp_ads, report_date)
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Brewhouse_Morning_Report.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Report written to {out_path}")

if __name__ == '__main__':
    main()
