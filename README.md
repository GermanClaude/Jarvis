# JARVIS – dein persönlicher KI-Assistent & Second Brain

Jarvis ist ein KI-Assistent, der **auf deinem Handy läuft**, dich mit der Zeit kennenlernt und
wie ein zweites Gehirn für dich arbeitet: Er merkt sich, wer du bist, wie du denkst und was dir
wichtig ist, und er kann auf deinem Handy Dateien verwalten, Code schreiben und ausführen und
Apps öffnen. Das Denken übernimmt Claude (Anthropic API).

## Was Jarvis kann

| Bereich | Fähigkeiten |
|---|---|
| **Second Brain** | Langzeitgedächtnis über dich (Identität, Vorlieben, Ziele, Projekte, Menschen, Gewohnheiten, **Denkweise**, **Kommunikationsstil**, Wissen). Notizen als Markdown-Dateien mit `[[Verlinkungen]]` (kompatibel mit Obsidian). Zusammenfassungen früherer Gespräche. |
| **Von dir lernen** | Speichert Neues sofort, während ihr redet. Zusätzlich liest er alle paar Nachrichten das Gespräch nochmal durch, zieht neue Erkenntnisse heraus, korrigiert Veraltetes und schreibt ein Gesprächsprotokoll. |
| **Denken wie du** | Dein Profil steckt in jeder Anfrage – Antworten, Entscheidungen und Texte „in deinem Namen“ richten sich nach deinen Werten und deinem Stil. |
| **Alltag** | Aufgaben & Erinnerungen, Planung, Websuche, Recherchen, Texte, Rechnen, Fotos analysieren. |
| **Handy steuern** | Dateien lesen/schreiben/bearbeiten/verschieben/suchen, Python & Shell ausführen (in Termux: `pkg`, `git`, `node` …), Apps/Links/Dateien öffnen, Benachrichtigungen, Vorlesen, Zwischenablage, Taschenlampe, Akku, Standort, Kamera. |
| **Oberfläche** | Web-App im Iron-Man-Look: Chat mit Spracheingabe 🎤 und Vorlesen 🔊, Bilder anhängen, Gedächtnis ansehen/bearbeiten, Notizen, Aufgaben, Dateimanager mit Code-Editor. Als App auf den Startbildschirm legbar. |

## Installation auf Android

1. **Termux** und **Termux:API** aus [F-Droid](https://f-droid.org) installieren
   (die Play-Store-Version von Termux ist veraltet). Optional: **Termux:Widget** für ein Homescreen-Symbol.
2. API-Schlüssel holen: <https://console.anthropic.com/> → *API Keys*.
3. In Termux:
   ```bash
   pkg install -y git
   git clone https://github.com/GermanClaude/Jarvis.git ~/Jarvis
   bash ~/Jarvis/install-termux.sh
   ```
   Das Skript installiert alles, fragt nach Schlüssel und Namen und legt den Befehl `jarvis` an.
   Der erste Durchlauf kann 10–20 Minuten dauern, weil einige Python-Pakete für Android gebaut werden.
4. Starten:
   ```bash
   jarvis
   ```
   Der Browser öffnet sich mit `http://127.0.0.1:8765`. Im Chrome-Menü **„Zum Startbildschirm hinzufügen“**
   – dann startet Jarvis wie eine normale App.

Weitere Befehle:

```bash
jarvis chat      # im Terminal chatten
jarvis profile   # zeigen, was Jarvis über dich weiß
jarvis reflect   # aus allen bisherigen Gesprächen lernen
```

> **Tipp:** Damit Android Termux im Hintergrund nicht beendet, in den App-Einstellungen von Termux
> die Akku-Optimierung ausschalten. `jarvis` setzt außerdem automatisch einen Wake-Lock.

**Erster Schritt:** Tippe im Chat auf *„Lass uns ein Kennenlern-Interview machen“* – so lernt Jarvis
dich am schnellsten kennen.

## Auf dem PC (Linux/macOS/Windows)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m jarvis
```

## Einstellungen

Alle Einstellungen stehen in `~/jarvis-data/.env` (Vorlage: [`.env.example`](.env.example)):

| Variable | Standard | Bedeutung |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Dein API-Schlüssel (Pflicht) |
| `JARVIS_USER_NAME` | – | Wie Jarvis dich nennt |
| `JARVIS_MODEL` | `claude-opus-5` | Claude-Modell |
| `JARVIS_EFFORT` | `high` | Denktiefe: `low` (günstig, schnell) … `max` |
| `JARVIS_WORKSPACE` | `~` | Arbeitsordner für Dateien & Code |
| `JARVIS_EXTRA_ROOTS` | Handyspeicher | Weitere erlaubte Ordner (`:`-getrennt) |
| `JARVIS_ALLOW_SHELL` | `1` | Shell-Befehle erlauben |
| `JARVIS_WEB_TOOLS` | `1` | Websuche & Webseiten lesen |
| `JARVIS_FALLBACKS` | `1` | Lehnt das Modell eine Anfrage ab, übernimmt automatisch ein Ersatzmodell |
| `JARVIS_REFLECT_EVERY` | `6` | Nach so vielen Nachrichten lernt Jarvis im Hintergrund |
| `JARVIS_HOST` / `JARVIS_PORT` | `127.0.0.1` / `8765` | `0.0.0.0` macht Jarvis im WLAN erreichbar |
| `JARVIS_TOKEN` | automatisch | Zugangscode (Pflicht, sobald Jarvis im Netz erreichbar ist) |

## Wo deine Daten liegen

Alles bleibt auf deinem Gerät in `~/jarvis-data/`:

- `jarvis.db` – Gedächtnis, Aufgaben, Gesprächsverläufe (SQLite)
- `brain/*.md` – deine Notizen als normale Markdown-Dateien

Zum Denken werden Nachrichten, dein Profil und Werkzeug-Ergebnisse an die Anthropic API geschickt.

## Sicherheit

- Jarvis darf nur in deinem Arbeitsordner, `~/jarvis-data` und dem Handyspeicher auf Dateien zugreifen.
- Zerstörerische Befehle (System/Home löschen, Formatieren …) sind hart gesperrt.
- Vor Löschen und anderen nicht umkehrbaren Aktionen fragt Jarvis nach.
- Standardmäßig ist der Server nur auf dem Handy selbst erreichbar. Wer ihn im WLAN freigibt,
  bekommt automatisch einen Zugangscode.
- Mit `JARVIS_ALLOW_SHELL=0` lässt sich die Shell komplett abschalten.

## Aufbau

```
jarvis/
  agent.py    Denk-Kern: Gesprächsschleife mit Werkzeugen, Lernen (Reflexion)
  brain.py    Second Brain: Fakten, Notizen, Episoden, Aufgaben, Gespräche
  tools.py    Werkzeuge: Gedächtnis, Dateien, Code, Handy (Termux:API)
  server.py   Webserver (nur Python-Standardbibliothek) mit Streaming
  web/        Handy-Oberfläche (PWA)
tests/        Offline-Tests (ohne API-Schlüssel lauffähig)
```

Tests ausführen: `python -m unittest discover -s tests`

## Ideen für später

- Proaktive Erinnerungen per Benachrichtigung (Termux:Job / Cron)
- Kalender, E-Mail, Messenger anbinden
- Wake-Word („Hey Jarvis“) und dauerhaftes Zuhören
