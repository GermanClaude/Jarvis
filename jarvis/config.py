"""Konfiguration über Umgebungsvariablen oder eine .env-Datei."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Provider:
    label: str
    base_url: str
    key_envs: tuple[str, ...]
    model: str
    key_url: str
    free: bool


# Reihenfolge = Priorität bei der automatischen Erkennung über gesetzte Schlüssel.
PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("Claude (Anthropic)", "", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
                          "claude-opus-5", "https://console.anthropic.com/", False),
    "gemini": Provider("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                       ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "gemini-flash-latest",
                       "https://aistudio.google.com/apikey", True),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",),
                     "llama-3.3-70b-versatile", "https://console.groq.com/keys", True),
    "openrouter": Provider("OpenRouter", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",),
                           "meta-llama/llama-3.3-70b-instruct:free", "https://openrouter.ai/keys", True),
    "mistral": Provider("Mistral", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",),
                        "mistral-small-latest", "https://console.mistral.ai/api-keys", True),
    "ollama": Provider("Ollama (lokal)", "http://127.0.0.1:11434/v1", (), "llama3.1",
                       "https://ollama.com", True),
    "openai": Provider("OpenAI-kompatibel", "", ("OPENAI_API_KEY",), "", "", False),
}
DEFAULT_PROVIDER = "gemini"


def _detect_provider() -> str:
    explicit = os.environ.get("JARVIS_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    for name, provider in PROVIDERS.items():
        if any(os.environ.get(env) for env in provider.key_envs):
            return name
    return DEFAULT_PROVIDER


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
        # Sprachmodell: Anbieter, Schlüssel, Adresse und Modell.
        self.provider = _detect_provider()
        info = PROVIDERS.get(self.provider)
        self.api_key = os.environ.get("JARVIS_API_KEY", "")
        if not self.api_key and info:
            self.api_key = next((os.environ[e] for e in info.key_envs if os.environ.get(e)), "")
        self.base_url = os.environ.get("JARVIS_BASE_URL", info.base_url if info else "")
        self.model = os.environ.get("JARVIS_MODEL") or (info.model if info else "")
        self.max_tokens = int(os.environ.get("JARVIS_MAX_TOKENS", "8192"))
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

    def setup_problem(self) -> str | None:
        """Beschreibt, was an der Modell-Konfiguration fehlt – oder None, wenn alles passt."""
        env_file = self.data_dir / ".env"
        info = PROVIDERS.get(self.provider)
        if info is None:
            return (f"Unbekannter Anbieter JARVIS_PROVIDER={self.provider}.\n"
                    f"Möglich sind: {', '.join(PROVIDERS)}.")
        if self.provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return ("Für Claude fehlt das Paket 'anthropic'.\n"
                        "Installieren mit: pip install -r requirements-claude.txt")
        if info.key_envs and not self.api_key:
            return (f"Kein API-Schlüssel für {info.label} gefunden.\n"
                    f"Trage in {env_file} ein:\n  {info.key_envs[0]}=...\n"
                    + (f"Schlüssel gibt es unter {info.key_url}" if info.key_url else ""))
        if not self.base_url and self.provider != "anthropic":
            return f"Für JARVIS_PROVIDER={self.provider} fehlt JARVIS_BASE_URL in {env_file}."
        if not self.model:
            return f"Kein Modell gesetzt. Trage JARVIS_MODEL=... in {env_file} ein."
        return None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.brain_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
