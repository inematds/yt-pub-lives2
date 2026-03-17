# yt-pub-lives2

Pipeline automatizado para cortar lives do YouTube em clips por topico e publicar em outro canal.

**Canal de origem** (lives): [INEMA TDS](https://www.youtube.com/@inematdsx) (`UC2QbQDyPKuHk93dwo5iq3Sw`)
**Canal de destino** (clips): [INEMA TIA](https://www.youtube.com/@InemaTIA) (`UCavuQHkxBSAZbzRoOm6Gq4g`)

## Fluxo

```
YouTube (lives do canal origem) → Transcricao → Analise IA → Corte (FFmpeg) → Thumbnail (IA) → Publicacao (canal destino)
```

1. **Sincroniza** lives do canal de origem via YouTube Data API
2. **Baixa transcricao** automatica (legendas do YouTube)
3. **Analisa topicos** com IA (Piramyd/Claude/OpenRouter API)
4. **Corta clips** com FFmpeg baseado nos timestamps
5. **Gera thumbnails** com IA (LLM + gerador de imagem) ou local
6. **Publica clips** no canal de destino com titulo, descricao, tags e thumbnail

## Estrutura

```
yt-pub-lives2/
├── config/                    # Configuracao isolada do projeto
│   ├── .env                   # Variaveis de ambiente (nao vai pro git)
│   ├── client_secret.json     # Credenciais OAuth (nao vai pro git)
│   ├── credentials.enc        # Tokens encriptados (nao vai pro git)
│   ├── .encryption_key        # Chave AES-GCM (nao vai pro git)
│   ├── prompt_cortes.txt      # Prompt IA para analise de topicos
│   ├── prompt_pub.txt         # Prompt IA para refinar titulo/descricao
│   └── prompt_thumb.txt       # Prompt IA para gerar thumbnails
├── dashboard/
│   ├── server.py              # Backend API (Python HTTP server)
│   └── index.html             # Frontend SPA (vanilla JS)
├── scripts/
│   ├── yt-auth                # Autenticacao OAuth standalone
│   ├── yt-clip                # Pipeline: transcricao → analise → corte
│   ├── yt-publish             # Upload de video para YouTube
│   ├── yt-thumbnail           # Gera thumbnails com IA
│   └── yt-dashboard           # Lanca o dashboard web
├── systemd/
│   ├── yt-dashboard.service   # Service systemd (porta 8091)
│   └── yt-scheduler.service   # Service systemd scheduler
├── scheduler.py               # Scheduler automatico
├── docker-compose.yml         # Docker (porta 8091)
├── Dockerfile
├── requirements.txt
├── setup.sh
└── docs/
    └── SETUP-CANAL-DESTINO.md # Documentacao completa do setup
```

## Requisitos

- Python 3.10+
- ffmpeg
- yt-dlp
- deno (runtime JS para yt-dlp)
- curl
- Pillow (thumbnails)

## Instalacao

```bash
git clone git@github.com:inematds/yt-pub-lives2.git
cd yt-pub-lives2
bash setup.sh
```

### 1. Configuracao Google Cloud

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com)
2. Ative as APIs:
   - **Google Sheets API**
   - **YouTube Data API v3**
3. Configure o **OAuth Consent Screen**:
   - Tipo: External
   - Modo: Testing
   - Adicione o email da conta do canal de destino como **test user**
4. Crie credenciais **OAuth 2.0** (tipo Desktop App)
   - Adicione `http://localhost:8888` nas **Authorized redirect URIs**
5. Crie uma **API Key**

### 2. Configuracao do projeto

Preencha `config/.env`:

```env
# Canal de ORIGEM (de onde vem as lives)
YOUTUBE_CHANNEL_ID=UC-id-do-canal-origem

# Canal de DESTINO (credenciais OAuth da conta dona do canal)
CLIENT_ID=seu-client-id.apps.googleusercontent.com
CLIENT_SECRET=GOCSPX-seu-secret
API_KEY=AIzaSy-sua-api-key
GCP_PROJECT=seu-projeto-id

# Planilha (criada automaticamente pelo yt-auth ou manualmente)
SPREADSHEET_ID=id-da-planilha

# Piramyd API (IA)
PIRAMYD_API_KEY=sk-sua-chave
```

### 3. Autenticacao OAuth

```bash
python3 scripts/yt-auth
```

Abre o browser, loga com a conta do canal de destino, autoriza, e salva os tokens encriptados em `config/`.

### 4. Planilha Google Sheets

A planilha pode ser criada automaticamente ou manualmente com 3 abas:

**CONFIG** (chave/valor):
```
chave,valor
channel_id,UC...
corte_auto,true
corte_horarios,"08:00,14:00,20:00"
corte_max_por_dia,3
pub_horarios,"09:00,15:00,21:00"
pub_max_por_vez,2
privacy_padrao,unlisted
ai_mode,piramyd-api
ai_model,claude-sonnet-4.5
thumb_mode,api
thumb_model,flux2-klein-4b
pipeline_cortes_paused,false
pipeline_pub_paused,true
```

**LIVES** (colunas):
```
video_id | titulo | data_live | duracao_min | url | status_transcricao | status_cortes | qtd_clips | clips_publicados | clips_pendentes | data_sync | observacoes
```

**PUBLICADOS** (colunas):
```
clip_video_id | clip_titulo | clip_url | live_video_id | live_titulo | data_publicacao | privacy | duracao | tags | categoria
```

### 5. Prompts de IA (opcional)

Copie os prompts personalizados para `config/`:
```bash
cp ~/caminho/prompt_cortes.txt config/
cp ~/caminho/prompt_pub.txt config/
cp ~/caminho/prompt_thumb.txt config/
```

Ou edite pelo dashboard na aba de configuracao.

## Uso

### Dashboard Web

```bash
python3 dashboard/server.py [porta]    # padrao: 8091
```

Acesse `http://localhost:8091` — painel com:
- Stats clicaveis (total lives, cortadas, pendentes, clips aguardando, publicados)
- Configuracao de horarios (picker visual 24h)
- Tabela de lives com filtro por status
- Aba Clips unificada: publicados + pendentes
- Controle de clips: pausar/retomar publicacao individual
- Reprocessar lives com erro
- Controle de privacy
- Configuracao de thumbnails
- Status do scheduler em tempo real

### Docker

```bash
docker-compose up -d
```

Dashboard em `http://localhost:8091`.

### Systemd

```bash
sudo cp systemd/yt-dashboard.service /etc/systemd/system/yt-dashboard2.service
sudo cp systemd/yt-scheduler.service /etc/systemd/system/yt-scheduler2.service
sudo systemctl daemon-reload
sudo systemctl enable --now yt-dashboard2 yt-scheduler2
```

### Cortar uma Live

```bash
yt-clip <video_id>                    # Modo manual (gera prompt)
yt-clip <video_id> --ai piramyd-api   # Modo automatico (Piramyd API)
yt-clip <video_id> --dry-run          # So mostra topicos
yt-clip <video_id> --publish          # Corta e publica
```

### Gerar Thumbnail

```bash
yt-thumbnail --title "Titulo do clip" --output thumb.jpg
```

### Publicar um Video

```bash
yt-publish video.mp4 --title "Titulo" --description "Descricao"
yt-publish video.mp4 --title "Titulo" --description "Desc" --privacy unlisted --tags "ia,dev"
```

## Diferenca do projeto original (yt-pub-lives)

| | yt-pub-lives | yt-pub-lives2 |
|---|---|---|
| Config | Global (`~/.config/gws/`) | Local (`./config/`) |
| Porta | 8090 | 8091 |
| Canal destino | Mesmo da origem | Diferente (INEMA TIA) |
| Auth | Via CLI `gws` | Script `yt-auth` standalone |
| Repositorio | `inematds/yt-pub-lives` | `inematds/yt-pub-lives2` |

## Tecnologias

- **Backend**: Python 3 (stdlib HTTPServer, sem frameworks)
- **Frontend**: HTML/CSS/JS vanilla (single page, sem build)
- **APIs**: YouTube Data API v3, Google Sheets API v4
- **IA**: Piramyd API / Anthropic Claude API / OpenRouter (analise de topicos + thumbnails)
- **Video**: FFmpeg (corte), yt-dlp (download)
- **Auth**: OAuth 2.0 com refresh token (AES-GCM encrypted)

## Licenca

Uso interno — INEMA TDS (@inematdsx)
