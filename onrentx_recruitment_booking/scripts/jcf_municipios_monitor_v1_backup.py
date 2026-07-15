#!/usr/bin/env python3
"""
JCF Municipios Monitor - Standalone cron script
Monitors SLP municipalities (Soledad de Graciano Sánchez + San Luis Potosí)
Sends WhatsApp report every hour via WaSender.
Runs via system cron, NOT inside Odoo.
"""
import json
import logging
import re
import urllib.request
import requests
import psycopg2

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_logger = logging.getLogger('jcf_municipios')

DTMLP_URL = 'https://jovenesconstruyendoelfuturo.stps.gob.mx/focalizacion/dtmlp.js'
WASENDER_URL = 'https://wasenderapi.com/api/send-message'
ALEIX_PHONE = '+524424751707'
SLP_IDEDO = 24
MUNICIPIOS_INTERES = [
    'San Luis Potosí',
    'Soledad de Graciano Sánchez',
    'Mexquitic de Carmona',
    'Cerro de San Pedro',
    'Villa de Reyes',
    'Santa María del Río',
    'Ahualulco',
    'Villa de Pozos',
]


def get_wasender_key():
    """Get WaSender API key from Odoo DB."""
    try:
        conn = psycopg2.connect(dbname='ORX', user='odoo')
        cur = conn.cursor()
        cur.execute("SELECT api_key FROM onrentx_wasender_config WHERE id = 4")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        _logger.error('DB error getting API key: %s', e)
        return None


def fetch_dtmlp():
    """Fetch dtmlp.js with retries."""
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


def parse_slp_municipios(js_content):
    """Parse SLP municipalities from dtmlp.js."""
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
                'status': int(status.group(1)) if status else 0,
                'avance': avance.group(1) if avance else '0',
                'lstatus': lstatus.group(1) if lstatus else '?',
            })
    return municipios


def send_wa(api_key, message):
    """Send WhatsApp via WaSender using requests."""
    try:
        resp = requests.post(
            WASENDER_URL,
            json={'to': ALEIX_PHONE, 'text': message},
            headers={
                'Authorization': 'Bearer %s' % api_key,
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        resp.raise_for_status()
        _logger.info('WhatsApp sent OK')
        return True
    except Exception as e:
        _logger.error('WhatsApp failed: %s', e)
        return False


ALERT_THRESHOLD = 15  # Alert + notify candidates if SLP or Soledad avance >= this %
STATE_FILE = '/tmp/jcf_municipios_last.json'
CANDIDATES_NOTIFIED_FILE = '/tmp/jcf_urgency_notified.json'
ALERT_MUNICIPIOS = ['San Luis Potosí', 'Soledad de Graciano Sánchez']

# N1 candidates to notify when threshold crossed
CANDIDATES = [
    {'name': 'Andrea', 'phone': '+524446830545', 'empresa': 'Comercial Ancora'},
    {'name': 'Dominick', 'phone': '+525534339161', 'empresa': 'Comercial Ancora'},
    {'name': 'Karla', 'phone': '+524448008263', 'empresa': 'Comercial Ancora'},
    {'name': 'Christian', 'phone': '+524444014781', 'empresa': 'Jorge Martinez Hernandez'},
    {'name': 'Victor', 'phone': '+524442223254', 'empresa': 'Jorge Martinez Hernandez'},
]


def load_last_state():
    """Load previous avance values."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    """Save current avance values."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        _logger.error('Failed to save state: %s', e)


def main():
    import sys
    # --alert-only mode: only send if threshold crossed (for 15-min cron)
    alert_only = '--alert-only' in sys.argv

    api_key = get_wasender_key()
    if not api_key:
        _logger.error('No API key, aborting')
        return

    js = fetch_dtmlp()
    if not js:
        if not alert_only:
            send_wa(api_key, '*Monitor Municipios JCF*\n\nNo se pudo descargar dtmlp.js (servidor caido)')
        return

    # Get timestamp
    ts_match = re.search(r'Datos actualizados al (.+)', js)
    ts = ts_match.group(1).strip() if ts_match else '?'

    # Check if inicio is empty
    inicio_pos = js.find('var inicio')
    if inicio_pos != -1:
        empty_check = js[inicio_pos:inicio_pos + 50]
        if 'inicio = [];' in empty_check or 'inicio = []' in empty_check.replace(' ', ''):
            if not alert_only:
                send_wa(api_key, '*Monitor Municipios JCF*\n\nPagina en blanco - sin datos')
            return

    municipios = parse_slp_municipios(js)
    if municipios is None:
        if not alert_only:
            send_wa(api_key, '*Monitor Municipios JCF*\n\nNo se encontraron municipios en dtmlp.js')
        return

    # Check for alerts (threshold crossed or candidate vinculado)
    last_state = load_last_state()
    current_state = {}
    alerts = []
    for m in municipios:
        current_state[m['name']] = int(m['avance']) if m['avance'].isdigit() else 0
        if m['name'] in ALERT_MUNICIPIOS:
            avance_now = current_state[m['name']]
            avance_prev = last_state.get(m['name'], 0)
            if avance_now >= ALERT_THRESHOLD and avance_prev < ALERT_THRESHOLD:
                alerts.append('*%s subio a %d%%* (antes %d%%)' % (m['name'], avance_now, avance_prev))
            elif avance_now > avance_prev and avance_now > 0:
                alerts.append('%s: %d%% -> %d%%' % (m['name'], avance_prev, avance_now))

    save_state(current_state)

    # Send immediate alert if threshold crossed
    if alerts:
        alert_msg = '*ALERTA Monitor Municipios SLP*\n\n' + '\n'.join(alerts) + '\n\nDatos: %s' % ts
        send_wa(api_key, alert_msg)
        _logger.info('Alert sent: %s', ', '.join(alerts))

        # If threshold crossed, also notify candidates (only once)
        threshold_crossed = any(
            current_state.get(m, 0) >= ALERT_THRESHOLD and last_state.get(m, 0) < ALERT_THRESHOLD
            for m in ALERT_MUNICIPIOS
        )
        if threshold_crossed:
            try:
                with open(CANDIDATES_NOTIFIED_FILE) as f:
                    already_notified = json.load(f).get('notified', False)
            except Exception:
                already_notified = False

            if not already_notified:
                slp_pct = current_state.get('San Luis Potosí', 0)
                sol_pct = current_state.get('Soledad de Graciano Sánchez', 0)
                sent = 0
                for c in CANDIDATES:
                    urgency_msg = (
                        "URGENTE %s! Los municipios de JCF se estan llenando rapido.\n\n"
                        "San Luis Potosi va al %d%% y Soledad al %d%%.\n\n"
                        "Si no te has vinculado, hazlo YA. Entra a jovenesconstruyendoelfuturo.stps.gob.mx "
                        "y busca la empresa *\"%s\"*.\n\n"
                        "Cada minuto cuenta. Enviame captura cuando lo logres."
                    ) % (c['name'], slp_pct, sol_pct, c['empresa'])
                    try:
                        resp = requests.post(
                            WASENDER_URL,
                            json={'to': c['phone'], 'text': urgency_msg},
                            headers={
                                'Authorization': 'Bearer %s' % api_key,
                                'Content-Type': 'application/json',
                            },
                            timeout=15,
                        )
                        resp.raise_for_status()
                        sent += 1
                        _logger.info('Urgency WA sent to %s', c['name'])
                    except Exception as e:
                        _logger.error('Urgency WA failed for %s: %s', c['name'], e)

                with open(CANDIDATES_NOTIFIED_FILE, 'w') as f:
                    json.dump({'notified': True, 'sent': sent}, f)

                send_wa(api_key, 'Aviso urgencia enviado a %d candidatos (N1+Victor+Karla)' % sent)
                _logger.info('Urgency notifications sent to %d candidates', sent)

                # Also SMS to Aleix
                try:
                    import base64 as b64
                    conn = psycopg2.connect(dbname='ORX', user='odoo')
                    cur = conn.cursor()
                    cur.execute("SELECT labsmobile_username, labsmobile_token, labsmobile_base_url FROM iap_account WHERE provider = 'sms_api_labsmobile' LIMIT 1")
                    row = cur.fetchone()
                    conn.close()
                    if row and row[0] and row[1]:
                        sms_auth = b64.b64encode(('%s:%s' % (row[0], row[1])).encode()).decode()
                        sms_msg = 'ALERTA JCF: SLP al %d%%, Soledad al %d%%. Aviso urgencia enviado a %d candidatos.' % (
                            current_state.get('San Luis Potosí', 0),
                            current_state.get('Soledad de Graciano Sánchez', 0),
                            sent,
                        )
                        resp = requests.post(
                            '%s/json/send' % (row[2] or 'https://api.labsmobile.com'),
                            json={'message': sms_msg, 'recipient': [{'msisdn': '524424751707'}]},
                            headers={
                                'Authorization': 'Basic %s' % sms_auth,
                                'Content-Type': 'application/json',
                            },
                            timeout=15,
                        )
                        _logger.info('SMS alert sent to Aleix')
                except Exception as e:
                    _logger.error('SMS alert failed: %s', e)

    # In alert-only mode, only send hourly report at minute 0
    from datetime import datetime
    now_min = datetime.now().minute
    is_hourly = (now_min < 15)  # runs at :00, :15, :30, :45 — only :00 sends full report

    if alert_only and not is_hourly:
        _logger.info('Alert-only check done. SLP=%s%% Soledad=%s%%',
                     current_state.get('San Luis Potosí', '?'),
                     current_state.get('Soledad de Graciano Sánchez', '?'))
        return

    # Focus municipalities
    focus = [m for m in municipios if m['name'] in MUNICIPIOS_INTERES]
    abiertos = [m for m in municipios if m['status'] == 1]
    cerrados = [m for m in municipios if m['status'] == 0]

    # Build message
    msg = '*Monitor Municipios SLP*\n'
    msg += 'Datos: %s\n\n' % ts

    # Focus municipalities first
    for m in focus:
        icon = 'ABIERTO' if m['status'] == 1 else 'CERRADO'
        msg += '*%s: %s (%s%%)*\n' % (m['name'], icon, m['avance'])
    msg += '\n'

    # Summary
    msg += 'Total SLP: %d abiertos, %d cerrados\n\n' % (len(abiertos), len(cerrados))

    # List open ones with avance > 0
    con_avance = [m for m in abiertos if m['avance'] != '0']
    if con_avance:
        msg += 'Con avance:\n'
        for m in sorted(con_avance, key=lambda x: -int(x['avance'])):
            msg += '  %s (%s%%)\n' % (m['name'], m['avance'])

    send_wa(api_key, msg)
    _logger.info('Report sent: %d abiertos, %d cerrados, focus: %s',
                 len(abiertos), len(cerrados),
                 ', '.join('%s=%s' % (m['name'], 'ABIERTO' if m['status'] == 1 else 'CERRADO') for m in focus))


if __name__ == '__main__':
    main()
