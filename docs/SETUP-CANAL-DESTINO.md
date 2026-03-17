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

## 8. Thumbnails personalizadas

Para que o pipeline consiga fazer upload de thumbnails personalizadas, o canal de destino
precisa ter o **telefone verificado** no YouTube.

Sem essa verificacao, o upload de videos funciona normalmente, mas thumbnails retornam:
```
HTTP 403: The authenticated user doesn't have permissions to upload and set custom video thumbnails.
```

Para verificar:
1. Acesse https://www.youtube.com/verify com a conta dona do canal de destino
2. Verifique o numero de telefone
3. Apos verificacao, thumbnails serao enviadas automaticamente pelo pipeline

As thumbnails sao geradas via IA (LLM + gerador de imagem) e salvas em `lives/thumbs/`.
Caso o upload falhe, ficam como pendentes e podem ser reenviadas pelo dashboard.

---

## 9. Discussao futura: como gerenciar mudanca de canais

Hoje os dados dos canais estao em dois lugares:

1. **`config/.env`** — `YOUTUBE_CHANNEL_ID` (origem) e credenciais OAuth (determinam o destino)
2. **Planilha CONFIG** — `canal_origem_nome`, `canal_destino_nome`, etc. (visual, pro dashboard)

### Mudar canal de ORIGEM (de onde vem as lives)
Simples — trocar `YOUTUBE_CHANNEL_ID` no `.env` e os campos `canal_origem_*` na planilha CONFIG.

### Mudar canal de DESTINO (onde publica os clips)
Complexo — envolve:
- Novas credenciais OAuth (outra conta Google)
- Novo `credentials.enc` (reautenticacao via `yt-auth`)
- Possivelmente nova planilha e novo projeto GCP

### Opcoes de arquitetura a considerar:

**A) Manter como esta** — `.env` e a fonte da verdade, dashboard so mostra. Para mudar, edita o `.env` e a planilha manualmente. Simples, funciona para "configura uma vez e roda".

**B) Editavel pelo dashboard** — campos de canal editaveis no painel de config, salvando na planilha e no `.env`. Mudar o destino ainda precisaria de reautenticacao OAuth.

**C) Multi-canal** — um unico projeto suporta multiplos destinos com dropdown para selecionar. Complexidade maior, mas permite escalar para varios canais.

**Decisao pendente** — definir se o caso de uso e "configura uma vez e roda" ou se precisa trocar canais com frequencia.

---

## 10. Checklist final

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
- [x] Prompts de IA copiados para `config/`
- [x] 116 lives de Jan-Mar 2025 sincronizadas
- [x] Pipeline testado: corte (9 clips) + publicacao (9 clips publicos)
- [x] Thumbnails geradas com IA (9 imagens)
- [ ] Verificar telefone do canal para habilitar custom thumbnails
- [ ] Upload das thumbnails pendentes apos verificacao
- [ ] Instalar systemd services
