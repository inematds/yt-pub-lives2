#!/usr/bin/env python3
"""
Scheduler para pipeline yt-pub-lives.
Roda em loop, checa a cada minuto se esta na hora de cortar ou publicar.
Le configuracao da planilha CONFIG.
"""

import json
import os
import sys
import time
import subprocess
import base64
import urllib.request
import urllib.parse
from datetime import datetime

# Config
CONFIG_DIR = os.environ.get('GWS_CONFIG_DIR', os.path.expanduser('~/.config/gws'))
ENV_FILE = os.path.join(CONFIG_DIR, '.env')
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '1KG6sp77DeelQ6RTqzMZN2INXHJWxuUFtOUI3dOf7Ivs')
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
LIVES_DIR = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard', 'scheduler_status.json')

# Load env
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key] = val


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def update_status(state, detail='', video_id=''):
    """Escreve status atual do scheduler em JSON para o dashboard ler."""
    data = {
        'state': state,        # idle | cortando | publicando | erro
        'detail': detail,
        'video_id': video_id,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def get_access_token():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with open(os.path.join(CONFIG_DIR, '.encryption_key'), 'r') as f:
        key = base64.b64decode(f.read().strip())

    with open(os.path.join(CONFIG_DIR, 'credentials.enc'), 'rb') as f:
        data = f.read()

    aesgcm = AESGCM(key)
    creds = json.loads(aesgcm.decrypt(data[:12], data[12:], None))

    token_data = urllib.parse.urlencode({
        'client_id': os.environ['CLIENT_ID'],
        'client_secret': os.environ['CLIENT_SECRET'],
        'refresh_token': creds['refresh_token'],
        'grant_type': 'refresh_token'
    }).encode()

    req = urllib.request.Request('https://oauth2.googleapis.com/token', data=token_data)
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp['access_token']


def sheets_get(range_str):
    token = get_access_token()
    encoded = urllib.parse.quote(range_str)
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}'
    req = urllib.request.Request(url)
    req.add_header('Authorization', f'Bearer {token}')
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode(), 'status': e.code}


def sheets_update(range_str, values):
    token = get_access_token()
    encoded = urllib.parse.quote(range_str)
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}?valueInputOption=RAW'
    body = json.dumps({'range': range_str, 'majorDimension': 'ROWS', 'values': values}).encode()
    req = urllib.request.Request(url, data=body, method='PUT')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'application/json')
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()}


def load_config():
    """Le CONFIG da planilha e retorna dict."""
    result = sheets_get('CONFIG!A1:B20')
    rows = result.get('values', [])
    config = {}
    for row in rows[1:]:
        if len(row) >= 2:
            config[row[0]] = row[1]
    return config


def get_pending_lives():
    """Retorna lives pendentes (mais antigas primeiro)."""
    result = sheets_get('LIVES!A1:L1000')
    rows = result.get('values', [])
    if len(rows) < 2:
        return [], rows
    headers = rows[0]
    lives = []
    for i, row in enumerate(rows[1:], 2):  # row_num starts at 2 (1-indexed, skip header)
        live = {'_row': i}
        for j, h in enumerate(headers):
            live[h] = row[j] if j < len(row) else ''
        lives.append(live)
    # Oldest first
    lives.sort(key=lambda l: l.get('data_live', ''))
    return lives, rows


def is_hour_now(horarios_str):
    """Checa se a hora atual esta na lista de horarios (ex: '06:00,12:00,18:00')."""
    if not horarios_str:
        return False
    now_hour = datetime.now().strftime('%H:00')
    return now_hour in [h.strip() for h in horarios_str.split(',')]


def run_corte(video_id):
    """Executa yt-clip para uma live."""
    log(f'  Executando corte: {video_id}')
    update_status('cortando', f'Cortando live {video_id}', video_id)
    script = os.path.join(SCRIPTS_DIR, 'yt-clip')
    env = os.environ.copy()
    env['LIVES_DIR'] = LIVES_DIR
    env['PATH'] = f"/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    result = subprocess.run(
        [script, video_id, '--ai', 'claude-api'],
        capture_output=True, text=True, timeout=1800,  # 30 min max
        env=env
    )

    if result.returncode == 0:
        log(f'  Corte concluido: {video_id}')
        update_status('idle', f'Corte concluido: {video_id}', video_id)
        return True
    else:
        log(f'  Erro no corte: {result.stderr[-500:] if result.stderr else "sem output"}')
        update_status('erro', f'Erro no corte: {video_id}', video_id)
        return False


def run_publicacao(video_id, clip_file, title, description, tags, privacy):
    """Executa yt-publish para um clip."""
    log(f'  Publicando: {title[:60]}')
    update_status('publicando', f'Publicando: {title[:50]}', video_id)
    script = os.path.join(SCRIPTS_DIR, 'yt-publish')
    env = os.environ.copy()
    env['PATH'] = f"/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    cmd = [script, clip_file, '--title', title, '--description', description, '--privacy', privacy]
    if tags:
        cmd += ['--tags', tags]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)

    if result.returncode == 0:
        # Extract video ID from output
        for line in (result.stdout + result.stderr).split('\n'):
            if 'Video ID:' in line:
                return line.split('Video ID:')[1].strip()
        log(f'  Publicado mas sem video ID no output')
        return 'unknown'
    else:
        log(f'  Erro na publicacao: {result.stderr[-500:] if result.stderr else "sem output"}')
        return None


def update_live_status(row_num, headers, row_data, status_field, new_status, extra=None):
    """Atualiza status de uma live na planilha."""
    if status_field in headers:
        col = headers.index(status_field)
        while len(row_data) <= col:
            row_data.append('')
        row_data[col] = new_status
    if extra:
        for field, val in extra.items():
            if field in headers:
                col = headers.index(field)
                while len(row_data) <= col:
                    row_data.append('')
                row_data[col] = str(val)
    sheets_update(f'LIVES!A{row_num}:L{row_num}', [row_data])


def process_cortes(config):
    """Processa cortes de lives pendentes."""
    max_per_run = int(config.get('corte_max_por_dia', '3'))
    lives, all_rows = get_pending_lives()
    headers = all_rows[0] if all_rows else []

    pendentes = [l for l in lives if l.get('status_cortes') != 'concluido']
    if not pendentes:
        log('  Nenhuma live pendente para cortar')
        return

    log(f'  {len(pendentes)} lives pendentes, processando ate {max_per_run}')

    for live in pendentes[:max_per_run]:
        vid = live.get('video_id', '')
        row_num = live['_row']
        if not vid:
            continue

        # Get original row data
        orig_row = all_rows[row_num - 1] if row_num - 1 < len(all_rows) else []

        success = run_corte(vid)
        if success:
            # Check what was produced
            job_dir = os.path.join(LIVES_DIR, vid)
            topics_file = os.path.join(job_dir, 'topics.json')
            clips_dir = os.path.join(job_dir, 'clips')

            qtd_clips = 0
            if os.path.exists(topics_file):
                with open(topics_file) as f:
                    topics = json.load(f)
                qtd_clips = len(topics.get('topics', []))

            has_clips = os.path.isdir(clips_dir) and len(os.listdir(clips_dir)) > 0

            update_live_status(row_num, headers, list(orig_row), 'status_transcricao', 'concluido', {
                'status_cortes': 'concluido' if has_clips else 'pendente',
                'qtd_clips': qtd_clips
            })
        else:
            update_live_status(row_num, headers, list(orig_row), 'status_cortes', 'erro')


def process_publicacao(config):
    """Publica clips cortados que ainda nao foram publicados."""
    privacy = config.get('privacy_padrao', 'unlisted')
    max_por_vez = int(config.get('pub_max_por_vez', '2') or '2')
    lives, all_rows = get_pending_lives()
    headers = all_rows[0] if all_rows else []

    # Find lives with clips but not all published
    for live in lives:
        vid = live.get('video_id', '')
        if live.get('status_cortes') != 'concluido' or not vid:
            continue

        qtd_clips = int(live.get('qtd_clips', '0') or '0')
        publicados = int(live.get('clips_publicados', '0') or '0')

        if publicados >= qtd_clips or qtd_clips == 0:
            continue

        job_dir = os.path.join(LIVES_DIR, vid)
        manifest_file = os.path.join(job_dir, 'clips_manifest.json')

        if not os.path.exists(manifest_file):
            log(f'  Sem manifest para {vid}, pulando')
            continue

        with open(manifest_file) as f:
            clips = json.load(f)

        # Check which clips are already published
        pub_result = sheets_get('PUBLICADOS!A1:J1000')
        pub_rows = pub_result.get('values', [])
        published_titles = set()
        if len(pub_rows) > 1:
            pub_headers = pub_rows[0]
            title_col = pub_headers.index('clip_titulo') if 'clip_titulo' in pub_headers else 1
            for row in pub_rows[1:]:
                if len(row) > title_col:
                    published_titles.add(row[title_col])

        count = 0
        for clip in clips:
            if count >= max_por_vez:
                log(f'  Limite de {max_por_vez} clips por vez atingido')
                break

            if clip['title'] in published_titles:
                continue

            if not os.path.exists(clip['file']):
                log(f'  Arquivo nao encontrado: {clip["file"]}')
                continue

            new_vid = run_publicacao(
                vid, clip['file'], clip['title'],
                clip.get('description', ''), ','.join(clip.get('tags', [])),
                privacy
            )

            if new_vid:
                # Add to PUBLICADOS sheet
                from datetime import datetime as dt
                now = dt.now().strftime('%Y-%m-%d %H:%M')
                from dashboard.server import sheets_append as _sa
                # Direct append
                token = get_access_token()
                encoded = urllib.parse.quote('PUBLICADOS!A1')
                url = f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS'
                body_data = json.dumps({
                    'range': 'PUBLICADOS!A1',
                    'majorDimension': 'ROWS',
                    'values': [[
                        new_vid, clip['title'],
                        f'https://www.youtube.com/watch?v={new_vid}',
                        vid, live.get('titulo', ''),
                        now, privacy,
                        str(clip.get('duration', '')),
                        ','.join(clip.get('tags', [])),
                        '27'
                    ]]
                }).encode()
                req = urllib.request.Request(url, data=body_data, method='POST')
                req.add_header('Authorization', f'Bearer {token}')
                req.add_header('Content-Type', 'application/json')
                try:
                    urllib.request.urlopen(req)
                except Exception as e:
                    log(f'  Erro ao gravar na planilha: {e}')

                count += 1
                log(f'  Publicado: {clip["title"][:50]} -> {new_vid}')

        if count > 0:
            row_num = live['_row']
            orig_row = list(all_rows[row_num - 1]) if row_num - 1 < len(all_rows) else []
            update_live_status(row_num, headers, orig_row, 'clips_publicados', str(publicados + count))
            log(f'  {count} clips publicados para {vid}')

        # Only process one live per run to avoid quota issues
        break


def main():
    log('Scheduler iniciado')
    log(f'  Scripts: {SCRIPTS_DIR}')
    log(f'  Lives: {LIVES_DIR}')
    log(f'  Config: {CONFIG_DIR}')
    update_status('idle', 'Scheduler iniciado')

    # Roda cortes uma vez ao iniciar
    current_hour = datetime.now().strftime('%H')
    try:
        config = load_config()
        cortes_paused = config.get('pipeline_cortes_paused', 'false') == 'true'
        if not cortes_paused:
            log('==> Corte inicial ao startar')
            process_cortes(config)
        else:
            log('==> Cortes pausados, pulando corte inicial')
    except Exception as e:
        log(f'ERRO no corte inicial: {e}')

    # Marca hora atual como ja executada para nao repetir no loop
    executed_this_hour = {'cortes': current_hour, 'pub': None}

    while True:
        try:
            now = datetime.now()
            current_hour = now.strftime('%H')

            config = load_config()

            # --- Cortes ---
            cortes_paused = config.get('pipeline_cortes_paused', 'false') == 'true'
            corte_auto = config.get('corte_auto', 'true') == 'true'
            corte_horarios = config.get('corte_horarios', '')

            if not cortes_paused and corte_auto and is_hour_now(corte_horarios):
                if executed_this_hour['cortes'] != current_hour:
                    log('==> Hora de cortar!')
                    process_cortes(config)
                    executed_this_hour['cortes'] = current_hour
            elif executed_this_hour['cortes'] != current_hour:
                # Reset flag when hour changes
                pass

            # --- Publicacao ---
            pub_paused = config.get('pipeline_pub_paused', 'false') == 'true'
            pub_horarios = config.get('pub_horarios', '')

            if not pub_paused and is_hour_now(pub_horarios):
                if executed_this_hour['pub'] != current_hour:
                    log('==> Hora de publicar!')
                    process_publicacao(config)
                    executed_this_hour['pub'] = current_hour

            # Reset when hour changes
            if executed_this_hour['cortes'] and executed_this_hour['cortes'] != current_hour:
                executed_this_hour['cortes'] = None
            if executed_this_hour['pub'] and executed_this_hour['pub'] != current_hour:
                executed_this_hour['pub'] = None

        except Exception as e:
            log(f'ERRO: {e}')

        # Check every 60 seconds
        time.sleep(60)


if __name__ == '__main__':
    main()
