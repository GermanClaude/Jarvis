#!/data/data/com.termux/files/usr/bin/bash
# JARVIS – Installation auf Android (Termux)
# Aufruf in Termux:  bash install-termux.sh
set -e

JARVIS_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/jarvis-data"

echo "==> Pakete installieren (beim ersten Mal kann das 10–20 Minuten dauern) …"
pkg update -y
# rust + binutils werden gebraucht, um pydantic-core und jiter für Android zu bauen.
pkg install -y python git rust binutils termux-api

echo "==> Python-Abhängigkeiten installieren …"
pip install --upgrade pip
pip install -r "$JARVIS_DIR/requirements.txt"

echo "==> Zugriff auf den Handyspeicher erlauben (bitte im Dialog bestätigen) …"
[ -d "$HOME/storage" ] || termux-setup-storage || true

mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/.env" ]; then
  cp "$JARVIS_DIR/.env.example" "$DATA_DIR/.env"
  echo
  read -r -p "Dein Anthropic API-Schlüssel (sk-ant-…): " KEY
  read -r -p "Wie soll Jarvis dich nennen? " NAME
  sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$KEY|" "$DATA_DIR/.env"
  sed -i "s|^JARVIS_USER_NAME=.*|JARVIS_USER_NAME=$NAME|" "$DATA_DIR/.env"
fi

echo "==> Befehl 'jarvis' anlegen …"
cat > "$PREFIX/bin/jarvis" <<LAUNCH
#!/data/data/com.termux/files/usr/bin/bash
cd "$JARVIS_DIR"
if [ "\$1" = "" ] || [ "\$1" = "serve" ]; then
  termux-wake-lock 2>/dev/null || true
  (sleep 2 && termux-open-url "http://127.0.0.1:8765") &
fi
exec python -m jarvis "\$@"
LAUNCH
chmod +x "$PREFIX/bin/jarvis"

echo "==> Homescreen-Verknüpfung für Termux:Widget anlegen …"
mkdir -p "$HOME/.shortcuts"
printf '#!/data/data/com.termux/files/usr/bin/bash\njarvis\n' > "$HOME/.shortcuts/Jarvis"
chmod +x "$HOME/.shortcuts/Jarvis"

echo
echo "Fertig! Starte Jarvis mit:   jarvis"
echo "Terminal-Chat:               jarvis chat"
echo "Was Jarvis über dich weiß:   jarvis profile"
echo "Tipp: Im Browser-Menü 'Zum Startbildschirm hinzufügen' → Jarvis wie eine App öffnen."
