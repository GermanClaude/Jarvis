# JARVIS – dein persönlicher KI-Assistent & Second Brain

Jarvis ist ein KI-Assistent, der **auf deinem Handy läuft**, dich mit der Zeit kennenlernt und
wie ein zweites Gehirn für dich arbeitet: Er merkt sich, wer du bist, wie du denkst und was dir
wichtig ist, und er kann auf deinem Handy Dateien verwalten, Code schreiben und ausführen und
Apps öffnen.

Das Denken übernimmt ein KI-Modell deiner Wahl – **kostenlos** mit Google Gemini, Groq, OpenRouter,
Mistral oder einem lokalen Modell (Ollama), oder kostenpflichtig mit Claude (Anthropic).

## Was Jarvis kann

| Bereich | Fähigkeiten |
|---|---|
| **Second Brain** | Langzeitgedächtnis über dich (Identität, Vorlieben, Ziele, Projekte, Menschen, Gewohnheiten, **Denkweise**, **Kommunikationsstil**, Wissen). Notizen als Markdown-Dateien mit `[[Verlinkungen]]` (kompatibel mit Obsidian). Zusammenfassungen früherer Gespräche. |
| **Von dir lernen** | Speichert Neues sofort, während ihr redet. Zusätzlich liest er alle paar Nachrichten das Gespräch nochmal durch, zieht neue Erkenntnisse heraus, korrigiert Veraltetes und schreibt ein Gesprächsprotokoll. |
| **Denken wie du** | Dein Profil steckt in jeder Anfrage – Antworten, Entscheidungen und Texte „in deinem Namen“ richten sich nach deinen Werten und deinem Stil. |
| **Alltag** | Aufgaben & Erinnerungen, Planung, Websuche (kostenlos über DuckDuckGo), Recherchen, Texte, Rechnen, Fotos analysieren. |
| **Handy steuern** | Dateien lesen/schreiben/bearbeiten/verschieben/suchen, Python & Shell ausführen (in Termux: `pkg`, `git`, `node` …), Apps/Links/Dateien öffnen, Benachrichtigungen, Vorlesen, Zwischenablage, Taschenlampe, Akku, Standort, Kamera. |
| **Oberfläche** | Web-App im Iron-Man-Look: Chat mit Spracheingabe 🎤 und Vorlesen 🔊, Bilder anhängen, Gedächtnis ansehen/bearbeiten, Notizen, Aufgaben, Dateimanager mit Code-Editor. Als App auf den Startbildschirm legbar. |

## Welches KI-Modell?

| Anbieter | Kosten | Stärken | Schlüssel holen |
|---|---|---|---|
| **Google Gemini** (empfohlen) | kostenlos* | gut mit Werkzeugen, versteht Bilder, großzügiges Gratis-Kontingent | <https://aistudio.google.com/apikey> |
| **Groq** | kostenlos* | extrem schnell | <https://console.groq.com/keys> |
| **OpenRouter** | kostenlos* (`:free`-Modelle) | viele Modelle zur Auswahl | <https://openrouter.ai/keys> |
| **Mistral** | kostenlos* | europäischer Anbieter | <https://console.mistral.ai/api-keys> |
| **Ollama** | kostenlos | läuft komplett auf deinem PC, privat | <https://ollama.com> |
| **Claude** (Anthropic) | ab 5 € Guthaben | beste Qualität bei langen Aufgaben | <https://console.anthropic.com/> |

\* Mit Tages- bzw. Minutenlimits. Die Bedingungen legen die Anbieter fest und ändern sie gelegentlich.
Bei kostenlosen Kontingenten dürfen manche Anbieter deine Eingaben zur Verbesserung ihrer Modelle
nutzen – lies dir die Bedingungen durch, bevor du Persönliches anvertraust.

## Installation auf Android

> Ausführliche Schritt-für-Schritt-Anleitung (Termux, PC, Updates, Backup, Fehlerbehebung): **[ANLEITUNG.md](ANLEITUNG.md)**

1. **Termux** und **Termux:API** aus [F-Droid](https://f-droid.org) installieren
   (die Play-Store-Version von Termux ist veraltet). Optional: **Termux:Widget** für ein Homescreen-Symbol.
2. Kostenlosen API-Schlüssel holen, z. B. bei Google Gemini: <https://aistudio.google.com/apikey>
   (siehe Tabelle oben).
3. In Termux:
   ```bash
   pkg install -y git
   git clone https://github.com/GermanClaude/Jarvis.git ~/Jarvis
   bash ~/Jarvis/install-termux.sh
   ```
   Das Skript installiert alles, fragt nach Anbieter, Schlüssel und Namen und legt den Befehl `jarvis` an.
   Mit einem kostenlosen Anbieter dauert das nur wenige Minuten; mit Claude 10–20 Minuten,
   weil dafür Python-Pakete für Android gebaut werden.
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
jarvis models    # verfügbare Modelle deines Anbieters anzeigen
```

> **Tipp:** Damit Android Termux im Hintergrund nicht beendet, in den App-Einstellungen von Termux
> die Akku-Optimierung ausschalten. `jarvis` setzt außerdem automatisch einen Wake-Lock.

**Erster Schritt:** Tippe im Chat auf *„Lass uns ein Kennenlern-Interview machen“* – so lernt Jarvis
dich am schnellsten kennen.

## Auf dem PC (Linux/macOS/Windows)

Für die kostenlosen Anbieter braucht Jarvis nur Python 3.10+ – keine Zusatzpakete:

```bash
export JARVIS_PROVIDER=gemini
export GEMINI_API_KEY=...
python -m jarvis
```

Für Claude zusätzlich `pip install -r requirements-claude.txt` und `ANTHROPIC_API_KEY` setzen.

## Einstellungen

Alle Einstellungen stehen in `~/jarvis-data/.env` (Vorlage: [`.env.example`](.env.example)):

| Variable | Standard | Bedeutung |
|---|---|---|
| `JARVIS_PROVIDER` | automatisch | `gemini`, `groq`, `openrouter`, `mistral`, `ollama`, `anthropic` oder `openai` (beliebiger OpenAI-kompatibler Dienst). Ohne Angabe: der Anbieter, dessen Schlüssel gesetzt ist |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`, `ANTHROPIC_API_KEY` | – | Schlüssel des gewählten Anbieters (Ollama braucht keinen) |
| `JARVIS_MODEL` | je nach Anbieter | Modell, z. B. `gemini-flash-latest`, `llama-3.3-70b-versatile`, `claude-opus-5`. Liste: `jarvis models` |
| `JARVIS_BASE_URL` | je nach Anbieter | Adresse des Dienstes, z. B. Ollama auf dem PC: `http://192.168.178.20:11434/v1` |
| `JARVIS_MAX_TOKENS` | `8192` | Maximale Antwortlänge (nicht für Claude) |
| `JARVIS_EFFORT` | `high` | Nur Claude: Denktiefe `low` (günstig, schnell) … `max` |
| `JARVIS_FALLBACKS` | `1` | Nur Claude: lehnt das Modell eine Anfrage ab, übernimmt automatisch ein Ersatzmodell |
| `JARVIS_USER_NAME` | – | Wie Jarvis dich nennt |
| `JARVIS_WORKSPACE` | `~` | Arbeitsordner für Dateien & Code |
| `JARVIS_EXTRA_ROOTS` | Handyspeicher | Weitere erlaubte Ordner (`:`-getrennt) |
| `JARVIS_ALLOW_SHELL` | `1` | Shell-Befehle erlauben |
| `JARVIS_WEB_TOOLS` | `1` | Websuche & Webseiten lesen |
| `JARVIS_REFLECT_EVERY` | `6` | Nach so vielen Nachrichten lernt Jarvis im Hintergrund (kostet je eine Anfrage) |
| `JARVIS_HOST` / `JARVIS_PORT` | `127.0.0.1` / `8765` | `0.0.0.0` macht Jarvis im WLAN erreichbar |
| `JARVIS_TOKEN` | automatisch | Zugangscode (Pflicht, sobald Jarvis im Netz erreichbar ist) |

## Wo deine Daten liegen

Alles bleibt auf deinem Gerät in `~/jarvis-data/`:

- `jarvis.db` – Gedächtnis, Aufgaben, Gesprächsverläufe (SQLite)
- `brain/*.md` – deine Notizen als normale Markdown-Dateien

Zum Denken werden Nachrichten, dein Profil und Werkzeug-Ergebnisse an den gewählten KI-Anbieter
geschickt – mit Ollama bleibt alles in deinem eigenen Netz.

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
  llm.py      Anbindung der KI-Modelle: Claude und OpenAI-kompatible Anbieter
  config.py   Einstellungen und Anbieter-Liste
  brain.py    Second Brain: Fakten, Notizen, Episoden, Aufgaben, Gespräche
  tools.py    Werkzeuge: Gedächtnis, Dateien, Code, Web, Handy (Termux:API)
  server.py   Webserver (nur Python-Standardbibliothek) mit Streaming
  web/        Handy-Oberfläche (PWA)
tests/        Offline-Tests (ohne API-Schlüssel und ohne Internet lauffähig)
```

Tests ausführen: `python -m unittest discover -s tests`

## Ideen für später

- Proaktive Erinnerungen per Benachrichtigung (Termux:Job / Cron)
- Kalender, E-Mail, Messenger anbinden
- Wake-Word („Hey Jarvis“) und dauerhaftes Zuhören

---

## Extra: Personensuche (eigenständige Website)

Im Ordner [`personensuche/`](personensuche/) liegt ein separates Projekt: eine Personensuchmaschine als reine
HTML-Website, die über GitHub Pages gehostet werden kann. Details: [personensuche/README.md](personensuche/README.md).
