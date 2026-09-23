#!/data/data/com.termux/files/usr/bin/bash
# JARVIS – Installation auf Android (Termux)
# Aufruf in Termux:  bash install-termux.sh
set -e

JARVIS_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/jarvis-data"
ENV_FILE="$DATA_DIR/.env"

echo "==> Pakete installieren …"
pkg update -y
pkg install -y python git termux-api

echo "==> Zugriff auf den Handyspeicher erlauben (bitte im Dialog bestätigen) …"
[ -d "$HOME/storage" ] || termux-setup-storage || true

mkdir -p "$DATA_DIR"
if [ ! -f "$ENV_FILE" ]; then
  cp "$JARVIS_DIR/.env.example" "$ENV_FILE"
  echo
  echo "Welches KI-Modell soll Jarvis benutzen?"
  echo "  1) Google Gemini   – kostenlos (empfohlen)"
  echo "  2) Groq            – kostenlos, sehr schnell"
  echo "  3) OpenRouter      – kostenlose Modelle"
  echo "  4) Mistral         – kostenlos"
  echo "  5) Claude          – kostenpflichtig, beste Qualität"
  read -r -p "Auswahl [1]: " CHOICE
  case "$CHOICE" in
    2) PROVIDER=groq;       KEYVAR=GROQ_API_KEY;       URL="https://console.groq.com/keys" ;;
    3) PROVIDER=openrouter; KEYVAR=OPENROUTER_API_KEY; URL="https://openrouter.ai/keys" ;;
    4) PROVIDER=mistral;    KEYVAR=MISTRAL_API_KEY;    URL="https://console.mistral.ai/api-keys" ;;
    5) PROVIDER=anthropic;  KEYVAR=ANTHROPIC_API_KEY;  URL="https://console.anthropic.com/" ;;
    *) PROVIDER=gemini;     KEYVAR=GEMINI_API_KEY;     URL="https://aistudio.google.com/apikey" ;;
  esac
  echo
  echo "Schlüssel bekommst du hier: $URL"
  read -r -p "Dein API-Schlüssel: " KEY
  read -r -p "Wie soll Jarvis dich nennen? " NAME
  sed -i "s|^JARVIS_PROVIDER=.*|JARVIS_PROVIDER=$PROVIDER|" "$ENV_FILE"
  sed -i "s|^GEMINI_API_KEY=.*|# GEMINI_API_KEY=|" "$ENV_FILE"
  sed -i "s|^#\? *$KEYVAR=.*|$KEYVAR=$KEY|" "$ENV_FILE"
  sed -i "s|^JARVIS_USER_NAME=.*|JARVIS_USER_NAME=$NAME|" "$ENV_FILE"
fi

# Claude braucht das Paket 'anthropic' (wird für Android gebaut, dauert 10–20 Minuten).
# Ältere Installationen ohne JARVIS_PROVIDER, aber mit Claude-Schlüssel, zählen auch dazu.
if grep -q "^JARVIS_PROVIDER=anthropic" "$ENV_FILE" \
   || { ! grep -q "^JARVIS_PROVIDER=" "$ENV_FILE" && grep -q "^ANTHROPIC_API_KEY=sk-" "$ENV_FILE"; }; then
  echo "==> Claude-Unterstützung installieren (kann 10–20 Minuten dauern) …"
  pkg install -y rust binutils
  pip install -r "$JARVIS_DIR/requirements-claude.txt"
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
echo "Verfügbare Modelle:          jarvis models"
echo "Was Jarvis über dich weiß:   jarvis profile"
echo "Einstellungen ändern:        nano ~/jarvis-data/.env"
echo "Tipp: Im Browser-Menü 'Zum Startbildschirm hinzufügen' → Jarvis wie eine App öffnen."
