# yt-pub-lives

Pipeline automatizado para cortar lives do YouTube em clips por topico e publicar de volta no canal.

## Fluxo

```
YouTube (lives) → Transcricao → Analise IA (Claude) → Corte (FFmpeg) → Thumbnail (IA) → Publicacao (YouTube API)
```

1. **Sincroniza** lives do canal via YouTube Data API
2. **Baixa transcricao** automatica (legendas do YouTube)
3. **Analisa topicos** com Claude (API ou manual)
4. **Corta clips** com FFmpeg baseado nos timestamps
5. **Gera thumbnails** com IA (LLM + gerador de imagem) ou local
6. **Publica clips** no YouTube com titulo, descricao, tags e thumbnail

## Estrutura

```
yt-pub-lives/
├── dashboard/
│   ├── server.py          # Backend API (Python HTTP server)
│   └── index.html         # Frontend SPA (vanilla JS)
├── scripts/
│   ├── yt-clip            # Pipeline: transcricao → analise → corte
│   ├── yt-publish         # Upload de video para YouTube
│   ├── yt-thumbnail       # Gera thumbnails com IA (LLM + imagem)
│   └── yt-dashboard       # Lanca o dashboard web
├── requirements.txt       # Dependencias Python
├── .env.example           # Template de variaveis de ambiente
├── setup.sh               # Script de instalacao
└── .gitignore
```

## Requisitos

- Python 3.10+
- ffmpeg
- yt-dlp
- curl

## Instalacao

```bash
git clone git@github.com:inematds/yt-pub-lives.git
cd yt-pub-lives
bash setup.sh
```

### Configuracao Google Cloud

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com)
2. Ative as APIs:
   - Google Sheets API
   - YouTube Data API v3
3. Crie credenciais OAuth 2.0 (Desktop App)
4. Copie `.env.example` para `~/.config/gws/.env` e preencha:
   - `CLIENT_ID` e `CLIENT_SECRET` do OAuth
   - `API_KEY` da API key
   - `YOUTUBE_CHANNEL_ID` do seu canal
   - `SPREADSHEET_ID` da planilha de controle
5. Execute o fluxo OAuth para gerar `credentials.enc`:
   ```bash
   # Use o gws CLI ou configure manualmente
   gws auth setup
   gws auth login
   ```

### Planilha Google Sheets

Crie uma planilha com 3 abas:

**LIVES** (colunas):
```
video_id | titulo | data_live | duracao_min | url | status_transcricao | status_cortes | qtd_clips | clips_publicados | clips_pendentes | data_sync | observacoes
```

**PUBLICADOS** (colunas):
```
clip_video_id | clip_titulo | clip_url | live_video_id | live_titulo | data_publicacao | privacy | duracao_seg | tags | categoria
```

**CONFIG** (chave/valor):
```
chave,valor
pub_horarios,"08:00,11:00,14:00,17:00"
privacy_padrao,unlisted
corte_auto,true
corte_horarios,"06:00,18:00"
corte_max_por_dia,3
channel_id,UC...
pipeline_cortes_paused,false
pipeline_pub_paused,false
thumb_mode,api
thumb_model,dreamshaper
thumb_api_key,sk-...
```

## Uso

### Dashboard Web

```bash
yt-dashboard [porta]    # padrao: 8090
```

Acesse `http://localhost:8090` — painel com:
- Stats (total lives, cortadas, pendentes, publicados)
- Configuracao de horarios (picker visual 24h)
- Tabela de lives com filtro, pesquisa e ordenacao
- Tabela de clips publicados com filtro por privacy
- Modais para ver transcricao e clips de cada live
- Controle de privacy (clique para alternar)
- Exclusao de clips (YouTube + planilha)
- Botoes para pausar/retomar cortes e publicacao
- Configuracao de thumbnails (modo, API key, modelo de imagem)

### Cortar uma Live

```bash
yt-clip <video_id>                    # Modo manual (gera prompt)
yt-clip <video_id> --ai claude-api    # Modo automatico (Claude API)
yt-clip <video_id> --dry-run          # So mostra topicos
yt-clip <video_id> --publish          # Corta e publica
```

### Gerar Thumbnail

```bash
yt-thumbnail --title "Titulo do clip" --output thumb.jpg
yt-thumbnail --title "Titulo" --description "Sobre o clip" --model dreamshaper
```

Pipeline em 3 etapas:
1. **LLM** (Claude Sonnet) analisa o titulo e gera um prompt estruturado (metafora visual, cores, composicao)
2. **Gerador de imagem** (Piramyd API) cria a cena de fundo sem texto
3. **Pillow** compoe o thumbnail final com texto em outline, accent line e brand

Modelos de imagem disponiveis (via Piramyd API):
- `dreamshaper` — artistico/chamativo (padrao)
- `flux2-klein-4b` — fotorealista
- `lucid-origin`, `qwen-image`, `sdxl-lite`, `z-image-turbo`

### Publicar um Video

```bash
yt-publish video.mp4 --title "Titulo" --description "Descricao"
yt-publish video.mp4 --title "Titulo" --description "Desc" --privacy unlisted --tags "ia,dev"
```

## Tecnologias

- **Backend**: Python 3 (stdlib HTTPServer, sem frameworks)
- **Frontend**: HTML/CSS/JS vanilla (single page, sem build)
- **APIs**: YouTube Data API v3, Google Sheets API v4
- **IA**: Anthropic Claude API (analise de topicos), Piramyd API (geracao de imagem)
- **Thumbnails**: Pillow (composicao), modelos de imagem via Piramyd (dreamshaper, flux2, etc.)
- **Video**: FFmpeg (corte), yt-dlp (download)
- **Auth**: OAuth 2.0 com refresh token (AES-GCM encrypted)

## Licenca

Uso interno — INEMA TDS (@inematdsx)
