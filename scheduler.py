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
import tempfile
import urllib.request
import urllib.parse
from datetime import datetime

# Config
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get('GWS_CONFIG_DIR', os.path.join(PROJECT_ROOT, 'config'))
ENV_FILE = os.path.join(CONFIG_DIR, '.env')
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard', 'scheduler_status.json')

# Load env (before reading env-dependent vars)
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key] = val

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID', '')
LIVES_DIR = os.environ.get('LIVES_DIR', os.path.join(PROJECT_ROOT, 'lives'))


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr, flush=True)


def update_status(state, detail='', video_id='', step='', clip_id='', clip_title=''):
    """Escreve status atual do scheduler em JSON para o dashboard ler."""
    data = {
        'state': state,        # idle | cortando | publicando | erro
        'detail': detail,
        'video_id': video_id,
        'clip_id': clip_id,
        'clip_title': clip_title,
        'step': step,          # etapa atual: transcricao | analise | download | corte | thumbnail | upload
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


def get_matching_schedule(horarios_str):
    """Retorna o horario agendado que bate com agora, ou None.
    Suporta HH:00 (hora cheia) e HH:MM (minuto exato)."""
    if not horarios_str:
        return None
    now_hm = datetime.now().strftime('%H:%M')
    now_hour = datetime.now().strftime('%H:00')
    for h in horarios_str.split(','):
        h = h.strip()
        if h == now_hm:
            return h
        if h == now_hour:
            return h
    return None


def run_corte(video_id, config=None):
    """Executa yt-clip para uma live, atualizando status por etapa."""
    log(f'  Executando corte: {video_id}')
    update_status('cortando', f'Baixando transcricao...', video_id, step='transcricao')
    script = os.path.join(SCRIPTS_DIR, 'yt-clip')
    env = os.environ.copy()
    env['LIVES_DIR'] = LIVES_DIR
    env['PATH'] = f"{os.path.expanduser('~/.deno/bin')}:/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    # Modo de analise: claude-api | anthropic-api | openrouter-api | piramyd-api
    ai_mode = 'claude-api'
    if config:
        ai_mode = config.get('ai_mode', 'claude-api')
        ai_model = config.get('ai_model', '')
        if ai_model:
            env['AI_MODEL'] = ai_model
        if ai_mode == 'anthropic-api':
            key = config.get('anthropic_api_key', '')
            if key:
                env['ANTHROPIC_API_KEY'] = key
        elif ai_mode == 'openrouter-api':
            key = config.get('openrouter_api_key', '')
            if key:
                env['OPENROUTER_API_KEY'] = key
        elif ai_mode == 'piramyd-api':
            key = config.get('thumb_api_key', '')
            if key:
                env['PIRAMYD_API_KEY'] = key

    proc = subprocess.Popen(
        [script, video_id, '--ai', ai_mode],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env
    )

    output_lines = []
    step_map = {
        '[1/5]': ('transcricao', 'Baixando transcricao...'),
        '[2/5]': ('analise_transcript', 'Processando transcricao...'),
        '[3/5]': ('analise', 'Analisando topicos com IA...'),
        '[4/5]': ('corte', 'Baixando video e cortando clips...'),
        '[5/5]': ('publicacao', 'Finalizando...'),
    }

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        output_lines.append(line)
        log(f'    | {line}')
        # Detecta etapa pelo marcador [N/5]
        for marker, (step, label) in step_map.items():
            if marker in line:
                update_status('cortando', label, video_id, step=step)
                break

    proc.wait()

    if proc.returncode == 0:
        log(f'  Corte concluido: {video_id}')
        update_status('idle', f'Corte concluido: {video_id}', video_id)
        return True
    else:
        last_output = '\n'.join(output_lines[-5:]) if output_lines else 'sem output'
        log(f'  Erro no corte: {last_output}')
        update_status('erro', f'Erro no corte: {video_id}', video_id)
        return False


def refine_pub_with_ai(title, description, config):
    """Usa IA para refinar titulo e descricao antes de publicar."""
    prompt_file = os.path.join(CONFIG_DIR, 'prompt_pub.txt')
    if not os.path.exists(prompt_file):
        return title, description

    with open(prompt_file) as f:
        system_prompt = f.read().strip()
    if not system_prompt:
        return title, description

    # Determine API endpoint and key
    api_key = config.get('thumb_api_key', '') or os.environ.get('PIRAMYD_API_KEY', '')
    if not api_key:
        log('  Sem API key para refinar publicacao, usando titulo/descricao originais')
        return title, description

    api_url = 'https://api.piramyd.cloud/v1/chat/completions'
    ai_model = config.get('ai_model', '') or 'claude-sonnet-4.5'

    user_msg = f'Titulo original: "{title}"\nDescricao original: "{description}"'

    payload = {
        'model': ai_model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_msg}
        ],
        'temperature': 0.7,
        'max_tokens': 500
    }

    try:
        log(f'  Refinando titulo/descricao com IA ({ai_model})...')
        body = json.dumps(payload).encode()
        req = urllib.request.Request(api_url, data=body)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'Bearer {api_key}')

        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        content = result['choices'][0]['message']['content']

        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            refined = json.loads(json_match.group())
            new_title = refined.get('title', title)
            new_desc = refined.get('description', description)
            log(f'  Titulo refinado: {new_title[:60]}')
            return new_title, new_desc
        else:
            log(f'  IA nao retornou JSON valido, usando originais')
            return title, description
    except Exception as e:
        log(f'  Erro ao refinar com IA: {e}, usando originais')
        return title, description


def run_publicacao(video_id, clip_file, title, description, tags, privacy):
    """Executa yt-publish para um clip."""
    log(f'  Publicando: {title[:60]}')
    log(f'  Arquivo: {clip_file} ({os.path.getsize(clip_file) / 1024 / 1024:.1f} MB)')
    update_status('publicando', f'Publicando: {title[:50]}', video_id, step='upload')
    script = os.path.join(SCRIPTS_DIR, 'yt-publish')
    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.deno/bin')}:/usr/bin:{os.path.expanduser('~/.local/bin')}:{SCRIPTS_DIR}:{env.get('PATH', '')}"

    cmd = [script, clip_file, '--title', title, '--description', description, '--privacy', privacy]
    if tags:
        cmd += ['--tags', tags]

    log(f'  CMD: {" ".join(cmd[:3])} ...')
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    output_lines = []
    video_id_result = None
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            output_lines.append(line)
            log(f'    | {line}')
            if 'Video ID:' in line:
                video_id_result = line.split('Video ID:')[1].strip()

        proc.wait(timeout=600)  # 10 min max per upload
    except subprocess.TimeoutExpired:
        log(f'  TIMEOUT: publicacao excedeu 10 min, matando processo')
        proc.kill()
        proc.wait()
        return None

    if proc.returncode == 0:
        if video_id_result:
            return video_id_result
        log(f'  Publicado mas sem video ID no output')
        return 'unknown'
    else:
        last_output = '\n'.join(output_lines[-5:]) if output_lines else 'sem output'
        log(f'  Erro na publicacao: {last_output}')
        return None


def upload_thumbnail(video_id, thumb_path):
    """Upload thumbnail to YouTube using thumbnails.set API."""
    token = get_access_token()
    url = f'https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}&uploadType=media'

    with open(thumb_path, 'rb') as f:
        img_data = f.read()

    req = urllib.request.Request(url, data=img_data, method='POST')
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Content-Type', 'image/jpeg')
    req.add_header('Content-Length', str(len(img_data)))

    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read())
    log(f'  Thumbnail uploaded for {video_id}')
    return result


def _add_pending_thumb(video_id, title):
    """Add a thumbnail to the pending list for later upload."""
    pending_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lives', 'pending_thumbs.json')
    pending = []
    if os.path.exists(pending_file):
        try:
            with open(pending_file) as f:
                pending = json.load(f)
        except Exception:
            pending = []
    # Avoid duplicates
    if not any(p['id'] == video_id for p in pending):
        pending.append({'id': video_id, 'title': title})
        with open(pending_file, 'w') as f:
            json.dump(pending, f, indent=2, ensure_ascii=False)


def handle_thumbnail(video_id, title, description, config):
    """Generate and upload thumbnail based on config thumb_mode."""
    thumb_mode = config.get('thumb_mode', 'none')
    if thumb_mode == 'none':
        return

    thumb_path = f'/tmp/yt_thumb_{video_id}.jpg'

    try:
        if thumb_mode == 'api':
            # Set API key, model and visual config before importing
            api_key = config.get('thumb_api_key', '')
            model = config.get('thumb_model', 'dreamshaper')
            if api_key:
                os.environ['PIRAMYD_API_KEY'] = api_key
            os.environ['THUMB_MODEL'] = model
            # Visual settings
            for key in ('thumb_font_size', 'thumb_text_color', 'thumb_accent_color',
                        'thumb_brand_color', 'thumb_text_position', 'thumb_brand'):
                val = config.get(key, '')
                if val:
                    os.environ[key.upper()] = val

            # Import generate_thumbnail from scripts/yt-thumbnail
            import types
            script_path = os.path.join(SCRIPTS_DIR, 'yt-thumbnail')
            yt_thumb = types.ModuleType('yt_thumbnail')
            yt_thumb.__file__ = script_path
            with open(script_path) as _f:
                exec(compile(_f.read(), script_path, 'exec'), yt_thumb.__dict__)

            log(f'  Generating API thumbnail for {video_id} (model: {model})')
            yt_thumb.generate_thumbnail(title, description, thumb_path)

        elif thumb_mode == 'local':
            # Local Pillow-based: extract frame from video + overlay text
            log(f'  Generating local thumbnail for {video_id}')
            from PIL import Image, ImageDraw, ImageFont

            # Create simple gradient background with text overlay
            img = Image.new('RGB', (1280, 720))
            for y in range(720):
                r = int(10 + 10 * y / 720)
                g = int(10 + 5 * y / 720)
                b = int(30 + 20 * y / 720)
                for x in range(1280):
                    img.putpixel((x, y), (r, g, b))

            draw = ImageDraw.Draw(img)
            font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            try:
                font = ImageFont.truetype(font_path, 64)
            except Exception:
                font = ImageFont.load_default()

            # Wrap and draw title text
            words = title[:60].upper().split()
            lines, current = [], ''
            for word in words:
                test = (current + ' ' + word).strip()
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] > 1000 and current:
                    lines.append(current)
                    current = word
                else:
                    current = test
            if current:
                lines.append(current)

            y_pos = 80
            for line in lines[:3]:
                # Shadow
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        draw.text((130 + dx, y_pos + dy), line, font=font, fill=(0, 0, 0))
                draw.text((130, y_pos), line, font=font, fill=(255, 255, 255))
                y_pos += 80

            img.save(thumb_path, 'JPEG', quality=92)

        else:
            log(f'  Unknown thumb_mode: {thumb_mode}, skipping')
            return

        # Save a copy to lives/thumbs/ for future reference
        if os.path.exists(thumb_path):
            thumbs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lives', 'thumbs')
            os.makedirs(thumbs_dir, exist_ok=True)
            import shutil
            saved_path = os.path.join(thumbs_dir, f'{video_id}.jpg')
            shutil.copy2(thumb_path, saved_path)

            # Upload to YouTube
            try:
                upload_thumbnail(video_id, thumb_path)
            except Exception as upload_err:
                log(f'  Thumbnail upload failed, saved as pending: {upload_err}')
                _add_pending_thumb(video_id, title)
            # Clean up temp file
            try:
                os.remove(thumb_path)
            except OSError:
                pass

    except Exception as e:
        log(f'  Thumbnail error (non-fatal): {e}')


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

    pendentes = [l for l in lives if l.get('status_cortes') not in ('concluido', 'erro')]
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

        success = run_corte(vid, config)
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

            update_live_status(row_num, headers, list(orig_row), 'status_transcricao', 'transcricao', {
                'status_cortes': 'concluido' if has_clips else 'pendente',
                'qtd_clips': qtd_clips
            })
        else:
            update_live_status(row_num, headers, list(orig_row), 'status_cortes', 'erro')


def process_publicacao(config):
    """Publica clips cortados que ainda nao foram publicados."""
    privacy = config.get('privacy_padrao', 'unlisted')
    max_por_vez = int(config.get('pub_max_por_vez', '2') or '2')
    log(f'  Buscando lives para publicar (privacy={privacy}, max={max_por_vez})...')
    lives, all_rows = get_pending_lives()
    headers = all_rows[0] if all_rows else []
    log(f'  {len(lives)} lives encontradas')

    # Find lives with clips but not all published
    found_any = False
    for live in lives:
        vid = live.get('video_id', '')
        if live.get('status_cortes') != 'concluido' or not vid:
            continue

        qtd_clips = int(live.get('qtd_clips', '0') or '0')
        publicados = int(live.get('clips_publicados', '0') or '0')

        if publicados >= qtd_clips or qtd_clips == 0:
            continue

        found_any = True
        log(f'  Live {vid}: {qtd_clips} clips, {publicados} publicados')

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
        log(f'  {len(clips)} clips no manifest, {len(published_titles)} ja publicados')
        for clip in clips:
            if count >= max_por_vez:
                log(f'  Limite de {max_por_vez} clips por vez atingido')
                break

            if clip['title'] in published_titles:
                log(f'  Ja publicado: {clip["title"][:50]}')
                continue

            if clip.get('paused', False):
                log(f'  Pausado: {clip["title"][:50]}')
                continue

            if not os.path.exists(clip['file']):
                log(f'  Arquivo nao encontrado: {clip["file"]}')
                continue

            clip_title = clip['title']
            clip_desc = clip.get('description', '')

            # Refinar titulo e descricao com IA
            update_status('publicando', f'Refinando com IA: {clip_title[:50]}', vid, step='refine', clip_title=clip_title[:50])
            clip_title, clip_desc = refine_pub_with_ai(clip_title, clip_desc, config)

            update_status('publicando', f'Enviando: {clip_title[:50]}', vid, step='upload', clip_title=clip_title[:50])
            new_vid = run_publicacao(
                vid, clip['file'], clip_title,
                clip_desc, ','.join(clip.get('tags', [])),
                privacy
            )

            if new_vid:
                # Generate and upload thumbnail
                update_status('publicando', f'Gerando thumbnail...', vid, step='thumbnail', clip_id=new_vid, clip_title=clip_title[:50])
                handle_thumbnail(
                    new_vid, clip_title,
                    clip.get('description', ''), config
                )

                # Add to PUBLICADOS sheet
                now = datetime.now().strftime('%Y-%m-%d %H:%M')
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

        # Update counter if clips were published OR if counter is out of sync
        actual_published = sum(1 for c in clips if c['title'] in published_titles)
        new_total = actual_published + count
        if new_total != publicados:
            row_num = live['_row']
            orig_row = list(all_rows[row_num - 1]) if row_num - 1 < len(all_rows) else []
            update_live_status(row_num, headers, orig_row, 'clips_publicados', str(new_total))
            log(f'  Atualizado clips_publicados: {publicados} -> {new_total} para {vid}')

        if count > 0:
            log(f'  {count} clips publicados para {vid}')
            update_status('idle', f'{count} clips publicados para {vid}')
            # Only break after actually publishing to avoid quota issues
            break

    if not found_any:
        log('  Nenhum clip pendente para publicar')
        update_status('idle', 'Nenhum clip para publicar')


def main():
    log('Scheduler iniciado')
    log(f'  Scripts: {SCRIPTS_DIR}')
    log(f'  Lives: {LIVES_DIR}')
    log(f'  Config: {CONFIG_DIR}')
    update_status('idle', 'Scheduler iniciado')

    # Roda cortes uma vez ao iniciar
    current_minute = datetime.now().strftime('%H:%M')
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

    # Rastreia qual horario agendado ja foi executado (evita repetir)
    # No startup, marca o horario atual como executado para nao disparar imediatamente
    startup_corte = get_matching_schedule(config.get('corte_horarios', '')) if config else None
    startup_pub = get_matching_schedule(config.get('pub_horarios', '')) if config else None
    last_executed = {'cortes': startup_corte, 'pub': startup_pub}
    log(f'  Agendamento: cortes={startup_corte or "nenhum agora"}, pub={startup_pub or "nenhum agora"}')

    while True:
        try:
            config = load_config()

            # --- Cortes ---
            cortes_paused = config.get('pipeline_cortes_paused', 'false') == 'true'
            corte_auto = config.get('corte_auto', 'true') == 'true'
            corte_horarios = config.get('corte_horarios', '')

            corte_match = get_matching_schedule(corte_horarios)
            if not cortes_paused and corte_auto and corte_match:
                if last_executed['cortes'] != corte_match:
                    last_executed['cortes'] = corte_match
                    log(f'==> Hora de cortar! (agendado: {corte_match})')
                    process_cortes(config)

            # Reset quando sai do horario
            if not corte_match and last_executed['cortes']:
                last_executed['cortes'] = None

            # --- Publicacao ---
            pub_paused = config.get('pipeline_pub_paused', 'false') == 'true'
            pub_horarios = config.get('pub_horarios', '')

            pub_match = get_matching_schedule(pub_horarios)
            if not pub_paused and pub_match:
                if last_executed['pub'] != pub_match:
                    last_executed['pub'] = pub_match
                    log(f'==> Hora de publicar! (agendado: {pub_match})')
                    process_publicacao(config)

            # Reset quando sai do horario
            if not pub_match and last_executed['pub']:
                last_executed['pub'] = None

        except Exception as e:
            log(f'ERRO: {e}')

        # Check every 60 seconds
        time.sleep(60)


if __name__ == '__main__':
    main()
