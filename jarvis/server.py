"""Kleiner Webserver (nur Standardbibliothek) für die Handy-Oberfläche.

Läuft in Termux ohne zusätzliche Pakete. Chat-Antworten werden per Server-Sent Events gestreamt.
"""

from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent import Jarvis
from .brain import Brain
from .config import Settings
from .tools import ToolError

WEB_DIR = Path(__file__).parent / "web"
MAX_BODY = 25 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class Api:
    """Verbindet HTTP-Routen mit Brain, Toolbox und Agent."""

    def __init__(self, jarvis: Jarvis) -> None:
        self.jarvis = jarvis
        self.brain: Brain = jarvis.brain
        self.settings: Settings = jarvis.settings

    def routes(self):
        return [
            ("GET", r"/api/status", self.status),
            ("GET", r"/api/conversations", lambda h, m: self.brain.conversations()),
            ("GET", r"/api/conversations/(\d+)", self.get_conversation),
            ("DELETE", r"/api/conversations/(\d+)", lambda h, m: {"ok": self.brain.delete_conversation(int(m[1]))}),
            ("POST", r"/api/conversations/(\d+)/reflect", self.reflect),
            ("GET", r"/api/facts", lambda h, m: self.brain.facts()),
            ("POST", r"/api/facts", self.add_fact),
            ("PUT", r"/api/facts/(\d+)", self.update_fact),
            ("DELETE", r"/api/facts/(\d+)", lambda h, m: {"ok": self.brain.delete_fact(int(m[1]))}),
            ("GET", r"/api/notes", lambda h, m: self.brain.list_notes()),
            ("GET", r"/api/notes/(.+)", self.get_note),
            ("PUT", r"/api/notes/(.+)", self.put_note),
            ("DELETE", r"/api/notes/(.+)", lambda h, m: {"ok": self.brain.delete_note(h.unquote(m[1]))}),
            ("GET", r"/api/tasks", lambda h, m: self.brain.tasks(include_done=h.query.get("all") == "1")),
            ("POST", r"/api/tasks", self.add_task),
            ("POST", r"/api/tasks/(\d+)/done", lambda h, m: {"ok": self.brain.complete_task(int(m[1]))}),
            ("DELETE", r"/api/tasks/(\d+)", lambda h, m: {"ok": self.brain.delete_task(int(m[1]))}),
            ("GET", r"/api/files", self.list_files),
            ("GET", r"/api/file", self.read_file),
            ("PUT", r"/api/file", self.write_file),
            ("POST", r"/api/chat", self.chat),
        ]

    # ------------------------------------------------------------ Handler
    def status(self, h, m):
        return {
            "provider": self.settings.provider,
            "model": self.settings.model,
            "workspace": str(self.settings.workspace),
            "shell": self.settings.allow_shell,
            "facts": len(self.brain.facts()),
            "notes": len(self.brain.list_notes()),
            "tasks": len(self.brain.tasks()),
        }

    def get_conversation(self, h, m):
        conv = self.brain.conversation(int(m[1]))
        if conv is None:
            raise LookupError("Gespräch nicht gefunden")
        return {**conv, "messages": self.brain.messages(conv["id"])}

    def reflect(self, h, m):
        data = self.jarvis.reflect(int(m[1]))
        return {"ok": data is not None, "result": data}

    def add_fact(self, h, m):
        body = h.json()
        return {"id": self.brain.add_fact(body["content"], body.get("category", "sonstiges"))}

    def update_fact(self, h, m):
        body = h.json()
        return {"ok": self.brain.update_fact(int(m[1]), body["content"], body.get("category"))}

    def get_note(self, h, m):
        return {"title": h.unquote(m[1]), "content": self.brain.read_note(h.unquote(m[1]))}

    def put_note(self, h, m):
        body = h.json()
        path = self.brain.save_note(h.unquote(m[1]), body["content"], body.get("mode", "overwrite"))
        return {"ok": True, "file": path.name}

    def add_task(self, h, m):
        body = h.json()
        return {"id": self.brain.add_task(body["title"], body.get("due"), body.get("notes"))}

    def list_files(self, h, m):
        target = self.jarvis.toolbox.resolve(h.query.get("path", "."))
        if not target.is_dir():
            raise LookupError("Kein Ordner")
        entries = []
        for p in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                size = p.stat().st_size if p.is_file() else None
            except OSError:
                size = None
            entries.append({"name": p.name, "dir": p.is_dir(), "size": size})
        return {"path": str(target), "parent": str(target.parent), "entries": entries}

    def read_file(self, h, m):
        target = self.jarvis.toolbox.resolve(h.query.get("path", ""))
        if not target.is_file():
            raise LookupError("Datei nicht gefunden")
        if target.stat().st_size > 2_000_000:
            raise ValueError("Datei zu groß für den Editor (> 2 MB)")
        return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace")}

    def write_file(self, h, m):
        body = h.json()
        target = self.jarvis.toolbox.resolve(body["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body["content"], encoding="utf-8")
        return {"ok": True, "path": str(target)}

    def chat(self, h, m):
        body = h.json()
        message = str(body.get("message", "")).strip()
        images = []
        for img in body.get("images", [])[:5]:
            media_type = img.get("media_type")
            data = img.get("data", "")
            if media_type not in IMAGE_TYPES:
                raise ValueError(f"Bildtyp nicht unterstützt: {media_type}")
            base64.b64decode(data, validate=True)
            images.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
        if not message and not images:
            raise ValueError("Leere Nachricht")
        conv_id = body.get("conversation_id")

        h.start_sse()
        self.jarvis.chat(int(conv_id) if conv_id else None, message or "(siehe Bild)", images, h.send_event)
        return None


class Handler(BaseHTTPRequestHandler):
    api: Api
    routes: list
    token: str
    server_version = "Jarvis/0.1"

    def log_message(self, fmt, *args):  # ruhigere Konsole
        if "/api/" in self.path and not self.path.startswith("/api/chat"):
            return
        super().log_message(fmt, *args)

    # ------------------------------------------------------------ Hilfen
    @staticmethod
    def unquote(value: str) -> str:
        from urllib.parse import unquote

        return unquote(value)

    def json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Anfrage zu groß")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        return data

    def send_json(self, data, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self._sse = True

    def send_event(self, event: str, data: dict) -> None:
        try:
            chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser geschlossen – Jarvis arbeitet trotzdem zu Ende und speichert alles.

    def authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else self.query.get("token", "")
        return hmac.compare_digest(supplied.encode(), self.token.encode())

    # ------------------------------------------------------------ Dispatch
    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self._sse = False
        path = parsed.path

        if not path.startswith("/api/"):
            if method == "GET":
                return self.serve_static(path)
            return self.send_json({"error": "Nicht gefunden"}, 404)

        if not self.authorized():
            return self.send_json({"error": "Nicht autorisiert"}, 401)

        for route_method, pattern, func in self.routes:
            if route_method != method:
                continue
            match = re.fullmatch(pattern, path)
            if not match:
                continue
            try:
                result = func(self, match)
            except (LookupError, FileNotFoundError) as exc:
                return self._error(str(exc), HTTPStatus.NOT_FOUND)
            except (ValueError, KeyError, TypeError, ToolError, FileExistsError) as exc:
                return self._error(str(exc), HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001
                return self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
            if not self._sse:
                self.send_json(result)
            return None
        return self.send_json({"error": "Nicht gefunden"}, 404)

    def _error(self, message: str, status: int) -> None:
        if self._sse:
            self.send_event("error", {"message": message})
        else:
            self.send_json({"error": message}, status)

    def serve_static(self, path: str) -> None:
        name = "index.html" if path in {"/", ""} else path.lstrip("/")
        target = (WEB_DIR / name).resolve()
        if not target.is_relative_to(WEB_DIR.resolve()) or not target.is_file():
            target = WEB_DIR / "index.html"
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def serve(jarvis: Jarvis, settings: Settings) -> None:
    api = Api(jarvis)
    handler = type("JarvisHandler", (Handler,), {"api": api, "routes": api.routes(), "token": settings.token})
    httpd = ThreadingHTTPServer((settings.host, settings.port), handler)
    httpd.daemon_threads = True
    url = f"http://{'127.0.0.1' if settings.host in {'0.0.0.0', '::'} else settings.host}:{settings.port}"
    print(f"\n  JARVIS ist online  →  {url}")
    if settings.token:
        print(f"  Zugangscode: {settings.token}")
    print(f"  Modell: {settings.model} ({settings.provider})")
    print(f"  Daten: {settings.data_dir}   Arbeitsordner: {settings.workspace}")
    print("  Beenden mit Strg+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nJarvis fährt herunter. Bis bald.")
    finally:
        httpd.server_close()
