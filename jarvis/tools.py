"""Werkzeuge, die Jarvis selbstständig benutzen kann.

Jedes Tool hat ein JSON-Schema (für Claude) und eine Python-Funktion (die es ausführt).
Handy-Funktionen laufen über Termux:API und melden sich sauber ab, wenn sie fehlen.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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
    """Fehler, der als verständliche Meldung an Claude zurückgeht."""


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
        tool = self.tools.get(name)
        if tool is None:
            return f"Unbekanntes Tool: {name}", True
        problem = validate(tool.schema, args)
        if problem:
            return json.dumps({"INVALID_INPUT": problem, "received": args}, ensure_ascii=False), True
        try:
            return _truncate(tool.func(**args)), False
        except ToolError as exc:
            return str(exc), True
        except Exception as exc:  # noqa: BLE001 – Fehler gehen als Text an Claude zurück
            return f"{type(exc).__name__}: {exc}", True

    # ------------------------------------------------------------ Registrierung
    def _register_all(self) -> None:
        self._register_memory()
        self._register_files()
        self._register_code()
        self._register_phone()

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

    def _register_phone(self) -> None:
        @self.add("open", (
            "Öffne etwas auf dem Handy: eine URL, eine Datei (mit passender App), eine App per Paketname "
            "(z. B. com.whatsapp) oder einen Intent-Link (tel:, mailto:, geo:, whatsapp://…)."
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
                if not shutil.which("monkey") and not shutil.which("am"):
                    raise ToolError("Apps starten geht nur auf Android (Termux).")
                return _run(["monkey", "-p", target, "-c", "android.intent.category.LAUNCHER", "1"])
            if kind == "file":
                target = str(self.resolve(target))
            if _is_termux():
                return _termux("termux-open", target) if kind == "file" else _termux("termux-open-url", target)
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
