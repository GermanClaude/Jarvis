"""Werkzeuge, die Jarvis selbstständig benutzen kann.

Jedes Tool hat ein JSON-Schema (für das Sprachmodell) und eine Python-Funktion (die es ausführt).
Handy-Funktionen laufen über Termux:API und melden sich sauber ab, wenn sie fehlen.
PC-Steuerung (Maus, Tastatur, Bildschirm) läuft über pyautogui und ist nur mit
JARVIS_PC_CONTROL=1 aktiv.
"""

from __future__ import annotations

import base64
import fnmatch
import io
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from . import screens
from .brain import FACT_CATEGORIES, Brain
from .config import Settings

MAX_RESULT_CHARS = 40_000
MAX_READ_BYTES = 2_000_000

# Befehle, die Jarvis niemals ausführt – egal, wer fragt (ganzes System/Home löschen usw.).
BLOCKED_SHELL_PATTERNS = [
    re.compile(r"\brm\s+(-\w+\s+)*(/|/\*|~/?|\$HOME/?|/storage/emulated/0/?|/sdcard/?)(\s|$|;|&|\|)"),
    re.compile(r"\bmkfs"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r"\bdd\b.*\bof=/dev/"),
    re.compile(r"\bchmod\s+-R\s+\S+\s+/(\s|$)"),
]


class ToolError(Exception):
    """Fehler, der als verständliche Meldung an das Modell zurückgeht."""


@dataclass
class ToolOutput:
    """Ergebnis mit Bildern (z. B. Bildschirmfoto). ``images``: Liste von (Medientyp, Base64-Daten)."""

    text: str
    images: list[tuple[str, str]]


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: Callable[..., str]

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
            # Große Eingaben (Dateiinhalte, Code) werden gestreamt, sobald sie entstehen.
            "eager_input_streaming": True,
        }


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


_TYPES = {
    "string": str, "integer": int, "boolean": bool, "array": list, "object": dict, "number": (int, float),
}


def validate(schema: dict, args: object) -> str | None:
    """Prüft Tool-Eingaben gegen das Schema. Gibt eine Fehlermeldung oder None zurück."""
    if not isinstance(args, dict):
        return "Eingabe ist kein Objekt."
    for key in schema.get("required", []):
        if key not in args:
            return f"Pflichtfeld '{key}' fehlt."
    for key, value in args.items():
        spec = schema["properties"].get(key)
        if spec is None:
            return f"Unbekanntes Feld '{key}'."
        expected = _TYPES[spec["type"]]
        if isinstance(value, bool) and spec["type"] in {"integer", "number"}:
            return f"Feld '{key}' muss {spec['type']} sein."
        if not isinstance(value, expected):
            return f"Feld '{key}' muss {spec['type']} sein."
        if "enum" in spec and value not in spec["enum"]:
            return f"Feld '{key}' muss einer von {spec['enum']} sein."
    return None


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} Zeichen abgeschnitten]"


def _run(cmd: list[str] | str, timeout: int = 60, cwd: Path | None = None, shell: bool = False,
         stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, shell=shell, cwd=str(cwd) if cwd else None, input=stdin,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Zeitüberschreitung nach {timeout}s.")
    except FileNotFoundError as exc:
        raise ToolError(f"Programm nicht gefunden: {exc.filename}")
    out = proc.stdout
    if proc.stderr:
        out += ("\n" if out else "") + "[stderr]\n" + proc.stderr
    return f"[exit {proc.returncode}]\n{out}".rstrip()


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Mobile Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _http_get(url: str, data: bytes | None = None, timeout: int = 20) -> tuple[str, str]:
    """Lädt eine Seite. Rückgabe: (Text, Content-Type)."""
    request = urllib.request.Request(url, data=data, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_READ_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, "replace"), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise ToolError(f"HTTP {exc.code} beim Laden von {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"Konnte {url} nicht laden: {exc}")


class _TextExtractor(HTMLParser):
    """Macht aus HTML lesbaren Text (ohne Skripte, Styles, Navigation)."""

    SKIP = {"script", "style", "noscript", "svg", "template", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "header", "h1", "h2", "h3", "h4", "h5", "h6",
             "pre", "blockquote", "table", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(page: str) -> tuple[str, str]:
    """Rückgabe: (Titel, Text)."""
    parser = _TextExtractor()
    parser.feed(page)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return parser.title.strip(), text


def parse_ddg_results(page: str, limit: int) -> list[dict]:
    """Liest Treffer aus der HTML-Ergebnisseite von DuckDuckGo."""
    def clean(fragment: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()

    links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|td)>', page, re.S)
    results = []
    for i, (href, title) in enumerate(links):
        href = html.unescape(href)
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/y.js" in href:  # Werbung
            continue
        results.append({"title": clean(title), "url": href, "snippet": clean(snippets[i]) if i < len(snippets) else ""})
        if len(results) >= limit:
            break
    return results


def _launch_program(name: str) -> str:
    """Startet ein Programm auf dem PC (Windows: wie Win+R, macOS: open -a, Linux: direkt)."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", name], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", name])
        else:
            subprocess.Popen([name], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        raise ToolError(f"Konnte '{name}' nicht starten: {exc}. Alternative: keyboard hotkey 'win', "
                        "Programmnamen tippen, Enter.")
    return f"Gestartet: {name} (mit screenshot prüfen, ob es geöffnet ist)"


def _gui():
    """pyautogui laden (nur auf dem PC, mit requirements-pc.txt)."""
    screens.make_dpi_aware()
    try:
        import pyautogui
    except ImportError:
        raise ToolError("PC-Steuerung braucht Zusatzpakete: pip install -r requirements-pc.txt")
    except Exception as exc:  # noqa: BLE001 – z. B. kein Bildschirm (Server, Termux)
        raise ToolError(f"PC-Steuerung nicht verfügbar: {exc}")
    pyautogui.FAILSAFE = True  # Maus in eine Bildschirmecke ziehen bricht alles ab
    pyautogui.PAUSE = 0.05
    return pyautogui


def _is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or shutil.which("termux-info") is not None


def _termux(cmd: str, *args: str, stdin: str | None = None, timeout: int = 30) -> str:
    if not shutil.which(cmd):
        raise ToolError(
            f"'{cmd}' ist nicht verfügbar. Installiere in Termux: 'pkg install termux-api' "
            "und die App 'Termux:API' (F-Droid)."
        )
    return _run([cmd, *args], timeout=timeout, stdin=stdin)


class Toolbox:
    def __init__(self, brain: Brain, settings: Settings) -> None:
        self.brain = brain
        self.settings = settings
        self.tools: dict[str, Tool] = {}
        # Bildschirmfoto-Koordinaten → echte Bildschirm-Koordinaten (Verkleinerung und Monitor-Position)
        self._screen_scale = 1.0
        self._screen_offset = (0, 0)
        self._register_all()

    # ------------------------------------------------------------ Hilfen
    def resolve(self, path: str) -> Path:
        """Relativ zum Arbeitsordner auflösen und auf erlaubte Ordner begrenzen."""
        raw = Path(os.path.expanduser(path))
        target = (raw if raw.is_absolute() else self.settings.workspace / raw).resolve()
        if not any(target == root or target.is_relative_to(root) for root in self.settings.allowed_roots):
            allowed = ", ".join(str(r) for r in self.settings.allowed_roots)
            raise ToolError(f"Zugriff verweigert: {target} liegt außerhalb von {allowed}.")
        return target

    def add(self, name: str, description: str, schema: dict):
        def deco(func: Callable[..., str]) -> Callable[..., str]:
            self.tools[name] = Tool(name, description, schema, func)
            return func
        return deco

    def definitions(self) -> list[dict]:
        return [t.definition() for t in self.tools.values()]

    def execute(self, name: str, args: object) -> tuple[str, bool]:
        """Führt ein Tool aus. Rückgabe: (Ergebnistext, ist_fehler)."""
        text, is_error, _ = self.execute_full(name, args)
        return text, is_error

    def execute_full(self, name: str, args: object) -> tuple[str, bool, list[tuple[str, str]]]:
        """Wie ``execute``, liefert zusätzlich Bilder: (Ergebnistext, ist_fehler, Bilder)."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Unbekanntes Tool: {name}", True, []
        problem = validate(tool.schema, args)
        if problem:
            return json.dumps({"INVALID_INPUT": problem, "received": args}, ensure_ascii=False), True, []
        try:
            result = tool.func(**args)
        except ToolError as exc:
            return str(exc), True, []
        except Exception as exc:  # noqa: BLE001 – Fehler gehen als Text an das Modell zurück
            return f"{type(exc).__name__}: {exc}", True, []
        if isinstance(result, ToolOutput):
            return _truncate(result.text), False, result.images
        return _truncate(result), False, []

    # ------------------------------------------------------------ Registrierung
    def _register_all(self) -> None:
        self._register_memory()
        self._register_files()
        self._register_code()
        self._register_phone()
        if self.settings.web_tools:
            self._register_web()
        if self.settings.pc_control:
            self._register_pc()

    def _register_memory(self) -> None:
        b = self.brain

        @self.add("remember", (
            "Speichere einen dauerhaften Fakt über den Nutzer im Langzeitgedächtnis: Vorlieben, Ziele, "
            "Projekte, Menschen, Gewohnheiten, wie er denkt und kommuniziert. Nutze das proaktiv, "
            "sobald du etwas Neues und Bleibendes über ihn erfährst. Ein Fakt pro Aufruf, kurz und konkret."
        ), _obj({
            "content": {"type": "string", "description": "Der Fakt, z. B. 'Trinkt morgens keinen Kaffee, nur Tee'."},
            "category": {"type": "string", "enum": FACT_CATEGORIES},
        }, ["content", "category"]))
        def remember(content: str, category: str) -> str:
            return f"Gespeichert als Fakt #{b.add_fact(content, category)}."

        @self.add("update_memory", "Korrigiere oder aktualisiere einen gespeicherten Fakt (per ID).", _obj({
            "fact_id": {"type": "integer"},
            "content": {"type": "string"},
        }, ["fact_id", "content"]))
        def update_memory(fact_id: int, content: str) -> str:
            if not b.update_fact(fact_id, content):
                raise ToolError(f"Fakt #{fact_id} existiert nicht.")
            return f"Fakt #{fact_id} aktualisiert."

        @self.add("forget", "Lösche einen gespeicherten Fakt, der falsch oder veraltet ist.", _obj({
            "fact_id": {"type": "integer"},
        }, ["fact_id"]))
        def forget(fact_id: int) -> str:
            if not b.delete_fact(fact_id):
                raise ToolError(f"Fakt #{fact_id} existiert nicht.")
            return f"Fakt #{fact_id} vergessen."

        @self.add("search_brain", (
            "Durchsuche das Second Brain (Fakten, Notizen, Zusammenfassungen früherer Gespräche) "
            "nach Stichworten. Nutze das, bevor du sagst, dass du etwas nicht weißt."
        ), _obj({"query": {"type": "string"}}, ["query"]))
        def search_brain(query: str) -> str:
            hits = b.search(query)
            if not hits:
                return "Nichts gefunden."
            return "\n".join(f"[{h.kind} {h.ref}] {h.text}" for h in hits)

        @self.add("save_note", (
            "Lege eine Markdown-Notiz im Second Brain an oder ergänze sie. Für Ideen, Wissen, "
            "Protokolle, Pläne, Tagebuch. Verlinke verwandte Notizen mit [[Titel]]."
        ), _obj({
            "title": {"type": "string"},
            "content": {"type": "string", "description": "Markdown-Inhalt."},
            "mode": {"type": "string", "enum": ["overwrite", "append", "create"],
                     "description": "append hängt an eine bestehende Notiz an."},
        }, ["title", "content"]))
        def save_note(title: str, content: str, mode: str = "overwrite") -> str:
            path = b.save_note(title, content, mode)
            return f"Notiz gespeichert: {path.name}"

        @self.add("read_note", "Lies eine Notiz aus dem Second Brain.", _obj({
            "title": {"type": "string"},
        }, ["title"]))
        def read_note(title: str) -> str:
            try:
                return b.read_note(title)
            except FileNotFoundError as exc:
                raise ToolError(str(exc))

        @self.add("list_notes", "Liste alle Notizen im Second Brain auf.", _obj({}, []))
        def list_notes() -> str:
            notes = b.list_notes()
            return "\n".join(n["title"] for n in notes) or "Noch keine Notizen."

        @self.add("add_task", "Lege eine Aufgabe oder Erinnerung an.", _obj({
            "title": {"type": "string"},
            "due": {"type": "string", "description": "Fälligkeit im Format YYYY-MM-DD oder YYYY-MM-DD HH:MM."},
            "notes": {"type": "string"},
        }, ["title"]))
        def add_task(title: str, due: str | None = None, notes: str | None = None) -> str:
            return f"Aufgabe #{b.add_task(title, due, notes)} angelegt."

        @self.add("list_tasks", "Zeige offene (oder alle) Aufgaben.", _obj({
            "include_done": {"type": "boolean"},
        }, []))
        def list_tasks(include_done: bool = False) -> str:
            rows = b.tasks(include_done)
            if not rows:
                return "Keine Aufgaben."
            return "\n".join(
                f"#{t['id']} [{'x' if t['done'] else ' '}] {t['title']}"
                + (f" (fällig {t['due']})" if t["due"] else "")
                + (f" – {t['notes']}" if t["notes"] else "")
                for t in rows
            )

        @self.add("complete_task", "Markiere eine Aufgabe als erledigt.", _obj({
            "task_id": {"type": "integer"},
        }, ["task_id"]))
        def complete_task(task_id: int) -> str:
            if not b.complete_task(task_id):
                raise ToolError(f"Aufgabe #{task_id} existiert nicht.")
            return f"Aufgabe #{task_id} erledigt."

    def _register_files(self) -> None:
        @self.add("list_dir", "Liste den Inhalt eines Ordners auf (Pfad relativ zum Arbeitsordner oder absolut).", _obj({
            "path": {"type": "string"},
        }, []))
        def list_dir(path: str = ".") -> str:
            target = self.resolve(path)
            if not target.is_dir():
                raise ToolError(f"Kein Ordner: {target}")
            lines = [f"{target}:"]
            for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
                if entry.is_dir():
                    lines.append(f"  {entry.name}/")
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"  {entry.name}  ({size} B)")
            return "\n".join(lines)

        @self.add("read_file", "Lies eine Textdatei. Mit offset/limit (Zeilen) für große Dateien.", _obj({
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "Erste Zeile (ab 1)."},
            "limit": {"type": "integer", "description": "Anzahl Zeilen."},
        }, ["path"]))
        def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
            target = self.resolve(path)
            if not target.is_file():
                raise ToolError(f"Datei nicht gefunden: {target}")
            if target.stat().st_size > MAX_READ_BYTES and limit > 2000:
                limit = 2000
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(offset, 1) - 1
            chunk = lines[start:start + limit]
            numbered = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start=start + 1))
            more = f"\n… ({len(lines)} Zeilen insgesamt)" if start + limit < len(lines) else ""
            return numbered + more

        @self.add("write_file", "Erstelle oder überschreibe eine Datei (Ordner werden angelegt).", _obj({
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, ["path", "content"]))
        def write_file(path: str, content: str) -> str:
            target = self.resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"{len(content)} Zeichen geschrieben nach {target}"

        @self.add("edit_file", (
            "Ersetze einen exakten Textabschnitt in einer Datei. old_text muss genau einmal vorkommen "
            "(außer replace_all ist true)."
        ), _obj({
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"},
        }, ["path", "old_text", "new_text"]))
        def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
            target = self.resolve(path)
            if not target.is_file():
                raise ToolError(f"Datei nicht gefunden: {target}")
            text = target.read_text(encoding="utf-8")
            count = text.count(old_text)
            if count == 0:
                raise ToolError("old_text kommt in der Datei nicht vor.")
            if count > 1 and not replace_all:
                raise ToolError(f"old_text kommt {count}× vor – mehr Kontext angeben oder replace_all nutzen.")
            target.write_text(text.replace(old_text, new_text), encoding="utf-8")
            return f"{count if replace_all else 1} Stelle(n) ersetzt in {target}"

        @self.add("move_path", "Verschiebe oder benenne eine Datei/einen Ordner um.", _obj({
            "source": {"type": "string"},
            "destination": {"type": "string"},
        }, ["source", "destination"]))
        def move_path(source: str, destination: str) -> str:
            src, dst = self.resolve(source), self.resolve(destination)
            if not src.exists():
                raise ToolError(f"Nicht gefunden: {src}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return f"{src} → {dst}"

        @self.add("delete_path", (
            "Lösche eine Datei oder einen leeren Ordner. Nur nach ausdrücklicher Zustimmung des Nutzers verwenden."
        ), _obj({"path": {"type": "string"}}, ["path"]))
        def delete_path(path: str) -> str:
            target = self.resolve(path)
            if target in self.settings.allowed_roots:
                raise ToolError("Ein Wurzelordner kann nicht gelöscht werden.")
            if target.is_dir():
                target.rmdir()
            elif target.exists():
                target.unlink()
            else:
                raise ToolError(f"Nicht gefunden: {target}")
            return f"Gelöscht: {target}"

        @self.add("find_files", (
            "Suche Dateien per Namensmuster (z. B. '*.py') und optional nach Text im Inhalt."
        ), _obj({
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "contains": {"type": "string", "description": "Nur Dateien, die diesen Text enthalten."},
        }, ["pattern"]))
        def find_files(pattern: str, path: str = ".", contains: str | None = None) -> str:
            base = self.resolve(path)
            results = []
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__"}]
                for name in files:
                    if not fnmatch.fnmatch(name, pattern):
                        continue
                    p = Path(root) / name
                    if contains:
                        try:
                            if p.stat().st_size > MAX_READ_BYTES or contains not in p.read_text(
                                encoding="utf-8", errors="ignore"
                            ):
                                continue
                        except OSError:
                            continue
                    results.append(str(p))
                    if len(results) >= 200:
                        return "\n".join(results) + "\n… (mehr Treffer abgeschnitten)"
            return "\n".join(results) or "Keine Treffer."

    def _register_code(self) -> None:
        @self.add("run_python", (
            "Führe Python-Code aus (eigener Prozess) und gib stdout/stderr zurück. Für Berechnungen, "
            "Datenverarbeitung, Tests und Skripte."
        ), _obj({
            "code": {"type": "string"},
            "timeout": {"type": "integer", "description": "Sekunden, Standard 60, max. 600."},
        }, ["code"]))
        def run_python(code: str, timeout: int = 60) -> str:
            return _run([sys.executable, "-"], timeout=min(timeout, 600), cwd=self.settings.workspace, stdin=code)

        if not self.settings.allow_shell:
            return

        @self.add("run_shell", (
            "Führe einen Shell-Befehl auf dem Gerät aus (in Termux: pkg, git, python, node, ls …). "
            "Frage vorher nach, wenn der Befehl etwas löscht oder nicht rückgängig zu machen ist."
        ), _obj({
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Arbeitsordner, Standard: Workspace."},
            "timeout": {"type": "integer", "description": "Sekunden, Standard 120, max. 1800."},
        }, ["command"]))
        def run_shell(command: str, cwd: str | None = None, timeout: int = 120) -> str:
            if any(p.search(command) for p in BLOCKED_SHELL_PATTERNS):
                raise ToolError("Dieser Befehl ist aus Sicherheitsgründen gesperrt.")
            workdir = self.resolve(cwd) if cwd else self.settings.workspace
            return _run(command, shell=True, timeout=min(timeout, 1800), cwd=workdir)

    def _register_web(self) -> None:
        # Kostenlose Websuche ohne Schlüssel (DuckDuckGo). Mit Claude übernimmt die serverseitige Suche.
        @self.add("web_search", (
            "Suche im Internet (DuckDuckGo). Liefert Titel, Link und Kurztext der Treffer. "
            "Für Details danach die passende Seite mit web_fetch lesen."
        ), _obj({
            "query": {"type": "string", "description": "Suchbegriffe"},
            "max_results": {"type": "integer", "description": "Anzahl Treffer (Standard 6, max. 15)"},
        }, ["query"]))
        def web_search(query: str, max_results: int = 6) -> str:
            data = urllib.parse.urlencode({"q": query, "kl": "de-de"}).encode()
            page, _ = _http_get("https://html.duckduckgo.com/html/", data=data)
            results = parse_ddg_results(page, max(1, min(max_results, 15)))
            if not results:
                raise ToolError("Keine Treffer (oder die Suche ist gerade nicht erreichbar). "
                                "Andere Suchbegriffe versuchen oder eine bekannte Seite direkt mit web_fetch lesen.")
            return "\n\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
                               for i, r in enumerate(results, 1))

        @self.add("web_fetch", (
            "Lade eine Webseite (http/https) und gib ihren lesbaren Text zurück."
        ), _obj({
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "description": "Maximale Textlänge (Standard 20000)"},
        }, ["url"]))
        def web_fetch(url: str, max_chars: int = 20_000) -> str:
            if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
                raise ToolError("Nur http- und https-Adressen sind erlaubt.")
            page, content_type = _http_get(url)
            if "html" in content_type or page.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                title, text = html_to_text(page)
                page = (f"# {title}\n\n" if title else "") + text
            return _truncate(page, max(500, min(max_chars, MAX_RESULT_CHARS)))

    def _register_pc(self) -> None:
        careful = (" Vorsicht: Vor dem Absenden von Nachrichten/E-Mails, Käufen, Zahlungen, Löschen oder "
                   "Systemeinstellungen den Nutzer ausdrücklich fragen. Niemals Passwörter eintippen.")

        def point(x: int, y: int) -> tuple[int, int]:
            left, top = self._screen_offset
            return left + round(x * self._screen_scale), top + round(y * self._screen_scale)

        @self.add("screenshot", (
            "Mache ein Bildschirmfoto vom PC und sieh es dir an. Nutze das, bevor du mit mouse/keyboard "
            "handelst, und danach zur Kontrolle. Koordinaten für mouse beziehen sich auf das letzte Bild. "
            "Bei mehreren Bildschirmen: 'monitor' wählt einen (1 = Hauptbildschirm); ohne Angabe der, "
            "auf dem das aktive Fenster liegt."
        ), _obj({
            "monitor": {"type": "integer", "description": "Bildschirm-Nummer (1, 2, …)"},
        }, []))
        def screenshot(monitor: int = 0) -> ToolOutput:
            gui = _gui()
            monitors = screens.monitors(tuple(gui.size()))
            if monitor:
                target = next((m for m in monitors if m.number == monitor), None)
                if target is None:
                    raise ToolError(f"Bildschirm {monitor} gibt es nicht (vorhanden: 1–{len(monitors)}).")
            else:
                active = next((w for w in screens.windows() if w.foreground), None)
                target = max(monitors, key=lambda m: (active.rect.overlap(m.rect) if active else 0, m.primary))
            r = target.rect
            try:
                from PIL import ImageGrab

                if sys.platform == "win32":
                    image = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
                else:
                    image = ImageGrab.grab()
            except (ImportError, OSError):
                image = gui.screenshot()
            width, height = image.size
            self._screen_offset = (r.left, r.top)
            self._screen_scale = max(1.0, width / 1280)
            if self._screen_scale > 1.0:
                image = image.resize((round(width / self._screen_scale), round(height / self._screen_scale)))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
            mx, my = gui.position()
            text = (f"Bildschirm {target.number} von {len(monitors)}, Bild {image.size[0]}x{image.size[1]} "
                    f"(echt {width}x{height}).")
            if r.left <= mx < r.right and r.top <= my < r.bottom:
                text += (f" Maus bei ({round((mx - r.left) / self._screen_scale)}, "
                         f"{round((my - r.top) / self._screen_scale)}).")
            return ToolOutput(text, [("image/jpeg", base64.b64encode(buffer.getvalue()).decode())])

        @self.add("mouse", (
            "Steuere die Maus am PC. Koordinaten (x, y) aus dem letzten screenshot. "
            "Aktionen: move, click, double_click, right_click, drag (von x,y nach to_x,to_y), "
            "scroll (amount: positiv = hoch, negativ = runter)." + careful
        ), _obj({
            "action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "drag", "scroll"]},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "to_x": {"type": "integer"},
            "to_y": {"type": "integer"},
            "amount": {"type": "integer", "description": "Scroll-Schritte (Standard 5)"},
        }, ["action"]))
        def mouse(action: str, x: int | None = None, y: int | None = None, to_x: int | None = None,
                  to_y: int | None = None, amount: int = 5) -> str:
            gui = _gui()
            needs_point = action in {"move", "drag"}
            if needs_point and (x is None or y is None):
                raise ToolError(f"Für '{action}' werden x und y gebraucht.")
            pos = point(x, y) if x is not None and y is not None else None
            if action == "move":
                gui.moveTo(*pos, duration=0.2)
            elif action in {"click", "double_click", "right_click"}:
                kwargs = {"x": pos[0], "y": pos[1]} if pos else {}
                {"click": gui.click, "double_click": gui.doubleClick, "right_click": gui.rightClick}[action](**kwargs)
            elif action == "drag":
                if to_x is None or to_y is None:
                    raise ToolError("Für 'drag' werden to_x und to_y gebraucht.")
                gui.moveTo(*pos, duration=0.2)
                gui.dragTo(*point(to_x, to_y), duration=0.5, button="left")
            elif action == "scroll":
                if pos:
                    gui.moveTo(*pos, duration=0.1)
                gui.scroll(amount * (120 if sys.platform == "win32" else 1))
            return f"Maus: {action} ausgeführt."

        @self.add("keyboard", (
            "Tastatur am PC: 'type' tippt Text (auch Umlaute) ins aktive Fenster, 'press' drückt eine Taste "
            "(z. B. enter, tab, esc, backspace, win, f5, volumeup, playpause), 'hotkey' drückt eine "
            "Kombination wie 'ctrl+c', 'alt+tab', 'win+d', 'ctrl+shift+esc'." + careful
        ), _obj({
            "action": {"type": "string", "enum": ["type", "press", "hotkey"]},
            "text": {"type": "string", "description": "Text für type"},
            "keys": {"type": "string", "description": "Taste bzw. Kombination für press/hotkey"},
            "times": {"type": "integer", "description": "Wie oft die Taste gedrückt wird (Standard 1)"},
        }, ["action"]))
        def keyboard(action: str, text: str = "", keys: str = "", times: int = 1) -> str:
            gui = _gui()
            if action == "type":
                if text.isascii():
                    gui.write(text, interval=0.01)
                else:  # pyautogui tippt keine Umlaute – über die Zwischenablage einfügen
                    import pyperclip
                    pyperclip.copy(text)
                    gui.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
                return f"Getippt: {len(text)} Zeichen."
            names = [k.strip().lower() for k in keys.replace(" ", "").split("+") if k.strip()]
            if not names:
                raise ToolError("'keys' fehlt.")
            unknown = [k for k in names if k not in gui.KEYBOARD_KEYS]
            if unknown:
                raise ToolError(f"Unbekannte Taste(n): {unknown}")
            for _ in range(max(1, min(times, 50))):
                if action == "hotkey":
                    gui.hotkey(*names)
                else:
                    gui.press(names[0])
            return f"Gedrückt: {keys}"

        @self.add("windows", (
            "Fenster am PC: 'list' zeigt offene Fenster, 'activate' holt ein Fenster nach vorne, "
            "'minimize'/'maximize' – jeweils per Teil des Fenstertitels (nur Windows)."
        ), _obj({
            "action": {"type": "string", "enum": ["list", "activate", "minimize", "maximize"]},
            "title": {"type": "string"},
        }, ["action"]))
        def windows(action: str, title: str = "") -> str:
            _gui()
            try:
                import pygetwindow
                all_windows = [w for w in pygetwindow.getAllWindows() if w.title.strip()]
            except (ImportError, NotImplementedError, AttributeError):
                raise ToolError("Fensterverwaltung gibt es nur unter Windows. Alternative: keyboard hotkey 'alt+tab'.")
            if action == "list":
                return "\n".join(sorted({w.title for w in all_windows})) or "Keine Fenster."
            matches = [w for w in all_windows if title.lower() in w.title.lower()] if title else []
            if not matches:
                raise ToolError(f"Kein Fenster mit '{title}' im Titel. Mit action 'list' nachsehen.")
            window = matches[0]
            if action == "activate":
                if window.isMinimized:
                    window.restore()
                window.activate()
            elif action == "minimize":
                window.minimize()
            else:
                window.maximize()
            return f"{action}: {window.title}"

        @self.add("pc", (
            "PC-Funktionen: volume_up, volume_down, mute, play_pause, next_track, previous_track, lock "
            "(PC sperren), clipboard_get, clipboard_set (text), info (System, Bildschirmgröße)."
        ), _obj({
            "action": {"type": "string", "enum": ["volume_up", "volume_down", "mute", "play_pause", "next_track",
                                                  "previous_track", "lock", "clipboard_get", "clipboard_set", "info"]},
            "text": {"type": "string"},
            "times": {"type": "integer", "description": "Für volume_up/down: Anzahl Stufen (Standard 5)"},
        }, ["action"]))
        def pc(action: str, text: str = "", times: int = 5) -> str:
            import platform

            gui = _gui()
            media = {"volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute",
                     "play_pause": "playpause", "next_track": "nexttrack", "previous_track": "prevtrack"}
            if action in media:
                presses = max(1, min(times, 50)) if action.startswith("volume_") and action != "mute" else 1
                gui.press(media[action], presses=presses)
                return f"{action} ausgeführt."
            if action == "lock":
                if sys.platform == "win32":
                    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
                elif sys.platform == "darwin":
                    subprocess.Popen(["pmset", "displaysleepnow"])
                else:
                    subprocess.Popen(["loginctl", "lock-session"])
                return "PC gesperrt."
            if action in {"clipboard_get", "clipboard_set"}:
                import pyperclip
                if action == "clipboard_get":
                    return pyperclip.paste() or "(Zwischenablage leer)"
                pyperclip.copy(text)
                return "In die Zwischenablage kopiert."
            width, height = gui.size()
            return (f"System: {platform.system()} {platform.release()} ({platform.machine()}), "
                    f"Bildschirm: {width}x{height}, Rechnername: {platform.node()}")

    def _register_phone(self) -> None:
        @self.add("open", (
            "Öffne etwas: eine URL, eine Datei (mit passender App), eine App oder einen Link "
            "(tel:, mailto:, geo:, whatsapp://…). Handy: App per Paketname (z. B. com.whatsapp). "
            "PC: Programm per Name mit kind='app' (z. B. notepad, calc, spotify, chrome)."
        ), _obj({
            "target": {"type": "string"},
            "kind": {"type": "string", "enum": ["auto", "url", "file", "app"]},
        }, ["target"]))
        def open_(target: str, kind: str = "auto") -> str:
            if kind == "auto":
                if "://" in target or target.split(":", 1)[0] in {"tel", "mailto", "geo", "sms"}:
                    kind = "url"
                elif "/" in target or (self.settings.workspace / os.path.expanduser(target)).exists():
                    kind = "file"
                elif target.count(".") >= 1 and " " not in target:
                    kind = "app"
                else:
                    kind = "url"
            if kind == "app":
                if shutil.which("monkey"):
                    return _run(["monkey", "-p", target, "-c", "android.intent.category.LAUNCHER", "1"])
                return _launch_program(target)
            if kind == "file":
                target = str(self.resolve(target))
            if _is_termux():
                return _termux("termux-open", target) if kind == "file" else _termux("termux-open-url", target)
            if kind == "file" and sys.platform == "win32":
                os.startfile(target)  # noqa: S606 – öffnet mit der verknüpften App
            elif kind == "file" and sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                webbrowser.open(target if kind == "url" else Path(target).as_uri())
            return f"Geöffnet: {target}"

        @self.add("list_apps", "Liste installierte Apps (Paketnamen) auf, optional gefiltert.", _obj({
            "filter": {"type": "string"},
        }, []))
        def list_apps(filter: str = "") -> str:  # noqa: A002 – Name kommt aus dem Schema
            if not shutil.which("pm"):
                raise ToolError("Nur auf Android verfügbar.")
            out = _run(["pm", "list", "packages", "-3"])
            pkgs = sorted(line.replace("package:", "").strip() for line in out.splitlines()[1:] if line.strip())
            if filter:
                pkgs = [p for p in pkgs if filter.lower() in p.lower()]
            return "\n".join(pkgs) or "Keine Apps gefunden."

        @self.add("phone", (
            "Handy-Funktionen über Termux:API: notify (Benachrichtigung), toast, speak (vorlesen), "
            "vibrate, torch (Taschenlampe an/aus), clipboard_get, clipboard_set, battery, location, "
            "wifi, volume, brightness, share (Text teilen), photo (Kamerafoto nach 'path')."
        ), _obj({
            "action": {"type": "string", "enum": [
                "notify", "toast", "speak", "vibrate", "torch", "clipboard_get", "clipboard_set",
                "battery", "location", "wifi", "volume", "brightness", "share", "photo",
            ]},
            "text": {"type": "string", "description": "Text für notify/toast/speak/clipboard_set/share."},
            "title": {"type": "string", "description": "Titel für notify."},
            "value": {"type": "string", "description": "on/off für torch, 0-255 für brightness, Pfad für photo."},
        }, ["action"]))
        def phone(action: str, text: str = "", title: str = "Jarvis", value: str = "") -> str:
            if action == "notify":
                return _termux("termux-notification", "--title", title, "--content", text)
            if action == "toast":
                return _termux("termux-toast", text)
            if action == "speak":
                return _termux("termux-tts-speak", text, timeout=120)
            if action == "vibrate":
                return _termux("termux-vibrate", "-d", value or "300")
            if action == "torch":
                return _termux("termux-torch", value or "on")
            if action == "clipboard_get":
                return _termux("termux-clipboard-get")
            if action == "clipboard_set":
                return _termux("termux-clipboard-set", stdin=text)
            if action == "battery":
                return _termux("termux-battery-status")
            if action == "location":
                return _termux("termux-location", "-p", "network", timeout=60)
            if action == "wifi":
                return _termux("termux-wifi-connectioninfo")
            if action == "volume":
                return _termux("termux-volume")
            if action == "brightness":
                return _termux("termux-brightness", value or "auto")
            if action == "share":
                return _termux("termux-share", "-a", "send", stdin=text)
            if action == "photo":
                dest = self.resolve(value or "jarvis-foto.jpg")
                return _termux("termux-camera-photo", str(dest), timeout=60)
            raise ToolError(f"Unbekannte Aktion: {action}")
