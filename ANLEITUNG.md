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

- Einen **Anthropic API-Schlüssel**: <https://console.anthropic.com/> → *API Keys* → *Create Key*.
  Der Schlüssel beginnt mit `sk-ant-`. Die Nutzung kostet Geld pro Anfrage, lade also etwas
  Guthaben auf (*Billing*).
- **Handy:** Android 7 oder neuer, ca. 1,5 GB freier Speicher, WLAN für die Installation.
- **PC:** Python 3.10 oder neuer und Git.

> **Den Schlüssel niemals weitergeben oder ins Repository hochladen.** Er steht nur in
> `~/jarvis-data/.env`, und diese Datei liegt außerhalb des Projektordners.

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

1. installiert Python, Git, Rust und `termux-api`,
2. installiert die Python-Pakete (der erste Durchlauf dauert **10–20 Minuten**, weil einige
   Pakete für Android gebaut werden – Bildschirm anlassen bzw. Termux im Vordergrund lassen),
3. fragt nach Zugriff auf den Handyspeicher → im Dialog **Erlauben** tippen,
4. fragt nach deinem **API-Schlüssel** und deinem **Namen** und speichert beides in `~/jarvis-data/.env`,
5. legt den Befehl `jarvis` und eine Verknüpfung für Termux:Widget an.

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

Dann:

```bash
pip install -r requirements.txt
```

### 3.2 API-Schlüssel eintragen

Am bequemsten dauerhaft über die Einstellungsdatei:

```bash
# Linux / macOS
mkdir -p ~/jarvis-data
cp .env.example ~/jarvis-data/.env
nano ~/jarvis-data/.env        # ANTHROPIC_API_KEY=sk-ant-... eintragen
```

```powershell
# Windows (PowerShell)
mkdir $HOME\jarvis-data -Force
copy .env.example $HOME\jarvis-data\.env
notepad $HOME\jarvis-data\.env
```

Alternativ nur für die aktuelle Sitzung:
`export ANTHROPIC_API_KEY=sk-ant-...` (Linux/macOS) bzw.
`$env:ANTHROPIC_API_KEY="sk-ant-..."` (PowerShell).

### 3.3 Starten

```bash
python -m jarvis
```

Dann im Browser <http://127.0.0.1:8765> öffnen. Beenden mit **Strg + C**.

Auf dem PC gibt es keinen `jarvis`-Befehl – überall, wo in dieser Anleitung `jarvis …` steht,
nimmst du `python -m jarvis …` (im Projektordner, mit aktivierter virtueller Umgebung).
Handyfunktionen wie Taschenlampe oder Vorlesen gibt es nur in Termux.

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
Beispiele: `JARVIS_EFFORT=low` macht Jarvis schneller und günstiger, `JARVIS_ALLOW_SHELL=0`
verbietet Shell-Befehle.

---

## 5. Aktualisieren, sichern, deinstallieren

**Aktualisieren:**

```bash
cd ~/Jarvis
git pull
pip install -r requirements.txt
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
| `Kein API-Schlüssel gefunden` | `~/jarvis-data/.env` prüfen: Zeile `ANTHROPIC_API_KEY=sk-ant-...` ohne Leerzeichen und ohne Anführungszeichen. |
| `jarvis: command not found` | Installationsskript nochmal ausführen: `bash ~/Jarvis/install-termux.sh`. |
| Installation bricht beim Bauen von `pydantic-core` / `jiter` ab | `pkg install -y rust binutils` und dann `pip install -r ~/Jarvis/requirements.txt` erneut ausführen. Genug freien Speicher sicherstellen. |
| `pkg`-Fehler wie „repository is under maintenance“ | `termux-change-repo` ausführen und einen anderen Spiegelserver wählen. |
| Browser öffnet sich nicht | Chrome selbst öffnen: `http://127.0.0.1:8765`. |
| `Address already in use` | Jarvis läuft schon (anderes Termux-Fenster) – dort beenden, oder anderen Port nehmen: `jarvis serve --port 8766`. |
| Handybefehle (Taschenlampe, Vorlesen …) hängen oder tun nichts | App **Termux:API** aus F-Droid installieren und ihr in den Android-Einstellungen die nötigen Berechtigungen geben. |
| Jarvis ist nach einer Weile nicht mehr erreichbar | Akku-Optimierung für Termux ausschalten (Abschnitt 2.4). |
| Kein Zugriff auf Fotos/Downloads | `termux-setup-storage` ausführen und erlauben. |
| Fehler `401` / `authentication_error` | API-Schlüssel ist falsch oder gelöscht – neuen Schlüssel in `.env` eintragen. |
| Fehler `credit balance is too low` | Guthaben in der Anthropic Console aufladen. |
| Fehler `not_found_error` zum Modell | In `~/jarvis-data/.env` bei `JARVIS_MODEL` ein aktuelles Modell eintragen (Liste in der Anthropic-Dokumentation). |

**Funktioniert die Installation?** Das kannst du ohne API-Schlüssel prüfen:

```bash
cd ~/Jarvis && python -m unittest discover -s tests
```

Am Ende sollte `OK` stehen.
