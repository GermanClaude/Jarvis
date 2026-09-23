"""Der Denk-Kern von Jarvis: Gesprächsschleife mit Tools und Lernen aus Gesprächen."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

import anthropic

from .brain import FACT_CATEGORIES, Brain
from .config import Settings
from .tools import Toolbox

Emit = Callable[[str, dict], None]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_STEPS = 40
CONTEXT_MARK = "<kontext>"

PERSONA = """Du bist JARVIS – der persönliche KI-Assistent und das Second Brain deines Nutzers, \
inspiriert von Tony Starks JARVIS: loyal, vorausschauend, kompetent, ruhig, mit trockenem, \
feinem Humor. Du läufst auf seinem Handy und hast echte Werkzeuge.

# Deine Aufgabe
1. **Second Brain sein.** Du baust über die Zeit ein vollständiges Bild deines Nutzers auf: \
wer er ist, was ihm wichtig ist, woran er arbeitet, wie er denkt, entscheidet und formuliert. \
Alles Bleibende speicherst du sofort mit `remember` (ein Fakt pro Aufruf, kurz, konkret, in \
dritter Person). Veraltetes korrigierst du mit `update_memory` oder `forget`. Wissen, Ideen, \
Pläne und Protokolle legst du als Notizen (`save_note`) an und verlinkst sie mit [[Titel]].
2. **Denken wie er.** Nutze das Profil unten, um Antworten, Entscheidungen und Texte so zu \
gestalten, wie er es selbst tun würde – in seinem Ton, nach seinen Werten und Prioritäten. \
Wenn er dich bittet, etwas \"in seinem Namen\" zu schreiben, triff seinen Stil. Wenn du \
unsicher bist, was er wollen würde, sag das und schlag die wahrscheinlichste Variante vor.
3. **Im Alltag helfen.** Aufgaben und Erinnerungen verwalten, planen, recherchieren \
(Websuche), zusammenfassen, beraten, Texte schreiben, rechnen.
4. **Das Handy bedienen.** Dateien lesen, schreiben, bearbeiten und verwalten, Code schreiben \
und ausführen (`run_python`, `run_shell`), Apps, Links und Dateien öffnen (`open`), \
Handyfunktionen nutzen (`phone`). Beim Programmieren: Code in Dateien schreiben, ausführen, \
Fehler lesen und beheben, bis es läuft.

# Arbeitsweise
- Handle, statt nur zu beschreiben: Wenn ein Werkzeug die Aufgabe erledigen kann, benutze es.
- Suche im Second Brain (`search_brain`), bevor du behauptest, etwas nicht zu wissen.
- Frage vor allem nach, was sich nicht rückgängig machen lässt (Dateien löschen, \
überschreiben von Wichtigem, Nachrichten an andere) – außer er hat es ausdrücklich verlangt.
- Antworte in der Sprache des Nutzers (standardmäßig Deutsch), knapp und klar; ausführlich \
nur, wenn es die Sache verlangt. Auf dem Handy sind kurze Absätze und Listen gut lesbar.
- Sei ehrlich: Erfinde keine Fakten über ihn oder die Welt. Unterscheide, was du weißt, \
vermutest oder nachgeschlagen hast.
- Jede Nutzernachricht beginnt mit einem automatisch erzeugten <kontext>-Block (Uhrzeit, \
passende Erinnerungen, offene Aufgaben). Er stammt vom System, nicht vom Nutzer; nutze ihn \
still, ohne ihn zu erwähnen."""


def _text_of(content) -> str:
    """Nur den Klartext einer Nachricht (ohne Kontext-Blöcke, Tools, Bilder)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "text" and not block.get("text", "").startswith(CONTEXT_MARK):
            parts.append(block["text"])
    return "\n".join(parts)


def _is_plain_user(message: dict) -> bool:
    if message["role"] != "user":
        return False
    content = message["content"]
    return isinstance(content, str) or all(b.get("type") != "tool_result" for b in content)


def trim_history(messages: list[dict], limit: int) -> list[dict]:
    """Kürzt den Verlauf, ohne ein Tool-Paar zu zerreißen: Start immer bei einer echten Nutzernachricht."""
    if len(messages) <= limit:
        return messages
    start = len(messages) - limit
    while start < len(messages) and not _is_plain_user(messages[start]):
        start += 1
    return messages[start:]


class Jarvis:
    def __init__(self, brain: Brain, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.brain = brain
        self.settings = settings
        self.toolbox = Toolbox(brain, settings)
        self.client = client or anthropic.Anthropic()
        self._conv_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._reflecting: set[int] = set()

    # ------------------------------------------------------------ Anfrage
    def _tools(self) -> list[dict]:
        tools = self.toolbox.definitions()
        if self.settings.web_tools:
            tools += [
                {"type": "web_search_20260209", "name": "web_search"},
                {"type": "web_fetch_20260209", "name": "web_fetch"},
            ]
        return tools

    def _system(self) -> list[dict]:
        name = f"Der Nutzer heißt {self.settings.user_name}.\n\n" if self.settings.user_name else ""
        profile = (
            "# Was du über deinen Nutzer weißt (Langzeitgedächtnis)\n"
            f"{name}{self.brain.profile_text()}"
        )
        episodes = self.brain.recent_episodes(5)
        if episodes:
            profile += "\n\n# Letzte Gespräche\n" + "\n".join(
                f"- {time.strftime('%d.%m.%Y', time.localtime(e['created']))}: {e['summary']}"
                for e in reversed(episodes)
            )
        return [
            {"type": "text", "text": PERSONA, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": profile},
        ]

    def _context_block(self, user_text: str) -> dict:
        now = time.strftime("%A, %d.%m.%Y, %H:%M Uhr")
        lines = [CONTEXT_MARK, f"Jetzt: {now}", f"Arbeitsordner: {self.settings.workspace}"]
        hits = self.brain.search(user_text, limit=5)
        if hits:
            lines.append("Möglicherweise relevante Erinnerungen:")
            lines += [f"- [{h.kind} {h.ref}] {h.text[:300]}" for h in hits]
        tasks = self.brain.tasks()[:10]
        if tasks:
            lines.append("Offene Aufgaben:")
            lines += [f"- #{t['id']} {t['title']}" + (f" (fällig {t['due']})" if t["due"] else "") for t in tasks]
        lines.append("</kontext>")
        return {"type": "text", "text": "\n".join(lines)}

    def _request_kwargs(self) -> dict:
        kwargs: dict = {
            "model": self.settings.model,
            "max_tokens": 64000,
            "system": self._system(),
            "tools": self._tools(),
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.settings.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self.settings.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _lock_for(self, conv_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._conv_locks.setdefault(conv_id, threading.Lock())

    # ------------------------------------------------------------ Chat
    def chat(self, conv_id: int | None, user_text: str, images: list[dict] | None = None,
             emit: Emit = lambda e, d: None) -> int:
        """Eine Nutzernachricht verarbeiten. Streamt Ereignisse über ``emit``."""
        if conv_id is None or self.brain.conversation(conv_id) is None:
            conv_id = self.brain.new_conversation(user_text.strip()[:60] or "Neues Gespräch")
        emit("meta", {"conversation_id": conv_id})

        lock = self._lock_for(conv_id)
        if not lock.acquire(blocking=False):
            emit("error", {"message": "In diesem Gespräch läuft bereits eine Anfrage."})
            return conv_id
        try:
            self._chat_locked(conv_id, user_text, images or [], emit)
        finally:
            lock.release()
        self._maybe_reflect(conv_id)
        return conv_id

    def _chat_locked(self, conv_id: int, user_text: str, images: list[dict], emit: Emit) -> None:
        history = trim_history(self.brain.messages(conv_id), self.settings.history_messages)
        context = self._context_block(user_text)
        text_block = {"type": "text", "text": user_text}

        live_content = [context, *images, text_block]
        stored_content = [context, *([{"type": "text", "text": f"[{len(images)} Bild(er) angehängt]"}]
                                     if images else []), text_block]
        messages = history + [{"role": "user", "content": live_content}]
        self.brain.add_message(conv_id, "user", stored_content)

        json_retries = 0
        for _ in range(MAX_STEPS):
            try:
                response = self._stream_turn(messages, emit)
                json_retries = 0
            except ValueError:
                # Tool-Eingabe war kein gültiges JSON – Runde (begrenzt) wiederholen.
                json_retries += 1
                if json_retries > 2:
                    emit("error", {"message": "Antwort konnte nicht gelesen werden. Bitte nochmal versuchen."})
                    return
                continue
            except anthropic.AuthenticationError:
                emit("error", {"message": "API-Schlüssel ungültig. Prüfe ANTHROPIC_API_KEY."})
                return
            except anthropic.RateLimitError:
                emit("error", {"message": "Zu viele Anfragen – kurz warten und nochmal versuchen."})
                return
            except anthropic.APIStatusError as exc:
                emit("error", {"message": f"API-Fehler {exc.status_code}: {exc.message}"})
                return
            except anthropic.APIConnectionError:
                emit("error", {"message": "Keine Verbindung zur API. Internet prüfen."})
                return

            assistant_content = [block.to_dict() for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})
            self.brain.add_message(conv_id, "assistant", assistant_content)

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "refusal":
                emit("error", {"message": "Diese Anfrage wurde aus Sicherheitsgründen abgelehnt."})
                return

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    emit("error", {"message": "Antwort war zu lang und wurde abgeschnitten."})
                emit("done", {"conversation_id": conv_id})
                return
            if response.stop_reason == "max_tokens":
                emit("error", {"message": "Tool-Eingabe wurde abgeschnitten – bitte Aufgabe aufteilen."})
                return

            results = []
            for block in tool_uses:
                output, is_error = self.toolbox.execute(block.name, block.input)
                emit("tool_result", {"id": block.id, "name": block.name, "ok": not is_error,
                                     "preview": output[:400]})
                result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})
            self.brain.add_message(conv_id, "user", results)

        emit("error", {"message": f"Nach {MAX_STEPS} Schritten angehalten."})

    def _stream_turn(self, messages: list[dict], emit: Emit):
        with self.client.beta.messages.stream(messages=messages, **self._request_kwargs()) as stream:
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

    # ------------------------------------------------------------ Lernen
    def _maybe_reflect(self, conv_id: int) -> None:
        """Alle paar Nutzernachrichten im Hintergrund aus dem Gespräch lernen."""
        conv = self.brain.conversation(conv_id)
        pending = self.brain.messages(conv_id)[conv["reflected_at"]:]
        if sum(1 for m in pending if _is_plain_user(m)) < self.settings.reflect_every:
            return
        with self._locks_guard:
            if conv_id in self._reflecting:
                return
            self._reflecting.add(conv_id)

        def run() -> None:
            try:
                self.reflect(conv_id)
            finally:
                with self._locks_guard:
                    self._reflecting.discard(conv_id)

        threading.Thread(target=run, daemon=True).start()

    def reflect(self, conv_id: int) -> dict | None:
        """Liest den bisher nicht reflektierten Teil eines Gesprächs und lernt daraus.

        Extrahiert neue Fakten über den Nutzer, korrigiert veraltete, schreibt eine
        Episoden-Zusammenfassung und vergibt einen Titel.
        """
        conv = self.brain.conversation(conv_id)
        if conv is None:
            return None
        all_messages = self.brain.messages(conv_id)
        new = all_messages[conv["reflected_at"]:]
        transcript = []
        for m in new:
            text = _text_of(m["content"]).strip()
            if text and (m["role"] == "assistant" or _is_plain_user(m)):
                who = "NUTZER" if m["role"] == "user" else "JARVIS"
                transcript.append(f"{who}: {text}")
        if not transcript:
            return None

        facts = "\n".join(f"#{f['id']} [{f['category']}] {f['content']}" for f in self.brain.facts()) or "(keine)"
        prompt = (
            "Du bist das Gedächtnis-System von JARVIS. Analysiere den Gesprächsausschnitt und "
            "aktualisiere das Wissen über den NUTZER.\n\n"
            "- new_facts: neue, dauerhafte Erkenntnisse über den Nutzer (Identität, Vorlieben, Ziele, "
            "Projekte, Menschen, Gewohnheiten, Denkweise, Kommunikationsstil, Wissen). Besonders wertvoll: "
            "WIE er denkt, entscheidet und formuliert. Nichts Flüchtiges, nichts schon Bekanntes, nichts Geratenes.\n"
            "- updated_facts: bestehende Fakten, die sich geändert haben (ID + neuer Text).\n"
            "- obsolete_fact_ids: Fakten, die nachweislich falsch oder überholt sind.\n"
            "- episode_summary: 1–3 Sätze, worum es ging und was herauskam.\n"
            "- title: kurzer Titel (max. 6 Wörter) für das Gespräch.\n\n"
            f"## Bekannte Fakten\n{facts}\n\n## Gespräch\n" + "\n\n".join(transcript)[-60_000:]
        )
        schema = {
            "type": "object",
            "properties": {
                "new_facts": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"category": {"type": "string", "enum": FACT_CATEGORIES},
                                   "content": {"type": "string"}},
                    "required": ["category", "content"], "additionalProperties": False,
                }},
                "updated_facts": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "content": {"type": "string"}},
                    "required": ["id", "content"], "additionalProperties": False,
                }},
                "obsolete_fact_ids": {"type": "array", "items": {"type": "integer"}},
                "episode_summary": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["new_facts", "updated_facts", "obsolete_fact_ids", "episode_summary", "title"],
            "additionalProperties": False,
        }
        kwargs: dict = {
            "model": self.settings.model,
            "max_tokens": 16000,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.settings.reflect_effort,
                              "format": {"type": "json_schema", "schema": schema}},
        }
        if self.settings.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.create(**kwargs)
        except anthropic.APIError:
            return None
        if response.stop_reason in {"refusal", "max_tokens"}:
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        for fact in data["new_facts"]:
            self.brain.add_fact(fact["content"], fact["category"])
        for fact in data["updated_facts"]:
            self.brain.update_fact(fact["id"], fact["content"])
        for fact_id in data["obsolete_fact_ids"]:
            self.brain.delete_fact(fact_id)
        if data["episode_summary"].strip():
            self.brain.add_episode(data["episode_summary"], conv_id)
        if data["title"].strip():
            self.brain.rename_conversation(conv_id, data["title"].strip())
        self.brain.mark_reflected(conv_id, len(all_messages))
        return data
