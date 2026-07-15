#!/usr/bin/env python3
# DEPRECATED: Logic migrated to models/jcf_vinculacion.py._cron_unified_jcf_monitor()
# Remove system crontab entry and this file after verifying unified cron works.
# Migration date: 2026-04-07
# To remove system crontab: crontab -e (as root/linux-odoo), remove jcf_municipios_monitor.py line

#!/usr/bin/env python3
"""
JCF Municipios Monitor v2 - Simplified
ONLY alerts when SLP or Soledad REOPEN (status changes to abierto).
No more percentage change spam.
"""
import json
import logging
import re
import urllib.request
import requests
import psycopg2
import sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_logger = logging.getLogger('jcf_municipios')

DTMLP_URL = 'https://jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/dtmlp.js'
WASENDER_URL = 'https://wasenderapi.com/api/send-message'
ALEIX_PHONE = '+524424751707'
SLP_IDEDO = 24
STATE_FILE = '/tmp/jcf_municipios_last.json'

# Only monitor these two for reopen alerts
WATCH_MUNICIPIOS = ['San Luis Potosi', 'Soledad de Graciano Sanchez']


def get_wasender_key():
    try:
        conn = psycopg2.connect(dbname='ORX', user='odoo')
        cur = conn.cursor()
        cur.execute("SELECT api_key FROM onrentx_wasender_config WHERE id = 4")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        _logger.error('DB error: %s', e)
        return None


def fetch_dtmlp():
    for attempt in range(3):
        try:
            req = urllib.request.Request(DTMLP_URL)
            req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64)')
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            _logger.warning('Attempt %d failed: %s', attempt + 1, e)
            import time
            time.sleep(5)
    return None


def normalize(name):
    """Normalize municipality name for comparison (remove accents)."""
    replacements = {
        '\xe1': 'a', '\xe9': 'e', '\xed': 'i', '\xf3': 'o', '\xfa': 'u',
        '\xc1': 'A', '\xc9': 'E', '\xcd': 'I', '\xd3': 'O', '\xda': 'U',
    }
    for old, new in replacements.items():
        name = name.replace(old, new)
    return name


def parse_slp_municipios(js_content):
    start = js_content.find('var detmun')
    if start == -1:
        return None
    section = js_content[start:]
    blocks = section.split('},')
    municipios = []
    for b in blocks:
        edo = re.search(r'edo:\s*(\d+)', b)
        if not edo or int(edo.group(1)) != SLP_IDEDO:
            continue
        lmun = re.search(r"lmun:\s*'([^']+)'", b)
        status = re.search(r'status:\s*(\d+)', b)
        avance = re.search(r"avance_mun:\s*'([^']*)'", b)
        lstatus = re.search(r"lstatus\s*:\s*'([^']*)'", b)
        if lmun:
            municipios.append({
                'name': lmun.group(1),
                'name_norm': normalize(lmun.group(1)),
                'status': int(status.group(1)) if status else 0,
                'avance': avance.group(1) if avance else '0',
                'lstatus': lstatus.group(1) if lstatus else '?',
            })
    return municipios


def send_wa(api_key, phone, message):
    try:
        resp = requests.post(
            WASENDER_URL,
            json={'to': phone, 'text': message},
            headers={
                'Authorization': 'Bearer %s' % api_key,
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        resp.raise_for_status()
        _logger.info('WhatsApp sent OK to %s', phone)
        return True
    except Exception as e:
        _logger.error('WhatsApp failed to %s: %s', phone, e)
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        _logger.error('Failed to save state: %s', e)


def main():
    api_key = get_wasender_key()
    if not api_key:
        _logger.error('No API key, aborting')
        return

    js = fetch_dtmlp()
    if not js:
        _logger.warning('Could not fetch dtmlp.js')
        return

    # Check if data is empty
    if 'inicio = [];' in js.replace(' ', ''):
        _logger.info('Empty data, platform may be updating')
        return

    municipios = parse_slp_municipios(js)
    if not municipios:
        _logger.warning('No SLP municipios found')
        return

    last_state = load_state()
    current_state = {}
    reopen_alerts = []

    for m in municipios:
        key = m['name_norm']
        current_state[key] = {'status': m['status'], 'avance': m['avance']}

        # Check if watched municipio REOPENED
        if key in WATCH_MUNICIPIOS:
            prev = last_state.get(key, {})
            prev_status = prev.get('status', -1)
            # Status 1 = abierto, 0 = cerrado, 2 = meta alcanzada
            # Alert if it was closed/meta (0 or 2) and now is open (1)
            if m['status'] == 1 and prev_status in (0, 2):
                reopen_alerts.append(
                    '*%s VOLVIO A ABRIR* (avance: %s%%)' % (m['name'], m['avance'])
                )

    save_state(current_state)

    if reopen_alerts:
        msg = ('*ALERTA JCF - MUNICIPIO REABIERTO*\n\n'
               + '\n'.join(reopen_alerts)
               + '\n\nEntra YA a vincular candidatos!')
        send_wa(api_key, ALEIX_PHONE, msg)
        _logger.info('REOPEN ALERT: %s', ', '.join(reopen_alerts))
    else:
        # Just log current status quietly
        slp = current_state.get('San Luis Potosi', {})
        sol = current_state.get('Soledad de Graciano Sanchez', {})
        _logger.info('Check done. SLP status=%s avance=%s%%, Soledad status=%s avance=%s%%',
                     slp.get('status', '?'), slp.get('avance', '?'),
                     sol.get('status', '?'), sol.get('avance', '?'))


if __name__ == '__main__':
    main()
