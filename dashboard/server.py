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
        elif path == '/api/stats':
            self.handle_api_stats()
        elif path == '/api/transcript':
            video_id = qs.get('id', [None])[0]
            self.handle_api_transcript(video_id)
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
        else:
            self.send_json(404, {'error': 'not found'})

    def send_json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

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

        self.send_json(200, {'publicados': publicados, 'total': len(publicados), 'filter': filter_live_id})

    def handle_api_transcript(self, video_id):
        """Return transcript for a video if available locally."""
        if not video_id:
            self.send_json(400, {'error': 'id parameter required'})
            return

        lives_dir = os.path.expanduser('~/projetos/gws/lives')
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
        result = sheets_get('CONFIG!A1:B20')
        rows = result.get('values', [])
        config = {}
        for row in rows[1:]:  # skip header
            if len(row) >= 2:
                config[row[0]] = row[1]
        self.send_json(200, {'config': config})

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
        result = sheets_get('CONFIG!A1:B20')
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
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    print(f'Dashboard rodando em http://localhost:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nServidor encerrado.')
