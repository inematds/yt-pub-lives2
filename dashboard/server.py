#!/usr/bin/env python3
"""
Dashboard server for GWS Lives & Clips pipeline.
Reads/writes to Google Sheet and syncs with YouTube channel.
"""

import json
import os
import sys
import base64
import urllib.request
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

# Config
CONFIG_DIR = os.environ.get('GWS_CONFIG_DIR', os.path.expanduser('~/.config/gws'))
ENV_FILE = os.path.join(CONFIG_DIR, '.env')
SPREADSHEET_ID = '1KG6sp77DeelQ6RTqzMZN2INXHJWxuUFtOUI3dOf7Ivs'
PORT = 8090

# Load env
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key] = val


def get_access_token():
    """Get OAuth access token from encrypted credentials."""
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


def sheets_api(method, endpoint, body=None):
    """Call Google Sheets API."""
    token = get_access_token()
    url = f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/{endpoint}'

    if body:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
    else:
        req = urllib.request.Request(url, method=method)

    req.add_header('Authorization', f'Bearer {token}')

    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        return {'error': error_body, 'status': e.code}


def sheets_get(range_str):
    """Read values from sheet."""
    encoded_range = urllib.parse.quote(range_str)
    return sheets_api('GET', f'values/{encoded_range}')


def sheets_update(range_str, values):
    """Write values to sheet."""
    encoded_range = urllib.parse.quote(range_str)
    body = {
        'range': range_str,
        'majorDimension': 'ROWS',
        'values': values
    }
    return sheets_api('PUT', f'values/{encoded_range}?valueInputOption=RAW', body)


def sheets_append(range_str, values):
    """Append values to sheet."""
    encoded_range = urllib.parse.quote(range_str)
    body = {
        'range': range_str,
        'majorDimension': 'ROWS',
        'values': values
    }
    return sheets_api('POST', f'values/{encoded_range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS', body)


def youtube_api(endpoint, params=None):
    """Call YouTube Data API."""
    token = get_access_token()
    api_key = os.environ.get('API_KEY', '')

    base_url = f'https://www.googleapis.com/youtube/v3/{endpoint}'
    if params:
        params['key'] = api_key
        base_url += '?' + urllib.parse.urlencode(params)
    else:
        base_url += f'?key={api_key}'

    req = urllib.request.Request(base_url)
    req.add_header('Authorization', f'Bearer {token}')

    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        return {'error': error_body, 'status': e.code}


def get_channel_lives(channel_id, page_token=None):
    """Get live streams from channel using search API."""
    params = {
        'channelId': channel_id,
        'part': 'snippet',
        'type': 'video',
        'eventType': 'completed',
        'maxResults': 50,
        'order': 'date'
    }
    if page_token:
        params['pageToken'] = page_token
    return youtube_api('search', params)


def get_video_details(video_ids):
    """Get video details by IDs."""
    params = {
        'part': 'snippet,contentDetails,statistics',
        'id': ','.join(video_ids)
    }
    return youtube_api('videos', params)


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == '/api/lives':
            self.handle_api_lives()
        elif path == '/api/publicados':
            video_id = qs.get('live', [None])[0]
            self.handle_api_publicados(video_id)
        elif path == '/api/config':
            self.handle_api_config()
        elif path == '/api/prompts':
            self.handle_api_prompts_get()
        elif path == '/api/stats':
            self.handle_api_stats()
        elif path == '/api/scheduler/status':
            self.handle_scheduler_status()
        elif path == '/api/transcript':
            video_id = qs.get('id', [None])[0]
            self.handle_api_transcript(video_id)
        elif path == '/api/thumbs/pending':
            self.handle_thumbs_pending()
        elif path.startswith('/clips/'):
            self.handle_serve_clip(path)
        elif path == '/':
            self.path = '/index.html'
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length else '{}'
        data = json.loads(body) if body else {}

        parsed = urllib.parse.urlparse(self.path)
        post_path = parsed.path

        if post_path == '/api/sync':
            self.handle_sync(data)
        elif post_path == '/api/config':
            self.handle_update_config(data)
        elif post_path == '/api/clip/privacy':
            self.handle_clip_privacy(data)
        elif post_path == '/api/clip/delete':
            self.handle_clip_delete(data)
        elif post_path == '/api/pipeline/toggle':
            self.handle_pipeline_toggle(data)
        elif post_path == '/api/live/reprocess':
            self.handle_live_reprocess(data)
        elif post_path == '/api/clip/pause':
            self.handle_clip_pause(data)
        elif post_path == '/api/prompts':
            self.handle_api_prompts_save(data)
        elif post_path == '/api/cleanup/clips':
            self.handle_cleanup_clips(data)
        elif post_path == '/api/cleanup/sources':
            self.handle_cleanup_sources(data)
        elif post_path == '/api/live/delete':
            self.handle_live_delete(data)
        elif post_path == '/api/thumbs/upload':
            self.handle_thumbs_upload(data)
        else:
            self.send_json(404, {'error': 'not found'})

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def handle_scheduler_status(self):
        status_file = os.path.join(os.path.dirname(__file__), 'scheduler_status.json')
        if os.path.exists(status_file):
            with open(status_file) as f:
                data = json.load(f)
            self.send_json(200, data)
        else:
            self.send_json(200, {'state': 'offline', 'detail': 'Scheduler nao iniciado', 'updated_at': ''})

    def handle_serve_clip(self, path):
        """Serve clip files from lives directory."""
        # /clips/<video_id>/<filename>
        parts = path.split('/', 3)  # ['', 'clips', 'video_id', 'filename']
        if len(parts) < 4:
            self.send_json(404, {'error': 'not found'})
            return
        video_id = parts[2]
        filename = parts[3]
        # Sanitize
        if '..' in video_id or '..' in filename or '/' in filename:
            self.send_json(400, {'error': 'invalid path'})
            return
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        filepath = os.path.join(lives_dir, video_id, 'clips', filename)
        if not os.path.exists(filepath):
            self.send_json(404, {'error': 'file not found'})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(os.path.getsize(filepath)))
        self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.end_headers()
        with open(filepath, 'rb') as f:
            self.wfile.write(f.read())

    def handle_api_lives(self):
        result = sheets_get('LIVES!A1:L1000')
        rows = result.get('values', [])
        if len(rows) < 2:
            self.send_json(200, {'lives': [], 'headers': rows[0] if rows else []})
            return

        headers = rows[0]
        lives = []
        date_col = headers.index('data_live') if 'data_live' in headers else 2
        for row in rows[1:]:
            live = {}
            for i, h in enumerate(headers):
                live[h] = row[i] if i < len(row) else ''
            lives.append(live)

        # Enrich with last publication date from PUBLICADOS
        pub_result = sheets_get('PUBLICADOS!A1:J1000')
        pub_rows = pub_result.get('values', [])
        pub_dates = {}  # live_video_id -> last pub date
        if len(pub_rows) > 1:
            pub_headers = pub_rows[0]
            live_col = pub_headers.index('live_video_id') if 'live_video_id' in pub_headers else 3
            date_col = pub_headers.index('data_publicacao') if 'data_publicacao' in pub_headers else 5
            for row in pub_rows[1:]:
                lid = row[live_col] if len(row) > live_col else ''
                dt = row[date_col] if len(row) > date_col else ''
                if lid and dt:
                    if lid not in pub_dates or dt > pub_dates[lid]:
                        pub_dates[lid] = dt

        for live in lives:
            live['data_publicacao'] = pub_dates.get(live.get('video_id', ''), '')

        # Oldest first - prioritize processing older lives
        lives.sort(key=lambda l: l.get('data_live', ''))

        self.send_json(200, {'lives': lives, 'total': len(lives)})

    def handle_api_publicados(self, filter_live_id=None):
        result = sheets_get('PUBLICADOS!A1:J1000')
        rows = result.get('values', [])
        if len(rows) < 2:
            self.send_json(200, {'publicados': [], 'headers': rows[0] if rows else []})
            return

        headers = rows[0]
        publicados = []
        live_col = headers.index('live_video_id') if 'live_video_id' in headers else 3
        for row in rows[1:]:
            pub = {}
            for i, h in enumerate(headers):
                pub[h] = row[i] if i < len(row) else ''
            if filter_live_id and pub.get('live_video_id', '') != filter_live_id:
                continue
            publicados.append(pub)

        # Enrich publicados with filename from manifest
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        manifests_cache = {}
        for pub in publicados:
            lid = pub.get('live_video_id', '')
            if lid and lid not in manifests_cache:
                mp = os.path.join(lives_dir, lid, 'clips_manifest.json')
                if os.path.exists(mp):
                    try:
                        with open(mp) as f:
                            manifests_cache[lid] = {c.get('title', ''): c.get('filename', '') for c in json.load(f)}
                    except Exception:
                        manifests_cache[lid] = {}
                else:
                    manifests_cache[lid] = {}
            pub['filename'] = manifests_cache.get(lid, {}).get(pub.get('clip_titulo', ''), '')

        # Incluir clips pendentes (cortados mas nao publicados)
        pendentes = []
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        pub_titles = set(p.get('clip_titulo', '') for p in publicados)

        live_ids = [filter_live_id] if filter_live_id else []
        if not filter_live_id:
            # Scan all lives with topics.json
            if os.path.isdir(lives_dir):
                for d in os.listdir(lives_dir):
                    if os.path.exists(os.path.join(lives_dir, d, 'topics.json')):
                        live_ids.append(d)

        for lid in live_ids:
            topics_path = os.path.join(lives_dir, lid, 'topics.json')
            manifest_path = os.path.join(lives_dir, lid, 'clips_manifest.json')
            if os.path.exists(topics_path):
                try:
                    with open(topics_path) as f:
                        topics_data = json.load(f)
                    # Load manifest for filenames and paused state
                    manifest = {}
                    if os.path.exists(manifest_path):
                        with open(manifest_path) as f:
                            for c in json.load(f):
                                manifest[c.get('title', '')] = {
                                    'filename': c.get('filename', ''),
                                    'paused': c.get('paused', False)
                                }
                    for t in topics_data.get('topics', []):
                        title = t.get('title', '')
                        if title not in pub_titles:
                            m = manifest.get(title, {})
                            pendentes.append({
                                'title': title,
                                'description': t.get('description', ''),
                                'tags': ', '.join(t.get('tags', [])),
                                'start': t.get('start', ''),
                                'end': t.get('end', ''),
                                'live_video_id': lid,
                                'filename': m.get('filename', ''),
                                'paused': m.get('paused', False),
                            })
                except Exception:
                    pass

        self.send_json(200, {'publicados': publicados, 'pendentes': pendentes, 'total': len(publicados), 'filter': filter_live_id})

    def handle_api_transcript(self, video_id):
        """Return transcript for a video if available locally."""
        if not video_id:
            self.send_json(400, {'error': 'id parameter required'})
            return

        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        job_dir = os.path.join(lives_dir, video_id)

        result = {'video_id': video_id, 'has_transcript': False, 'has_topics': False}

        # Check condensed transcript
        condensed_path = os.path.join(job_dir, 'condensed.txt')
        if os.path.exists(condensed_path):
            with open(condensed_path, 'r') as f:
                result['transcript'] = f.read()
            result['has_transcript'] = True

        # Check topics
        topics_path = os.path.join(job_dir, 'topics.json')
        if os.path.exists(topics_path):
            with open(topics_path, 'r') as f:
                result['topics'] = json.load(f)
            result['has_topics'] = True

        self.send_json(200, result)

    def handle_api_config(self):
        result = sheets_get('CONFIG!A1:B50')
        rows = result.get('values', [])
        config = {}
        for row in rows[1:]:  # skip header
            if len(row) >= 2:
                config[row[0]] = row[1]
        self.send_json(200, {'config': config})

    def handle_api_prompts_get(self):
        config_dir = os.environ.get('GWS_CONFIG_DIR', os.path.expanduser('~/.config/gws'))
        prompts = {}
        for name in ('prompt_cortes', 'prompt_pub', 'prompt_thumb'):
            path = os.path.join(config_dir, f'{name}.txt')
            if os.path.exists(path):
                with open(path) as f:
                    prompts[name] = f.read()
            else:
                prompts[name] = ''
        self.send_json(200, {'prompts': prompts})

    def handle_api_prompts_save(self, data):
        config_dir = os.environ.get('GWS_CONFIG_DIR', os.path.expanduser('~/.config/gws'))
        saved = []
        for name in ('prompt_cortes', 'prompt_pub', 'prompt_thumb'):
            if name in data:
                path = os.path.join(config_dir, f'{name}.txt')
                with open(path, 'w') as f:
                    f.write(data[name])
                saved.append(name)
        self.send_json(200, {'ok': True, 'saved': saved})

    def handle_api_stats(self):
        lives_result = sheets_get('LIVES!A1:L1000')
        pub_result = sheets_get('PUBLICADOS!A1:J1000')

        lives_rows = lives_result.get('values', [])
        pub_rows = pub_result.get('values', [])

        total_lives = max(0, len(lives_rows) - 1)
        total_publicados = max(0, len(pub_rows) - 1)

        # Count by status
        pendentes = 0
        cortados = 0
        for row in lives_rows[1:]:
            status = row[6] if len(row) > 6 else ''
            if status == 'concluido':
                cortados += 1
            else:
                pendentes += 1

        self.send_json(200, {
            'total_lives': total_lives,
            'total_publicados': total_publicados,
            'lives_cortadas': cortados,
            'lives_pendentes': pendentes
        })

    def handle_sync(self, data):
        """Sync lives from YouTube channel."""
        channel_id = os.environ.get('YOUTUBE_CHANNEL_ID', 'UC2QbQDyPKuHk93dwo5iq3Sw')

        # Get existing video IDs from sheet
        existing_result = sheets_get('LIVES!A2:A1000')
        existing_ids = set()
        for row in existing_result.get('values', []):
            if row:
                existing_ids.add(row[0])

        # Fetch lives from YouTube
        all_lives = []
        page_token = None
        max_pages = data.get('max_pages', 3)  # limit pages to avoid quota

        for _ in range(max_pages):
            result = get_channel_lives(channel_id, page_token)
            if 'error' in result:
                self.send_json(500, {'error': result['error']})
                return

            items = result.get('items', [])
            for item in items:
                vid = item['id'].get('videoId', '')
                if vid and vid not in existing_ids:
                    snippet = item.get('snippet', {})
                    all_lives.append({
                        'video_id': vid,
                        'titulo': snippet.get('title', ''),
                        'data_live': snippet.get('publishedAt', '')[:10],
                        'url': f'https://www.youtube.com/watch?v={vid}'
                    })

            page_token = result.get('nextPageToken')
            if not page_token:
                break

        # Get durations for new videos
        if all_lives:
            video_ids = [l['video_id'] for l in all_lives]
            # Batch in groups of 50
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i:i+50]
                details = get_video_details(batch)
                duration_map = {}
                for item in details.get('items', []):
                    vid = item['id']
                    # Parse ISO 8601 duration (PT1H30M15S)
                    dur = item.get('contentDetails', {}).get('duration', '')
                    minutes = parse_duration_minutes(dur)
                    duration_map[vid] = minutes

                for live in all_lives:
                    if live['video_id'] in duration_map:
                        live['duracao_min'] = str(duration_map[live['video_id']])

            # Append new lives to sheet
            new_rows = []
            today = __import__('datetime').date.today().isoformat()
            for live in all_lives:
                new_rows.append([
                    live['video_id'],
                    live['titulo'],
                    live['data_live'],
                    live.get('duracao_min', ''),
                    live['url'],
                    'pendente',  # status_transcricao
                    'pendente',  # status_cortes
                    '0',         # qtd_clips
                    '0',         # clips_publicados
                    '0',         # clips_pendentes
                    today,       # data_sync
                    ''           # observacoes
                ])

            if new_rows:
                sheets_append('LIVES!A1', new_rows)

        self.send_json(200, {
            'novas_lives': len(all_lives),
            'ja_existentes': len(existing_ids),
            'lives': all_lives
        })

    def handle_update_config(self, data):
        """Update config values."""
        # Read current config
        result = sheets_get('CONFIG!A1:B50')
        rows = result.get('values', [])

        # Update values
        for i, row in enumerate(rows):
            if len(row) >= 1 and row[0] in data:
                rows[i] = [row[0], str(data[row[0]])]

        sheets_update('CONFIG!A1:B' + str(len(rows)), rows)
        self.send_json(200, {'ok': True, 'updated': list(data.keys())})

    def handle_clip_privacy(self, data):
        """Update privacy of a published clip on YouTube."""
        clip_id = data.get('clip_video_id')
        new_privacy = data.get('privacy')
        if not clip_id or not new_privacy:
            self.send_json(400, {'error': 'clip_video_id and privacy required'})
            return

        # Update on YouTube
        token = get_access_token()
        api_key = os.environ.get('API_KEY', '')
        body = {
            'id': clip_id,
            'status': {'privacyStatus': new_privacy}
        }
        url = f'https://www.googleapis.com/youtube/v3/videos?part=status&key={api_key}'
        req_data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=req_data, method='PUT')
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', 'application/json')

        try:
            resp = urllib.request.urlopen(req)
            json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            self.send_json(500, {'error': error_body})
            return

        # Update in spreadsheet
        result = sheets_get('PUBLICADOS!A1:J1000')
        rows = result.get('values', [])
        if rows:
            headers = rows[0]
            id_col = headers.index('clip_video_id') if 'clip_video_id' in headers else 0
            priv_col = headers.index('privacy') if 'privacy' in headers else 6
            for i, row in enumerate(rows[1:], 1):
                if len(row) > id_col and row[id_col] == clip_id:
                    while len(row) <= priv_col:
                        row.append('')
                    row[priv_col] = new_privacy
                    sheets_update(f'PUBLICADOS!A{i+1}:J{i+1}', [row])
                    break

        self.send_json(200, {'ok': True, 'clip_video_id': clip_id, 'privacy': new_privacy})

    def handle_clip_delete(self, data):
        """Delete a published clip from YouTube."""
        clip_id = data.get('clip_video_id')
        if not clip_id:
            self.send_json(400, {'error': 'clip_video_id required'})
            return

        # Delete from YouTube
        token = get_access_token()
        api_key = os.environ.get('API_KEY', '')
        url = f'https://www.googleapis.com/youtube/v3/videos?id={clip_id}&key={api_key}'
        req = urllib.request.Request(url, method='DELETE')
        req.add_header('Authorization', f'Bearer {token}')

        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            if e.code != 204:
                error_body = e.read().decode()
                self.send_json(500, {'error': error_body})
                return

        # Remove from spreadsheet
        result = sheets_get('PUBLICADOS!A1:J1000')
        rows = result.get('values', [])
        if rows:
            headers = rows[0]
            id_col = headers.index('clip_video_id') if 'clip_video_id' in headers else 0
            new_rows = [headers]
            for row in rows[1:]:
                if len(row) > id_col and row[id_col] == clip_id:
                    continue
                new_rows.append(row)

            # Clear and rewrite
            sheets_api('POST', f'values/{urllib.parse.quote("PUBLICADOS!A1:J1000")}:clear', {})
            if len(new_rows) > 0:
                sheets_update(f'PUBLICADOS!A1:J{len(new_rows)}', new_rows)

        self.send_json(200, {'ok': True, 'deleted': clip_id})

    def handle_pipeline_toggle(self, data):
        """Toggle pipeline pause flags in CONFIG sheet."""
        target = data.get('target', 'cortes')  # cortes | pub
        key = 'pipeline_cortes_paused' if target == 'cortes' else 'pipeline_pub_paused'

        # Read current config
        result = sheets_get('CONFIG!A1:B50')
        rows = result.get('values', [])

        current = 'false'
        found_idx = -1
        for i, row in enumerate(rows):
            if len(row) >= 1 and row[0] == key:
                current = row[1] if len(row) >= 2 else 'false'
                found_idx = i
                break

        new_val = 'false' if current == 'true' else 'true'

        if found_idx >= 0:
            rows[found_idx] = [key, new_val]
            sheets_update('CONFIG!A1:B' + str(len(rows)), rows)
        else:
            sheets_append('CONFIG!A1', [[key, new_val]])

        self.send_json(200, {'ok': True, 'target': target, 'paused': new_val == 'true'})

    def handle_live_reprocess(self, data):
        """Reset status_cortes (and optionally status_transcricao) to allow reprocessing."""
        video_id = data.get('video_id', '')
        if not video_id:
            self.send_json(400, {'error': 'video_id required'})
            return

        result = sheets_get('LIVES!A1:L1000')
        rows = result.get('values', [])
        if len(rows) < 2:
            self.send_json(404, {'error': 'no lives found'})
            return

        headers = rows[0]
        cortes_col = headers.index('status_cortes') if 'status_cortes' in headers else -1
        trans_col = headers.index('status_transcricao') if 'status_transcricao' in headers else -1
        vid_col = headers.index('video_id') if 'video_id' in headers else 0

        found = False
        for i, row in enumerate(rows[1:], 1):
            vid = row[vid_col] if vid_col < len(row) else ''
            if vid == video_id:
                while len(row) <= max(cortes_col, trans_col):
                    row.append('')
                if cortes_col >= 0:
                    row[cortes_col] = 'pendente'
                if trans_col >= 0:
                    row[trans_col] = 'pendente'
                sheets_update(f'LIVES!A{i+1}:L{i+1}', [row])
                # Clean local files to force re-download
                import shutil
                job_dir = os.path.join(os.path.dirname(__file__), '..', 'lives', video_id)
                if os.path.exists(job_dir):
                    shutil.rmtree(job_dir)
                found = True
                break

        if found:
            self.send_json(200, {'ok': True, 'video_id': video_id})
        else:
            self.send_json(404, {'error': f'video_id {video_id} not found'})

    def handle_clip_pause(self, data):
        """Toggle paused status of a clip in clips_manifest.json."""
        live_id = data.get('live_video_id', '')
        title = data.get('title', '')
        if not live_id or not title:
            self.send_json(400, {'error': 'live_video_id and title required'})
            return

        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        manifest_path = os.path.join(lives_dir, live_id, 'clips_manifest.json')
        if not os.path.exists(manifest_path):
            self.send_json(404, {'error': 'manifest not found'})
            return

        with open(manifest_path) as f:
            clips = json.load(f)

        found = False
        for clip in clips:
            if clip.get('title', '') == title:
                clip['paused'] = not clip.get('paused', False)
                found = True
                new_state = clip['paused']
                break

        if found:
            with open(manifest_path, 'w') as f:
                json.dump(clips, f, ensure_ascii=False, indent=2)
            self.send_json(200, {'ok': True, 'paused': new_state})
        else:
            self.send_json(404, {'error': 'clip not found in manifest'})

    def handle_cleanup_clips(self, data):
        """Deleta arquivos mp4 dos clips do disco. Mantem manifest e planilha."""
        video_id = data.get('video_id', '')  # opcional: limpar só uma live
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        deleted = 0
        freed = 0

        if video_id:
            dirs = [os.path.join(lives_dir, video_id)]
        else:
            dirs = [os.path.join(lives_dir, d) for d in os.listdir(lives_dir)
                    if os.path.isdir(os.path.join(lives_dir, d))]

        for job_dir in dirs:
            clips_dir = os.path.join(job_dir, 'clips')
            if not os.path.isdir(clips_dir):
                continue
            for f in os.listdir(clips_dir):
                if f.endswith('.mp4'):
                    fpath = os.path.join(clips_dir, f)
                    freed += os.path.getsize(fpath)
                    os.remove(fpath)
                    deleted += 1

        freed_mb = freed / 1024 / 1024
        self.send_json(200, {'ok': True, 'deleted': deleted, 'freed_mb': round(freed_mb, 1)})

    def handle_cleanup_sources(self, data):
        """Deleta arquivos source.mp4 (videos originais) do disco. Mantem clips e manifest."""
        video_id = data.get('video_id', '')  # opcional: limpar só uma live
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        deleted = 0
        freed = 0

        if video_id:
            dirs = [os.path.join(lives_dir, video_id)]
        else:
            dirs = [os.path.join(lives_dir, d) for d in os.listdir(lives_dir)
                    if os.path.isdir(os.path.join(lives_dir, d))]

        for job_dir in dirs:
            source = os.path.join(job_dir, 'source.mp4')
            if os.path.exists(source):
                freed += os.path.getsize(source)
                os.remove(source)
                deleted += 1

        freed_mb = freed / 1024 / 1024
        self.send_json(200, {'ok': True, 'deleted': deleted, 'freed_mb': round(freed_mb, 1)})

    def handle_live_delete(self, data):
        """Deleta live: remove arquivos do disco E remove da planilha LIVES."""
        import shutil
        video_id = data.get('video_id', '')
        if not video_id:
            self.send_json(400, {'error': 'video_id required'})
            return

        # Remove da planilha
        result = sheets_get('LIVES!A1:L1000')
        rows = result.get('values', [])
        if len(rows) < 2:
            self.send_json(404, {'error': 'no lives'})
            return

        headers = rows[0]
        vid_col = headers.index('video_id') if 'video_id' in headers else 0
        found_row = None
        for i, row in enumerate(rows[1:], 2):
            vid = row[vid_col] if vid_col < len(row) else ''
            if vid == video_id:
                found_row = i
                break

        if not found_row:
            self.send_json(404, {'error': f'{video_id} not found'})
            return

        # Delete row from sheet
        token = get_access_token()
        delete_body = json.dumps({
            'requests': [{
                'deleteDimension': {
                    'range': {
                        'sheetId': 0,
                        'dimension': 'ROWS',
                        'startIndex': found_row - 1,
                        'endIndex': found_row
                    }
                }
            }]
        }).encode()
        req = urllib.request.Request(
            f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}:batchUpdate',
            data=delete_body, method='POST')
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', 'application/json')
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            self.send_json(500, {'error': f'Erro ao deletar da planilha: {e}'})
            return

        # Remove arquivos do disco
        lives_dir = os.environ.get('LIVES_DIR', os.path.expanduser('~/projetos/gws/lives'))
        job_dir = os.path.join(lives_dir, video_id)
        freed = 0
        if os.path.exists(job_dir):
            for root, dirs, files in os.walk(job_dir):
                for f in files:
                    freed += os.path.getsize(os.path.join(root, f))
            shutil.rmtree(job_dir)

        freed_mb = freed / 1024 / 1024
        self.send_json(200, {'ok': True, 'video_id': video_id, 'freed_mb': round(freed_mb, 1)})


    def handle_thumbs_pending(self):
        """List pending thumbnails."""
        pending_file = os.path.join(os.path.dirname(__file__), '..', 'lives', 'pending_thumbs.json')
        thumb_dir = os.path.join(os.path.dirname(__file__), '..', 'lives', 'thumbs')
        if not os.path.exists(pending_file):
            self.send_json(200, {'pending': [], 'total': 0})
            return
        with open(pending_file) as f:
            clips = json.load(f)
        # Enrich with has_image flag
        for clip in clips:
            thumb_path = os.path.join(thumb_dir, f"{clip['id']}.jpg")
            clip['has_image'] = os.path.exists(thumb_path)
        self.send_json(200, {'pending': clips, 'total': len(clips)})

    def handle_thumbs_upload(self, data):
        """Upload pending thumbnails to YouTube."""
        pending_file = os.path.join(os.path.dirname(__file__), '..', 'lives', 'pending_thumbs.json')
        thumb_dir = os.path.join(os.path.dirname(__file__), '..', 'lives', 'thumbs')

        if not os.path.exists(pending_file):
            self.send_json(200, {'ok': True, 'uploaded': 0, 'errors': 0, 'remaining': 0})
            return

        with open(pending_file) as f:
            clips = json.load(f)

        if not clips:
            self.send_json(200, {'ok': True, 'uploaded': 0, 'errors': 0, 'remaining': 0})
            return

        # Import upload function from scheduler
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        from scheduler import upload_thumbnail

        uploaded = 0
        errors = 0
        remaining = []
        error_details = []

        for clip in clips:
            vid = clip['id']
            thumb_path = os.path.join(thumb_dir, f'{vid}.jpg')

            if not os.path.exists(thumb_path):
                remaining.append(clip)
                continue

            try:
                upload_thumbnail(vid, thumb_path)
                uploaded += 1
            except Exception as e:
                err_msg = str(e)
                if 'quota' in err_msg.lower():
                    remaining.append(clip)
                    # Add remaining clips that haven't been processed
                    idx = clips.index(clip)
                    remaining.extend(clips[idx + 1:])
                    error_details.append('Quota excedida - parou')
                    break
                errors += 1
                error_details.append(f'{clip.get("title", vid)[:40]}: {err_msg[:60]}')
                remaining.append(clip)

        # Update pending file
        with open(pending_file, 'w') as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)

        self.send_json(200, {
            'ok': True,
            'uploaded': uploaded,
            'errors': errors,
            'remaining': len(remaining),
            'error_details': error_details
        })


def parse_duration_minutes(iso_duration):
    """Parse ISO 8601 duration like PT1H30M15S to minutes."""
    import re
    hours = re.search(r'(\d+)H', iso_duration)
    minutes = re.search(r'(\d+)M', iso_duration)
    seconds = re.search(r'(\d+)S', iso_duration)

    total = 0
    if hours:
        total += int(hours.group(1)) * 60
    if minutes:
        total += int(minutes.group(1))
    if seconds:
        total += int(seconds.group(1)) / 60

    return round(total)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT

    # Start scheduler in background thread
    import threading
    scheduler_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    sys.path.insert(0, scheduler_dir)
    try:
        from scheduler import main as scheduler_main
        t = threading.Thread(target=scheduler_main, daemon=True, name='scheduler')
        t.start()
        print(f'Scheduler iniciado em background thread', file=sys.stderr)
    except Exception as e:
        print(f'ERRO ao iniciar scheduler: {e}', file=sys.stderr)

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(('0.0.0.0', port), DashboardHandler)
    print(f'Dashboard rodando em http://localhost:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServidor encerrado.')
