# Setup Completo — Canal de Destino (INEMA TIA)

Documento de referencia do processo completo de configuracao do projeto `yt-pub-lives2`
para publicar clips em um canal de destino diferente do canal de origem.

- **Canal de origem** (lives): INEMA TDS (`UC2QbQDyPKuHk93dwo5iq3Sw`)
- **Canal de destino** (clips): INEMA TIA (`UCavuQHkxBSAZbzRoOm6Gq4g` / `@InemaTIA`)
- **Data**: 2026-03-16

---

## 1. Criacao do projeto GCP para o canal de destino

### O que foi feito (no Google Cloud Console):
1. Criar projeto GCP: `certain-perigee-490501-r2`
2. Ativar APIs:
   - **YouTube Data API v3**
   - **Google Sheets API**
3. Configurar **OAuth Consent Screen**:
   - Tipo: External
   - Nome do app: `webyt`
   - Modo: Testing
4. Criar **OAuth Client ID** (tipo Desktop App)
5. Criar **API Key** para YouTube Data API

### Credenciais geradas:
- Client ID: (armazenado em `config/.env`)
- Client Secret: (armazenado em `config/.env`)
- API Key: (armazenado em `config/.env`)

---

## 2. Isolamento da configuracao dentro do projeto

### Problema:
O projeto original (`yt-pub-lives`) usava configuracao global em `~/.config/gws/`.
Rodar dois projetos ao mesmo tempo causaria conflito (mesmos arquivos de config/credenciais).

### Solucao:
Toda configuracao foi movida para `./config/` dentro do projeto.

### Arquivos alterados:

| Arquivo | Antes (global) | Depois (local) |
|---|---|---|
| `scheduler.py` | `~/.config/gws` | `<projeto>/config` |
| `dashboard/server.py` | `~/.config/gws` | `<projeto>/config` |
| `scripts/yt-clip` | `~/.config/gws` | `<script_dir>/../config` |
| `scripts/yt-publish` | `~/.config/gws` | `<script_dir>/../config` |
| `scripts/yt-thumbnail` | `~/.config/gws` | `<script_dir>/../config` |
| `systemd/*.service` | `~/.config/gws` | `/home/nmaldaner/projetos/yt-pub-lives2/config` |

### Valores hardcoded removidos:
- `SPREADSHEET_ID` em `server.py` e `scheduler.py` — agora vem do `.env`
- `YOUTUBE_CHANNEL_ID` default em `server.py` — agora vem do `.env`
- `LIVES_DIR` default — agora relativo ao projeto (`<projeto>/lives`)

### Porta alterada:
- Dashboard: `8090` → `8091` (para nao conflitar com o projeto original)
- Alterado em: `server.py`, `Dockerfile`, `docker-compose.yml`, `systemd/yt-dashboard.service`

---

## 3. Configuracao do `config/.env`

Arquivo criado com campos separados para origem e destino:

```env
# Canal de ORIGEM (de onde vem as lives)
YOUTUBE_CHANNEL_ID=<id-canal-origem>

# Canal de DESTINO (credenciais OAuth da conta do canal destino)
CLIENT_ID=<seu-client-id>.apps.googleusercontent.com
CLIENT_SECRET=GOCSPX-<seu-secret>
API_KEY=<sua-api-key>
GCP_PROJECT=<seu-projeto-gcp>
SPREADSHEET_ID=<id-da-planilha>
PIRAMYD_API_KEY=sk-<sua-chave>
```

---

## 4. Autenticacao OAuth

### Script criado: `scripts/yt-auth`

Script standalone que faz o fluxo OAuth completo sem depender do CLI `gws`:

```bash
python3 scripts/yt-auth
```

Fluxo:
1. Abre o browser na tela de login do Google
2. Usuario loga com a conta dona do canal de destino
3. Autoriza permissoes (YouTube + Sheets)
4. Callback capturado em `http://localhost:8888`
5. Troca codigo por tokens (access_token + refresh_token)
6. Encripta tokens com AES-GCM e salva em `config/credentials.enc`
7. Salva chave de encriptacao em `config/.encryption_key`
8. Testa acesso mostrando o nome do canal

### Problemas encontrados e solucoes:

| Problema | Solucao |
|---|---|
| `Error 400: redirect_uri_mismatch` | Adicionar `http://localhost:8888` nas Authorized redirect URIs do OAuth client no GCP Console |
| `Error 403: access_denied` — app nao verificado | Adicionar `inemafuturostds@gmail.com` como **test user** no OAuth Consent Screen do GCP |
| `Error 403: Forbidden` ao criar planilha | Ativar **Google Sheets API** no projeto GCP |

### Resultado:
```
Canal: INEMA TIA (UCavuQHkxBSAZbzRoOm6Gq4g)
```

---

## 5. Criacao da planilha Google Sheets

Planilha criada automaticamente via API com 3 abas:

- **URL**: https://docs.google.com/spreadsheets/d/19OwctluvWp4w_Md7-VGzbFh7WAHFUtxwdyw2nsseYbI
- **ID**: `19OwctluvWp4w_Md7-VGzbFh7WAHFUtxwdyw2nsseYbI`

### Abas:

**CONFIG** — pre-populada com valores padrao:
- `channel_id`: UCavuQHkxBSAZbzRoOm6Gq4g
- `ai_mode`: piramyd-api
- `privacy_padrao`: unlisted
- `pipeline_pub_paused`: true (seguranca — ativar manualmente)

**LIVES** — headers criados, pronta para sync

**PUBLICADOS** — headers criados, pronta para receber clips publicados

---

## 6. Repositorio Git

- Remote atualizado para: `git@github.com:inematds/yt-pub-lives2.git`
- `config/.env` e credenciais no `.gitignore` (nao vao pro repositorio)

---

## 7. Systemd Services

Services atualizados para rodar isolados do projeto original:

```
systemd/yt-dashboard.service  → porta 8091, config em yt-pub-lives2/config
systemd/yt-scheduler.service  → lives em yt-pub-lives2/lives, config em yt-pub-lives2/config
```

Para instalar:
```bash
sudo cp systemd/yt-dashboard.service /etc/systemd/system/yt-dashboard2.service
sudo cp systemd/yt-scheduler.service /etc/systemd/system/yt-scheduler2.service
sudo systemctl daemon-reload
sudo systemctl enable --now yt-dashboard2 yt-scheduler2
```

---

## 8. Checklist final

- [x] Projeto GCP criado (`certain-perigee-490501-r2`)
- [x] YouTube Data API v3 ativada
- [x] Google Sheets API ativada
- [x] OAuth Client ID criado (Desktop App)
- [x] Redirect URI `http://localhost:8888` cadastrada
- [x] Email `inemafuturostds@gmail.com` adicionado como test user
- [x] API Key criada
- [x] Config isolado em `./config/`
- [x] `config/.env` preenchido
- [x] `config/client_secret.json` criado
- [x] OAuth autenticado — `config/credentials.enc` + `.encryption_key` gerados
- [x] Planilha criada com abas CONFIG, LIVES, PUBLICADOS
- [x] Porta 8091 (sem conflito com projeto original na 8090)
- [x] Remote git atualizado para `inematds/yt-pub-lives2`
- [ ] Copiar prompts de IA para `config/` (prompt_cortes.txt, prompt_pub.txt, prompt_thumb.txt)
- [ ] Instalar systemd services
- [ ] Primeiro sync de lives pelo dashboard
- [ ] Testar pipeline completo (corte + publicacao)
