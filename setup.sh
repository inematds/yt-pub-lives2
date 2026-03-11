#!/usr/bin/env bash
# setup.sh — Configura o ambiente para o yt-pub-lives
set -euo pipefail

echo "==> yt-pub-lives — Setup"

# 1. Python dependencies
echo "==> Instalando dependencias Python..."
pip3 install --user -r requirements.txt

# 2. Check external tools
echo "==> Verificando ferramentas..."
for tool in python3 ffmpeg curl; do
  if command -v "$tool" &>/dev/null; then
    echo "    $tool: OK"
  else
    echo "    $tool: NAO ENCONTRADO — instale com: sudo apt-get install $tool"
  fi
done

if command -v yt-dlp &>/dev/null; then
  echo "    yt-dlp: OK"
else
  echo "    yt-dlp: NAO ENCONTRADO — instale com: pip3 install yt-dlp"
fi

# 3. Symlink scripts
echo "==> Instalando scripts CLI..."
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)/scripts"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

for script in yt-clip yt-publish yt-dashboard; do
  ln -sf "$SCRIPTS_DIR/$script" "$BIN_DIR/$script"
  echo "    $BIN_DIR/$script -> $SCRIPTS_DIR/$script"
done

# 4. Config directory
CONFIG_DIR="${HOME}/.config/gws"
mkdir -p "$CONFIG_DIR"

if [[ ! -f "$CONFIG_DIR/.env" ]]; then
  echo ""
  echo "==> ATENCAO: Crie o arquivo $CONFIG_DIR/.env com suas credenciais."
  echo "    Use .env.example como modelo."
  echo ""
fi

# 5. PATH check
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
  echo "==> Adicione ao seu ~/.bashrc:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""
echo "==> Setup completo!"
echo "    Para iniciar o dashboard: yt-dashboard"
echo "    Para cortar uma live:     yt-clip <video_id>"
echo "    Para publicar um video:   yt-publish <arquivo> --title 'Titulo' --description 'Desc'"
