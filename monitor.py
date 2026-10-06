"""Read-only OAX calendar monitor. Credentials belong in GitHub Secrets."""
import argparse
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

BASE = 'https://bookings.oax.gr'
ATHENS = ZoneInfo('Europe/Athens')


def grid(table):
    rows = [r for r in table.find_all('tr') if r.find_parent('table') is table]
    result = [[] for _ in rows]
    for y, row in enumerate(rows):
        x = 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            while x < len(result[y]) and result[y][x] is not None:
                x += 1
            height = int(cell.get('rowspan', 1)) or len(rows) - y
            width = int(cell.get('colspan', 1))
            if height < 1 or width < 1 or height > 1000 or width > 1000:
                raise ValueError('Invalid calendar cell span')
            for yy in range(y, min(y + height, len(rows))):
                while len(result[yy]) < x + width:
                    result[yy].append(None)
                for xx in range(x, x + width):
                    result[yy][xx] = cell
            x += width
    return result


def text(cell):
    return ' '.join(cell.stripped_strings) if cell else ''


def extract(html, day, config):
    soup = BeautifulSoup(html, 'html.parser')
    if soup.select_one('#login_form'):
        raise RuntimeError('Login required: check OAX credentials')
    pattern = re.compile(config['free_text_pattern'], re.IGNORECASE)
    slots, diagnostics = [], []
    seen = set()
    found = False
    now = datetime.now(ATHENS)
    for table in soup.find_all('table'):
        matrix = grid(table)
        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell is None or id(cell) in seen:
                    continue
                # Headers are matched narrowly, avoiding outer layout tables.
                court = None
                for above in reversed(matrix[:y]):
                    candidate = text(above[x]) if x < len(above) else ''
                    if re.fullmatch(r'ΓΗΠΕΔΟ\s*\d+', candidate, re.IGNORECASE):
                        court = candidate
                        break
                if not court:
                    continue
                seen.add(id(cell))
                # Require time at the beginning of the cell or a time-only row header.
                content = text(cell)
                match = re.match(r'^(\d{1,2}:\d{2})(?:\s|$)', content)
                hour = match.group(1).zfill(5) if match else None
                remainder = content[match.end():].strip() if match else content
                if not hour:
                    for left in row[:x]:
                        if re.fullmatch(r'\d{1,2}:\d{2}', text(left)):
                            hour = text(left).zfill(5)
                            break
                if hour not in config['hours']:
                    continue
                found = True
                # Diagnostics never contain member names, links, or session tokens.
                diagnostics.append({'date': day, 'court': court, 'hour': hour,
                                    'empty_after_time': not remainder,
                                    'matches_free_rule': bool(pattern.fullmatch(remainder)),
                                    'class': cell.get('class', []),
                                    'link_count': len(cell.find_all('a'))})
                if config['availability_verified'] and pattern.fullmatch(remainder):
                    start = datetime.fromisoformat(f'{day}T{hour}').replace(tzinfo=ATHENS)
                    if start > now:
                        slots.append({'date': day, 'court': court, 'hour': hour})
    if not found:
        raise RuntimeError(f'Calendar structure not recognized for {day}; no target hours found')
    return slots, diagnostics


def telegram(message):
    token = os.environ['TELEGRAM_BOT_TOKEN']
    payload = json.dumps({'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'text': message}).encode()
    request = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise RuntimeError('Telegram rejected notification')
    except Exception:
        # Never expose the URL containing the Telegram token in an exception.
        raise RuntimeError('Telegram delivery failed; check bot token and chat ID') from None


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path('config.json').read_text())
    days = int(config['days_ahead'])
    if not 1 <= days <= 31:
        raise RuntimeError('days_ahead must be between 1 and 31')
    if not config['availability_verified'] and not args.diagnose:
        raise RuntimeError('Run diagnostic mode and verify the free-cell rule before enabling alerts')
    state_path = Path('state.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    today = datetime.now(ATHENS).date()
    diagnostics = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(30000)
        page.goto(f'{BASE}/login.php', wait_until='domcontentloaded')
        page.locator('#login_username').fill(os.environ['OAX_USERNAME'])
        page.locator('#login_passwd').fill(os.environ['OAX_PASSWORD'])
        # Submit the observed login form; no booking controls are operated.
        page.locator('#login_form').evaluate('(form) => form.submit()')
        page.wait_for_load_state('domcontentloaded')
        for offset in range(days):
            day = (today + timedelta(days=offset)).isoformat()
            page.goto(f'{BASE}/calendar/calendar.php?{urlencode({"date": day})}',
                      wait_until='networkidle')
            if '/login.php' in page.url:
                raise RuntimeError('OAX login failed or session expired')
            slots, detail = extract(page.content(), day, config)
            diagnostics.extend(detail)
            if args.diagnose:
                continue
            keys = {f'{s["date"]}|{s["court"]}|{s["hour"]}' for s in slots}
            before = set(state.get(day, []))
            new = [s for s in slots if f'{s["date"]}|{s["court"]}|{s["hour"]}' not in before]
            if new:
                lines = [f'{s["court"]} · {s["hour"]}' for s in new]
                telegram(f'🎾 Διαθέσιμα γήπεδα ΟΑΧ — {day}\n' + '\n'.join(lines)
                         + f'\n{BASE}/calendar/calendar.php?date={day}\n'
                         + 'Διαθέσιμα κατά τον έλεγχο. Επιβεβαίωσε πριν την κράτηση.')
            state[day] = sorted(keys)
            # Persist after successful delivery; a failed delivery is retried next run.
            state = {d: v for d, v in state.items() if d >= today.isoformat()}
            if not args.diagnose:
                state_path.write_text(json.dumps(state, ensure_ascii=False))
        browser.close()
    if args.diagnose:
        Path('diagnostics.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2))
        print(f'Diagnostic complete: {len(diagnostics)} target cells. Alerts were not sent.')
    else:
        print('Calendar check complete.')


if __name__ == '__main__':
    main()
