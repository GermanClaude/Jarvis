"""Konfiguration über Umgebungsvariablen oder eine .env-Datei."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Parser (KEY=VALUE pro Zeile), überschreibt nichts Gesetztes."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


DATA_DIR = Path(os.environ.get("JARVIS_HOME", Path.home() / "jarvis-data")).expanduser()
_load_dotenv(DATA_DIR / ".env")
_load_dotenv(Path.cwd() / ".env")
DATA_DIR = Path(os.environ.get("JARVIS_HOME", DATA_DIR)).expanduser()


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


class Settings:
    def __init__(self) -> None:
        self.data_dir = DATA_DIR
        self.brain_dir = DATA_DIR / "brain"
        self.db_path = DATA_DIR / "jarvis.db"
        self.workspace = Path(os.environ.get("JARVIS_WORKSPACE", Path.home())).expanduser().resolve()

        # Zusätzliche Ordner, auf die Jarvis zugreifen darf (z. B. der Handyspeicher).
        roots = [self.workspace, self.data_dir.resolve()]
        extra = os.environ.get("JARVIS_EXTRA_ROOTS", "/storage/emulated/0:/sdcard")
        for part in extra.split(":"):
            if part and Path(part).exists():
                roots.append(Path(part).resolve())
        self.allowed_roots = list(dict.fromkeys(roots))

        self.user_name = os.environ.get("JARVIS_USER_NAME", "")
        self.model = os.environ.get("JARVIS_MODEL", "claude-opus-5")
        self.effort = os.environ.get("JARVIS_EFFORT", "high")
        self.reflect_effort = os.environ.get("JARVIS_REFLECT_EFFORT", "low")
        self.fallbacks = _flag("JARVIS_FALLBACKS", True)
        self.web_tools = _flag("JARVIS_WEB_TOOLS", True)
        self.allow_shell = _flag("JARVIS_ALLOW_SHELL", True)
        self.reflect_every = int(os.environ.get("JARVIS_REFLECT_EVERY", "6"))
        self.history_messages = int(os.environ.get("JARVIS_HISTORY_MESSAGES", "60"))

        self.host = os.environ.get("JARVIS_HOST", "127.0.0.1")
        self.port = int(os.environ.get("JARVIS_PORT", "8765"))
        self.token = os.environ.get("JARVIS_TOKEN", "")
        if not self.token and self.host not in {"127.0.0.1", "localhost", "::1"}:
            # Wer Jarvis im Netzwerk erreichbar macht, braucht ein Passwort.
            self.token = secrets.token_urlsafe(16)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.brain_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
