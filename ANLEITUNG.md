# JARVIS starten – Schritt-für-Schritt-Anleitung

Diese Anleitung erklärt, wie du Jarvis **auf dem Handy mit Termux** und **auf dem PC**
einrichtest, startest, aktualisierst und wieder beendest. Was Jarvis kann, steht im
[README](README.md).

**Inhalt**

1. [Was du brauchst](#1-was-du-brauchst)
2. [Android mit Termux](#2-android-mit-termux)
3. [PC: Linux, macOS, Windows](#3-pc-linux-macos-windows)
4. [Jarvis benutzen](#4-jarvis-benutzen)
5. [Aktualisieren, sichern, deinstallieren](#5-aktualisieren-sichern-deinstallieren)
6. [Probleme lösen](#6-probleme-lösen)

---

## 1. Was du brauchst

- Einen **API-Schlüssel** für ein KI-Modell. Kostenlos geht es zum Beispiel so:

  **Google Gemini (empfohlen):**
  1. <https://aistudio.google.com/apikey> öffnen und mit einem Google-Konto anmelden.
  2. **„API-Schlüssel erstellen“** (*Create API key*) tippen.
  3. Den Schlüssel kopieren. Eine Kreditkarte ist nicht nötig.

  Andere kostenlose Anbieter: [Groq](https://console.groq.com/keys),
  [OpenRouter](https://openrouter.ai/keys), [Mistral](https://console.mistral.ai/api-keys).
  Claude ([Anthropic](https://console.anthropic.com/)) ist kostenpflichtig (ab 5 € Guthaben),
  liefert aber die beste Qualität. Eine Übersicht steht im [README](README.md#welches-ki-modell).
- **Handy:** Android 7 oder neuer, ca. 500 MB freier Speicher (mit Claude ca. 1,5 GB), WLAN für die Installation.
- **PC:** Python 3.10 oder neuer und Git.

> **Den Schlüssel niemals weitergeben oder ins Repository hochladen.** Er steht nur in
> `~/jarvis-data/.env`, und diese Datei liegt außerhalb des Projektordners.

> **Kostenlose Kontingente haben Limits** (Anfragen pro Minute und pro Tag). Ist das Limit
> erreicht, meldet Jarvis das und du wartest kurz bzw. bis zum nächsten Tag. Manche Anbieter
> dürfen Eingaben aus dem kostenlosen Kontingent zur Verbesserung ihrer Modelle verwenden.

---

## 2. Android mit Termux

### 2.1 Apps installieren

Installiere aus **[F-Droid](https://f-droid.org)** (nicht aus dem Play Store, die Version dort ist veraltet):

| App | Wofür | Pflicht? |
|---|---|---|
| **Termux** | Linux-Terminal, in dem Jarvis läuft | ja |
| **Termux:API** | Jarvis darf Benachrichtigungen, Vorlesen, Taschenlampe, Akku, Standort, Kamera … nutzen | empfohlen |
| **Termux:Widget** | Jarvis per Symbol auf dem Startbildschirm starten | optional |

Alle drei müssen aus **derselben Quelle** (F-Droid) stammen, sonst arbeiten sie nicht zusammen.

### 2.2 Jarvis herunterladen

Öffne Termux und gib ein:

```bash
pkg update -y
pkg install -y git
git clone https://github.com/GermanClaude/Jarvis.git ~/Jarvis
```

**Ist das Repository privat?** Dann fragt Git nach Benutzername und Passwort. Als Passwort
funktioniert dein GitHub-Passwort **nicht**. Du brauchst einen *Personal Access Token*:
GitHub → *Settings* → *Developer settings* → *Personal access tokens* → *Fine-grained tokens* →
Zugriff auf `GermanClaude/Jarvis` mit *Contents: Read* erlauben → Token als Passwort einfügen.

### 2.3 Installationsskript ausführen

```bash
bash ~/Jarvis/install-termux.sh
```

Das Skript:

1. installiert Python, Git und `termux-api`,
2. fragt nach Zugriff auf den Handyspeicher → im Dialog **Erlauben** tippen,
3. fragt, welches **KI-Modell** du nutzen willst (Enter = Google Gemini, kostenlos),
4. fragt nach deinem **API-Schlüssel** und deinem **Namen** und speichert alles in `~/jarvis-data/.env`,
5. nur bei Claude: installiert zusätzlich Rust und das Paket `anthropic` (dauert **10–20 Minuten**,
   Bildschirm anlassen bzw. Termux im Vordergrund lassen),
6. legt den Befehl `jarvis` und eine Verknüpfung für Termux:Widget an.

**Anbieter später wechseln:** `nano ~/jarvis-data/.env` öffnen, `JARVIS_PROVIDER=` ändern und den
passenden Schlüssel eintragen (z. B. `GROQ_API_KEY=...`). Beim Wechsel zu Claude danach
`bash ~/Jarvis/install-termux.sh` erneut ausführen, damit das nötige Paket installiert wird.

### 2.4 Akku-Optimierung ausschalten

Sonst beendet Android Termux, sobald du die App wechselst:

*Einstellungen* → *Apps* → *Termux* → *Akku* → **Nicht eingeschränkt / Nicht optimieren**.

Jarvis setzt beim Start zusätzlich einen Wake-Lock (Benachrichtigung „Termux – wake lock held“).

### 2.5 Starten

```bash
jarvis
```

Nach ein paar Sekunden öffnet sich der Browser mit `http://127.0.0.1:8765`. Falls nicht, öffne
die Adresse selbst in Chrome.

**Als App auf den Startbildschirm:** In Chrome auf ⋮ → **„Zum Startbildschirm hinzufügen“**.
Wichtig: Das Symbol funktioniert nur, solange `jarvis` in Termux läuft.

**Per Widget starten (ohne Termux zu öffnen):** Lange auf den Startbildschirm drücken →
*Widgets* → *Termux:Widget* hinziehen → **Jarvis** antippen.

### 2.6 Beenden

In Termux **Strg + C** drücken (in der Termux-Tastaturleiste `CTRL`, dann `c`).
Wake-Lock lösen: `termux-wake-unlock` oder in der Termux-Benachrichtigung auf *Exit* tippen.

---

## 3. PC: Linux, macOS, Windows

### 3.1 Herunterladen und einrichten

```bash
git clone https://github.com/GermanClaude/Jarvis.git
cd Jarvis
python -m venv .venv
```

Virtuelle Umgebung aktivieren:

| System | Befehl |
|---|---|
| Linux / macOS | `source .venv/bin/activate` |
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |
| Windows (cmd) | `.venv\Scripts\activate.bat` |

Für die kostenlosen Anbieter sind keine Zusatzpakete nötig. Nur für Claude:

```bash
pip install -r requirements-claude.txt
```

### 3.2 Anbieter und API-Schlüssel eintragen

Am bequemsten dauerhaft über die Einstellungsdatei (darin `JARVIS_PROVIDER` und den passenden
Schlüssel eintragen, z. B. `JARVIS_PROVIDER=gemini` und `GEMINI_API_KEY=...`):

```bash
# Linux / macOS
mkdir -p ~/jarvis-data
cp .env.example ~/jarvis-data/.env
nano ~/jarvis-data/.env        # Anbieter und Schlüssel eintragen
```

```powershell
# Windows (PowerShell)
mkdir $HOME\jarvis-data -Force
copy .env.example $HOME\jarvis-data\.env
notepad $HOME\jarvis-data\.env
```

Alternativ nur für die aktuelle Sitzung:
`export GEMINI_API_KEY=...` (Linux/macOS) bzw. `$env:GEMINI_API_KEY="..."` (PowerShell).

### 3.3 Starten

```bash
python -m jarvis
```

Dann im Browser <http://127.0.0.1:8765> öffnen. Beenden mit **Strg + C**.

Auf dem PC gibt es keinen `jarvis`-Befehl – überall, wo in dieser Anleitung `jarvis …` steht,
nimmst du `python -m jarvis …` (im Projektordner, mit aktivierter virtueller Umgebung).
Handyfunktionen wie Taschenlampe oder Vorlesen gibt es nur in Termux.

### 3.4 Komplett kostenlos und privat: lokales Modell mit Ollama

Mit [Ollama](https://ollama.com) läuft das KI-Modell auf deinem eigenen PC – ohne Schlüssel,
ohne Limits, und deine Daten verlassen dein Netz nicht. Das Handy ist dafür zu schwach, der PC
sollte mindestens 8 GB Arbeitsspeicher haben (besser 16 GB).

1. Ollama installieren und ein Modell laden, das Werkzeuge unterstützt:
   ```bash
   ollama pull llama3.1
   ```
2. **Jarvis auf demselben PC:** in `~/jarvis-data/.env` `JARVIS_PROVIDER=ollama` setzen, fertig.
3. **Jarvis auf dem Handy, Modell auf dem PC:** Ollama im WLAN freigeben (Umgebungsvariable
   `OLLAMA_HOST=0.0.0.0` setzen und Ollama neu starten) und auf dem Handy eintragen:
   ```
   JARVIS_PROVIDER=ollama
   JARVIS_BASE_URL=http://<IP-des-PCs>:11434/v1
   ```

Kleine lokale Modelle sind spürbar weniger zuverlässig als die großen Online-Modelle,
vor allem bei längeren Aufgaben mit Dateien und Code.

---

## 4. Jarvis benutzen

| Befehl | Was passiert |
|---|---|
| `jarvis` | Web-Oberfläche starten (Standard) |
| `jarvis serve --port 9000` | Web-Oberfläche auf einem anderen Port |
| `jarvis serve --host 0.0.0.0` | Im WLAN erreichbar machen (siehe unten) |
| `jarvis chat` | Im Terminal chatten – `/neu` neues Gespräch, `/lernen` jetzt lernen, `/exit` beenden |
| `jarvis profile` | Zeigen, was Jarvis über dich weiß |
| `jarvis reflect` | Aus allen bisherigen Gesprächen lernen |
| `jarvis models` | Verfügbare Modelle deines Anbieters anzeigen (das eingestellte ist mit `*` markiert) |

**Erster Schritt:** Tippe im Chat auf *„Lass uns ein Kennenlern-Interview machen“*.

### Von einem anderen Gerät aus nutzen (z. B. Handy-Jarvis am Laptop)

```bash
jarvis serve --host 0.0.0.0
```

Termux zeigt dann einen **Zugangscode** an. Auf dem anderen Gerät im selben WLAN
`http://<IP-des-Handys>:8765` öffnen und den Code eingeben. Die IP findest du unter
*Einstellungen* → *WLAN* → *Netzwerkdetails* oder mit `ifconfig` in Termux.
Einen festen Code kannst du mit `JARVIS_TOKEN=...` in `~/jarvis-data/.env` setzen.

> Nur in vertrauenswürdigen Netzen verwenden – Jarvis kann Dateien bearbeiten und Befehle ausführen.

### Einstellungen ändern

```bash
nano ~/jarvis-data/.env
```

Danach Jarvis neu starten. Alle Einstellungen sind im [README](README.md#einstellungen) erklärt.
Beispiele: `JARVIS_MODEL=...` wählt ein anderes Modell, `JARVIS_REFLECT_EVERY=12` spart
Anfragen (Jarvis lernt seltener im Hintergrund), `JARVIS_ALLOW_SHELL=0` verbietet Shell-Befehle.

---

## 5. Aktualisieren, sichern, deinstallieren

**Aktualisieren:**

```bash
cd ~/Jarvis
git pull
bash install-termux.sh     # auf dem PC nur bei Claude: pip install -r requirements-claude.txt
```

Deine Daten in `~/jarvis-data/` bleiben dabei erhalten.

**Sichern:** Alles, was Jarvis über dich weiß, liegt in `~/jarvis-data/`. So kopierst du es
in den Download-Ordner des Handys:

```bash
tar czf ~/storage/downloads/jarvis-backup.tar.gz -C ~ jarvis-data
```

Wiederherstellen: `tar xzf ~/storage/downloads/jarvis-backup.tar.gz -C ~`

Die Notizen in `~/jarvis-data/brain/` sind normale Markdown-Dateien und lassen sich z. B. mit
Obsidian öffnen.

**Deinstallieren:**

```bash
rm -rf ~/Jarvis $PREFIX/bin/jarvis ~/.shortcuts/Jarvis
rm -rf ~/jarvis-data     # löscht auch dein Gedächtnis – vorher sichern!
```

---

## 6. Probleme lösen

| Problem | Lösung |
|---|---|
| `Kein API-Schlüssel für … gefunden` | `~/jarvis-data/.env` prüfen: `JARVIS_PROVIDER` und der passende Schlüssel (z. B. `GEMINI_API_KEY=...`) ohne Leerzeichen und ohne Anführungszeichen. |
| `Unbekannter Anbieter` | Bei `JARVIS_PROVIDER` einen dieser Werte eintragen: `gemini`, `groq`, `openrouter`, `mistral`, `ollama`, `anthropic`, `openai`. |
| `Kostenloses Limit erreicht oder zu viele Anfragen` | Kurz warten bzw. bis zum nächsten Tag. Oder einen zweiten Anbieter einrichten und `JARVIS_PROVIDER` wechseln. `JARVIS_REFLECT_EVERY=12` spart Anfragen. |
| `Modell '…' nicht gefunden` | Anbieter haben ihr Modell-Angebot geändert: `jarvis models` zeigt die verfügbaren Modelle, eines davon bei `JARVIS_MODEL` eintragen. |
| `API-Schlüssel ungültig` | Schlüssel neu kopieren (ohne Leerzeichen am Ende) oder beim Anbieter einen neuen erstellen. |
| Fehler beim Senden von Fotos | Nicht jedes Modell versteht Bilder. Gemini kann es; bei Groq, Mistral und OpenRouter ein Modell mit Bildunterstützung wählen. |
| Jarvis ruft Werkzeuge falsch auf oder antwortet seltsam | Kleine bzw. kostenlose Modelle sind weniger zuverlässig. Ein größeres Modell wählen (`jarvis models`) oder den Anbieter wechseln. |
| `jarvis: command not found` | Installationsskript nochmal ausführen: `bash ~/Jarvis/install-termux.sh`. |
| `Für Claude fehlt das Paket 'anthropic'` | `bash ~/Jarvis/install-termux.sh` ausführen (auf dem PC: `pip install -r requirements-claude.txt`). |
| Installation bricht beim Bauen von `pydantic-core` / `jiter` ab (nur Claude) | `pkg install -y rust binutils` und dann `pip install -r ~/Jarvis/requirements-claude.txt` erneut ausführen. Genug freien Speicher sicherstellen. |
| `pkg`-Fehler wie „repository is under maintenance“ | `termux-change-repo` ausführen und einen anderen Spiegelserver wählen. |
| Browser öffnet sich nicht | Chrome selbst öffnen: `http://127.0.0.1:8765`. |
| `Address already in use` | Jarvis läuft schon (anderes Termux-Fenster) – dort beenden, oder anderen Port nehmen: `jarvis serve --port 8766`. |
| Handybefehle (Taschenlampe, Vorlesen …) hängen oder tun nichts | App **Termux:API** aus F-Droid installieren und ihr in den Android-Einstellungen die nötigen Berechtigungen geben. |
| Jarvis ist nach einer Weile nicht mehr erreichbar | Akku-Optimierung für Termux ausschalten (Abschnitt 2.4). |
| Kein Zugriff auf Fotos/Downloads | `termux-setup-storage` ausführen und erlauben. |
| Websuche findet nichts | Die kostenlose Suche (DuckDuckGo) blockt manchmal kurzzeitig. Später nochmal versuchen oder Jarvis bitten, eine bestimmte Seite direkt zu lesen. |
| `Keine Verbindung zu http://…:11434` (Ollama) | Läuft Ollama auf dem PC? Ist `OLLAMA_HOST=0.0.0.0` gesetzt und stimmt die IP in `JARVIS_BASE_URL`? |

**Funktioniert die Installation?** Das kannst du ohne API-Schlüssel und ohne Internet prüfen:

```bash
cd ~/Jarvis && python -m unittest discover -s tests
```

Am Ende sollte `OK` stehen (ohne Claude-Paket mit dem Zusatz `skipped=3` – das ist in Ordnung).
