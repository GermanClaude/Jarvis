"""Anbindung an Sprachmodelle.

Intern speichert Jarvis Nachrichten im Anthropic-Format (Content-Blöcke). Zwei Backends:

- ``AnthropicBackend``: Claude über das offizielle SDK (kostenpflichtig, optional installiert).
- ``OpenAICompatBackend``: alle Anbieter mit OpenAI-kompatibler Schnittstelle – Google Gemini,
  Groq, OpenRouter, Mistral, Ollama … (viele davon kostenlos). Nur Standardbibliothek.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from .config import PROVIDERS, Settings

Emit = Callable[[str, dict], None]
Transport = Callable[[dict], Iterable[dict]]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Jarvis baut den Verlauf jedes Mal neu (aktuelles Profil im System-Prompt, gekürzte Verläufe,
# Platzhalter statt Bildern). Neuere Claude-Konten lehnen dann wiedergegebene Denkblöcke mit 400 ab –
# mit "drop_block" verwirft die API nur die betroffenen Denkblöcke und antwortet trotzdem.
BINDING_BETA = "thinking-binding-controls-2026-08-01"
WEB_TOOL_NAMES = {"web_search", "web_fetch"}
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 30


class LLMError(Exception):
    """Fehler beim Sprachmodell, mit einer Meldung, die man dem Nutzer zeigen kann."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Turn:
    """Eine Modellantwort: Content-Blöcke im Anthropic-Format und der Grund fürs Ende."""

    content: list[dict]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | pause_turn


def parse_json_object(text: str) -> dict | None:
    """Liest ein JSON-Objekt aus einer Modellantwort, auch wenn es in ``` oder Text eingebettet ist."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def make_backend(settings: Settings, client=None):
    if client is not None or settings.provider == "anthropic":
        return AnthropicBackend(settings, client)
    return OpenAICompatBackend(settings)


# ================================================================ Claude
class AnthropicBackend:
    def __init__(self, settings: Settings, client=None) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("Für Claude fehlt das Paket 'anthropic': pip install -r requirements-claude.txt") from exc
        self.anthropic = anthropic
        self.settings = settings
        self.client = client or anthropic.Anthropic()

    def _tools(self, tools: list[dict]) -> list[dict]:
        # Claude sucht serverseitig im Web – die lokalen Web-Werkzeuge werden dann nicht gebraucht.
        tools = [t for t in tools if t["name"] not in WEB_TOOL_NAMES]
        if self.settings.web_tools:
            tools += [
                {"type": "web_search_20260209", "name": "web_search"},
                {"type": "web_fetch_20260209", "name": "web_fetch"},
            ]
        return tools

    def _fallbacks(self, kwargs: dict) -> dict:
        if self.settings.fallbacks:
            kwargs["betas"] = [*kwargs.get("betas", []), FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _call(self, func):
        a = self.anthropic
        try:
            return func()
        except a.AuthenticationError as exc:
            raise LLMError("API-Schlüssel ungültig. Prüfe ANTHROPIC_API_KEY.", 401) from exc
        except a.RateLimitError as exc:
            raise LLMError("Zu viele Anfragen – kurz warten und nochmal versuchen.", 429) from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API-Fehler {exc.status_code}: {exc.message}", exc.status_code) from exc
        except a.APIConnectionError as exc:
            raise LLMError("Keine Verbindung zur API. Internet prüfen.") from exc

    def stream_turn(self, system: tuple[str, str], messages: list[dict], tools: list[dict], emit: Emit) -> Turn:
        persona, profile = system
        kwargs = self._fallbacks({
            "model": self.settings.model,
            "max_tokens": 64000,
            "system": [
                {"type": "text", "text": persona, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": profile},
            ],
            "tools": self._tools(tools),
            "thinking": {"type": "adaptive", "display": "summarized",
                         "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "output_config": {"effort": self.settings.effort},
            "cache_control": {"type": "ephemeral"},
            "betas": [BINDING_BETA],
        })

        def run():
            with self.client.beta.messages.stream(messages=messages, **kwargs) as stream:
                for event in stream:
                    if event.type == "text":
                        emit("text", {"delta": event.text})
                    elif event.type == "thinking":
                        emit("thinking", {"delta": event.thinking})
                    elif event.type == "content_block_start":
                        block = event.content_block
                        if block.type in {"tool_use", "server_tool_use"}:
                            emit("tool", {"id": block.id, "name": block.name})
                    elif event.type == "content_block_stop":
                        block = getattr(event, "content_block", None)
                        if block is not None and block.type in {"tool_use", "server_tool_use"}:
                            emit("tool_input", {"id": block.id, "name": block.name, "input": block.input})
                return stream.get_final_message()

        response = self._call(run)
        return Turn([block.to_dict() for block in response.content], response.stop_reason)

    def complete_json(self, prompt: str, schema: dict) -> dict | None:
        kwargs = self._fallbacks({
            "model": self.settings.model,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.settings.reflect_effort,
                              "format": {"type": "json_schema", "schema": schema}},
        })
        try:
            response = self._call(lambda: self.client.beta.messages.create(**kwargs))
        except LLMError:
            return None
        if response.stop_reason in {"refusal", "max_tokens"}:
            return None
        return parse_json_object(next((b.text for b in response.content if b.type == "text"), ""))

    def list_models(self) -> list[str]:
        return [m.id for m in self._call(lambda: list(self.client.models.list()))]


# ================================================================ OpenAI-kompatibel
def _call_id(original: str) -> str:
    """Stabile, 9-stellige alphanumerische ID (manche Anbieter, z. B. Mistral, verlangen genau das)."""
    return hashlib.sha1(original.encode()).hexdigest()[:9]


def _result_text(block: dict) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ("FEHLER: " if block.get("is_error") else "") + str(content)


def _is_image(block: dict) -> bool:
    return block.get("type") == "image" and block.get("source", {}).get("type") == "base64"


def _image_part(block: dict) -> dict:
    src = block["source"]
    return {"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}}


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Wandelt den Verlauf (Anthropic-Format) in Chat-Completions-Nachrichten um.

    Jeder Tool-Aufruf bekommt genau ein Ergebnis; verwaiste Ergebnisse werden verworfen.
    Serverseitige Claude-Blöcke (Denken, Websuche) aus älteren Gesprächen fallen weg.
    """
    out: list[dict] = [{"role": "system", "content": system}]
    pending: list[str] = []

    def flush() -> None:
        for cid in pending:
            out.append({"role": "tool", "tool_call_id": cid, "content": "(kein Ergebnis)"})
        pending.clear()

    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            flush()
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            flush()
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [
                {"id": _call_id(b["id"]), "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                for b in content if b.get("type") == "tool_use"
            ]
            if not text and not calls:
                continue
            entry: dict = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
                pending.extend(c["id"] for c in calls)
            out.append(entry)
            continue

        parts: list[dict] = []
        tool_images: list[dict] = []
        for block in content:
            kind = block.get("type")
            if kind == "tool_result":
                cid = _call_id(block["tool_use_id"])
                if cid in pending:
                    pending.remove(cid)
                    out.append({"role": "tool", "tool_call_id": cid, "content": _result_text(block)})
                    if isinstance(block.get("content"), list):
                        tool_images += [_image_part(b) for b in block["content"] if _is_image(b)]
            elif kind == "text":
                parts.append({"type": "text", "text": block["text"]})
            elif _is_image(block):
                parts.append(_image_part(block))
        if tool_images:
            # Tool-Nachrichten können bei OpenAI-kompatiblen APIs keine Bilder tragen → eigene Nachricht.
            flush()
            out.append({"role": "user", "content": [
                {"type": "text", "text": "(Bild aus dem letzten Werkzeug-Ergebnis)"}, *tool_images]})
        if parts:
            flush()
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    flush()
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in tools if "input_schema" in t
    ]


class OpenAICompatBackend:
    def __init__(self, settings: Settings, transport: Transport | None = None) -> None:
        self.settings = settings
        self.transport = transport or self._http

    # ------------------------------------------------------------ HTTP
    def _headers(self, stream: bool) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "Jarvis/0.2",
        }
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        if self.settings.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/GermanClaude/Jarvis"
            headers["X-Title"] = "Jarvis"
        return headers

    def _error(self, status: int, detail: str) -> LLMError:
        info = PROVIDERS.get(self.settings.provider)
        key_env = info.key_envs[0] if info and info.key_envs else "JARVIS_API_KEY"
        if status in {401, 403}:
            msg = f"API-Schlüssel ungültig oder ohne Berechtigung. Prüfe {key_env} in ~/jarvis-data/.env."
        elif status == 429:
            msg = ("Kostenloses Limit erreicht oder zu viele Anfragen – kurz warten und nochmal versuchen "
                   "(Tageslimits setzen sich meist nach 24 Stunden zurück).")
        elif status == 404:
            msg = (f"Modell '{self.settings.model}' nicht gefunden. "
                   "Mit 'jarvis models' die verfügbaren Modelle anzeigen und JARVIS_MODEL anpassen.")
        else:
            msg = f"API-Fehler {status}"
        if detail and status not in {401, 403}:
            msg += f" ({detail[:300]})"
        return LLMError(msg, status)

    def _open(self, path: str, payload: dict | None):
        url = self.settings.base_url.rstrip("/") + path
        data = json.dumps(payload).encode() if payload is not None else None
        stream = bool(payload and payload.get("stream"))
        for attempt in range(3):
            request = urllib.request.Request(url, data=data, headers=self._headers(stream),
                                             method="POST" if data else "GET")
            try:
                return urllib.request.urlopen(request, timeout=300)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                try:
                    body = json.loads(detail)
                    body = body[0] if isinstance(body, list) and body else body
                    err = body.get("error", body) if isinstance(body, dict) else body
                    detail = err.get("message", detail) if isinstance(err, dict) else str(err)
                except (json.JSONDecodeError, AttributeError):
                    pass
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                wait = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2.0 * (attempt + 1)
                if exc.code in RETRY_STATUS and attempt < 2 and wait <= MAX_RETRY_WAIT:
                    time.sleep(wait)
                    continue
                raise self._error(exc.code, detail.strip()) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                hint = " Läuft 'ollama serve'?" if self.settings.provider == "ollama" else " Internet prüfen."
                raise LLMError(f"Keine Verbindung zu {url}.{hint}") from exc
        raise LLMError("Keine Antwort vom Anbieter.")

    def _http(self, payload: dict) -> Iterator[dict]:
        """Sendet eine Chat-Anfrage; liefert die Stream-Chunks bzw. die ganze Antwort als einen Chunk."""
        with self._open("/chat/completions", payload) as response:
            if not payload.get("stream"):
                yield json.load(response)
                return
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    err = chunk["error"]
                    raise LLMError(f"API-Fehler: {err.get('message', err) if isinstance(err, dict) else err}")
                yield chunk

    # ------------------------------------------------------------ Anfragen
    def stream_turn(self, system: tuple[str, str], messages: list[dict], tools: list[dict], emit: Emit) -> Turn:
        payload: dict = {
            "model": self.settings.model,
            "messages": to_openai_messages("\n\n".join(system), messages),
            "max_tokens": self.settings.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = to_openai_tools(tools)

        text: list[str] = []
        calls: dict[int, dict] = {}
        finish = None
        for chunk in self.transport(payload):
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    emit("thinking", {"delta": reasoning})
                if isinstance(delta.get("content"), str) and delta["content"]:
                    text.append(delta["content"])
                    emit("text", {"delta": delta["content"]})
                for i, call in enumerate(delta.get("tool_calls") or []):
                    slot = calls.setdefault(call.get("index", i), {"id": "", "name": "", "args": ""})
                    slot["id"] = call.get("id") or slot["id"]
                    function = call.get("function") or {}
                    slot["name"] = function.get("name") or slot["name"]
                    args = function.get("arguments")
                    if isinstance(args, dict):
                        slot["args"] = json.dumps(args)
                    elif args:
                        slot["args"] += args
                finish = choice.get("finish_reason") or finish

        content: list[dict] = []
        if "".join(text).strip():
            content.append({"type": "text", "text": "".join(text)})
        for index in sorted(calls):
            slot = calls[index]
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except json.JSONDecodeError as exc:
                raise ValueError("Tool-Eingabe ist kein gültiges JSON") from exc
            if not isinstance(args, dict):
                raise ValueError("Tool-Eingabe ist kein Objekt")
            block = {"type": "tool_use", "id": slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                     "name": slot["name"], "input": {k: v for k, v in args.items() if v is not None}}
            emit("tool", {"id": block["id"], "name": block["name"]})
            emit("tool_input", {"id": block["id"], "name": block["name"], "input": block["input"]})
            content.append(block)

        if finish == "length":
            stop = "max_tokens"
        elif calls:
            stop = "tool_use"
        elif finish == "content_filter":
            stop = "refusal"
        else:
            stop = "end_turn"
        return Turn(content, stop)

    def complete_json(self, prompt: str, schema: dict) -> dict | None:
        payload: dict = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": "Antworte ausschließlich mit einem JSON-Objekt nach diesem "
                                              f"JSON-Schema, ohne weiteren Text:\n{json.dumps(schema)}"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.settings.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        try:
            chunks = list(self.transport(payload))
        except LLMError as exc:
            if exc.status != 400:
                return None
            payload.pop("response_format")  # nicht jedes Modell kennt den JSON-Modus
            try:
                chunks = list(self.transport(payload))
            except LLMError:
                return None
        try:
            choice = chunks[0]["choices"][0]
            text = choice["message"]["content"] or ""
        except (IndexError, KeyError, TypeError):
            return None
        if choice.get("finish_reason") == "length":
            return None
        return parse_json_object(text)

    def list_models(self) -> list[str]:
        with self._open("/models", None) as response:
            data = json.load(response)
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        return sorted(str(m.get("id") or m.get("name")) for m in items if isinstance(m, dict))
