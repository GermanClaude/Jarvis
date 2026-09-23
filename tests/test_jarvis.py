"""Offline-Tests: Gedächtnis, Tools und die Agent-Schleife mit simulierten APIs."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from jarvis.agent import Jarvis, trim_history
from jarvis.brain import Brain
from jarvis.config import Settings
from jarvis.llm import LLMError, OpenAICompatBackend, parse_json_object, to_openai_messages
from jarvis.tools import Toolbox, html_to_text, parse_ddg_results, validate

try:  # Claude ist optional (requirements-claude.txt)
    from anthropic.types.beta import BetaMessage
except ImportError:
    BetaMessage = None

PROVIDER_ENV = ["JARVIS_PROVIDER", "JARVIS_API_KEY", "JARVIS_MODEL", "JARVIS_BASE_URL", "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY",
                "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY"]
for _name in PROVIDER_ENV:
    os.environ.pop(_name, None)


def make_settings(tmp: Path) -> Settings:
    os.environ["JARVIS_EXTRA_ROOTS"] = ""
    s = Settings()
    s.data_dir = tmp / "data"
    s.brain_dir = s.data_dir / "brain"
    s.db_path = s.data_dir / "jarvis.db"
    s.workspace = (tmp / "ws").resolve()
    s.workspace.mkdir()
    s.allowed_roots = [s.workspace, s.data_dir.resolve()]
    s.ensure_dirs()
    return s


def message(content: list[dict], stop_reason: str):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


class FakeStream:
    def __init__(self, final: BetaMessage) -> None:
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self) -> BetaMessage:
        return self.final


class FakeMessages:
    def __init__(self, turns: list[BetaMessage], reflection: dict | None = None) -> None:
        self.turns = list(turns)
        self.reflection = reflection
        self.requests: list[dict] = []

    def stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return FakeStream(self.turns.pop(0))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return message([{"type": "text", "text": json.dumps(self.reflection)}], "end_turn")


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.beta = type("Beta", (), {"messages": messages})()


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.brain = Brain(self.tmp / "db.sqlite", self.tmp / "brain")

    def test_facts_and_profile(self):
        fid = self.brain.add_fact("Trinkt morgens Tee statt Kaffee", "vorliebe")
        self.assertEqual(self.brain.add_fact("trinkt morgens tee statt kaffee", "vorliebe"), fid)
        self.assertIn("Tee", self.brain.profile_text())
        self.brain.update_fact(fid, "Trinkt morgens Matcha")
        self.assertIn("Matcha", self.brain.profile_text())
        self.assertTrue(self.brain.delete_fact(fid))

    def test_notes_and_search(self):
        self.brain.save_note("Projekt Garten", "Hochbeet bauen, Tomaten pflanzen")
        self.brain.save_note("Projekt Garten", "Gießplan erstellen", mode="append")
        text = self.brain.read_note("Projekt Garten")
        self.assertIn("Hochbeet", text)
        self.assertIn("Gießplan", text)
        hits = self.brain.search("Tomaten Garten")
        self.assertEqual(hits[0].kind, "notiz")

    def test_tasks(self):
        tid = self.brain.add_task("Zahnarzt anrufen", "2026-10-01")
        self.assertEqual(len(self.brain.tasks()), 1)
        self.brain.complete_task(tid)
        self.assertEqual(len(self.brain.tasks()), 0)
        self.assertEqual(len(self.brain.tasks(include_done=True)), 1)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(self.tmp)
        self.brain = Brain(self.settings.db_path, self.settings.brain_dir)
        self.box = Toolbox(self.brain, self.settings)

    def test_file_roundtrip(self):
        out, err = self.box.execute("write_file", {"path": "code/hallo.py", "content": "print('hi')\n"})
        self.assertFalse(err, out)
        out, err = self.box.execute("edit_file", {"path": "code/hallo.py", "old_text": "hi", "new_text": "Jarvis"})
        self.assertFalse(err, out)
        out, _ = self.box.execute("read_file", {"path": "code/hallo.py"})
        self.assertIn("Jarvis", out)
        out, _ = self.box.execute("find_files", {"pattern": "*.py", "contains": "Jarvis"})
        self.assertIn("hallo.py", out)

    def test_path_escape_blocked(self):
        out, err = self.box.execute("read_file", {"path": "../../etc/passwd"})
        self.assertTrue(err)
        self.assertIn("Zugriff verweigert", out)

    def test_run_python(self):
        out, err = self.box.execute("run_python", {"code": "print(6*7)"})
        self.assertFalse(err)
        self.assertIn("42", out)

    def test_shell_blocklist(self):
        for cmd in ["rm  -rf   /", "rm -rf ~", "rm -rf $HOME/", "rm -r -f /*", "echo x; rm -rf / ", "mkfs.ext4 /dev/x"]:
            out, err = self.box.execute("run_shell", {"command": cmd})
            self.assertTrue(err, cmd)
            self.assertIn("gesperrt", out)
        out, err = self.box.execute("run_shell", {"command": "mkdir -p tmpdir && rm -rf tmpdir/ && echo ok"})
        self.assertFalse(err, out)
        self.assertIn("ok", out)

    def test_validation(self):
        schema = self.box.tools["remember"].schema
        self.assertIsNone(validate(schema, {"content": "x", "category": "ziel"}))
        self.assertIn("fehlt", validate(schema, {"content": "x"}))
        self.assertIn("einer von", validate(schema, {"content": "x", "category": "quatsch"}))
        out, err = self.box.execute("complete_task", {"task_id": "eins"})
        self.assertTrue(err)
        self.assertIn("INVALID_INPUT", out)

    def test_tool_definitions_are_well_formed(self):
        for d in self.box.definitions():
            self.assertTrue(d["eager_input_streaming"])
            self.assertEqual(d["input_schema"]["type"], "object")
            for key in d["input_schema"]["required"]:
                self.assertIn(key, d["input_schema"]["properties"])


@unittest.skipIf(BetaMessage is None, "Paket 'anthropic' nicht installiert")
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(self.tmp)
        self.settings.reflect_every = 100
        self.settings.provider = "anthropic"
        self.settings.model = "claude-opus-5"
        self.brain = Brain(self.settings.db_path, self.settings.brain_dir)

    def test_tool_loop_learns_and_persists(self):
        fake = FakeMessages([
            message([
                {"type": "text", "text": "Notiert."},
                {"type": "tool_use", "id": "toolu_1", "name": "remember",
                 "input": {"content": "Heißt Nathanael", "category": "identitaet"}},
            ], "tool_use"),
            message([{"type": "text", "text": "Freut mich, Nathanael."}], "end_turn"),
        ])
        jarvis = Jarvis(self.brain, self.settings, client=FakeClient(fake))
        events = []
        conv = jarvis.chat(None, "Ich heiße Nathanael", emit=lambda e, d: events.append((e, d)))

        self.assertIn("Heißt Nathanael", self.brain.profile_text())
        kinds = [e for e, _ in events]
        self.assertEqual(kinds[0], "meta")
        self.assertIn("tool_result", kinds)
        self.assertEqual(kinds[-1], "done")

        stored = self.brain.messages(conv)
        self.assertEqual([m["role"] for m in stored], ["user", "assistant", "user", "assistant"])
        self.assertEqual(stored[2]["content"][0]["tool_use_id"], "toolu_1")

        # Zweiter Request enthält das Tool-Ergebnis; Fallbacks & Caching sind gesetzt.
        second = fake.requests[1]
        self.assertEqual(second["messages"][-1]["content"][0]["type"], "tool_result")
        self.assertEqual(second["fallbacks"], "default")
        self.assertEqual(second["model"], "claude-opus-5")
        # Das Profil im System-Prompt ist beim zweiten Aufruf schon aktualisiert.
        self.assertIn("Heißt Nathanael", second["system"][1]["text"])
        # Mit Claude läuft die Websuche serverseitig – keine doppelten lokalen Web-Tools.
        names = [t["name"] for t in second["tools"]]
        self.assertEqual(names.count("web_search"), 1)
        self.assertIn("web_search_20260209", [t.get("type") for t in second["tools"]])

    def test_refusal_stops(self):
        fake = FakeMessages([message([], "refusal")])
        jarvis = Jarvis(self.brain, self.settings, client=FakeClient(fake))
        events = []
        jarvis.chat(None, "…", emit=lambda e, d: events.append(e))
        self.assertEqual(events[-1], "error")

    def test_reflection(self):
        fid = self.brain.add_fact("Wohnt in Hamburg", "identitaet")
        reflection = {
            "new_facts": [{"category": "denkweise", "content": "Entscheidet lieber schnell und korrigiert später"}],
            "updated_facts": [{"id": fid, "content": "Wohnt in Berlin"}],
            "obsolete_fact_ids": [],
            "episode_summary": "Umzug nach Berlin besprochen.",
            "title": "Umzug",
        }
        fake = FakeMessages([message([{"type": "text", "text": "Klingt gut."}], "end_turn")], reflection)
        jarvis = Jarvis(self.brain, self.settings, client=FakeClient(fake))
        conv = jarvis.chat(None, "Ich bin nach Berlin gezogen")
        result = jarvis.reflect(conv)

        self.assertEqual(result["title"], "Umzug")
        profile = self.brain.profile_text()
        self.assertIn("Berlin", profile)
        self.assertIn("Entscheidet lieber schnell", profile)
        self.assertEqual(self.brain.conversation(conv)["title"], "Umzug")
        self.assertEqual(self.brain.recent_episodes()[0]["summary"], "Umzug nach Berlin besprochen.")
        self.assertIsNone(jarvis.reflect(conv))  # nichts Neues → kein weiterer Aufruf


class HistoryTests(unittest.TestCase):
    def test_trim_history_never_starts_with_tool_result(self):
        msgs = []
        for i in range(10):
            msgs += [
                {"role": "user", "content": [{"type": "text", "text": f"frage {i}"}]},
                {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "fertig"}]},
            ]
        for limit in range(1, 20):
            trimmed = trim_history(msgs, limit)
            if trimmed:
                self.assertEqual(trimmed[0]["content"][0]["type"], "text")
                self.assertEqual(trimmed[0]["role"], "user")


def chunk(delta: dict | None = None, finish: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


class FakeTransport:
    """Simuliert eine OpenAI-kompatible API (Gemini, Groq, …): liefert vorbereitete Antworten."""

    def __init__(self, turns: list[list[dict]]) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []

    def __call__(self, payload: dict):
        self.requests.append(json.loads(json.dumps(payload)))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return iter(turn)


class FreeProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(self.tmp)
        self.settings.reflect_every = 100
        self.settings.provider = "gemini"
        self.settings.model = "gemini-flash-latest"
        self.brain = Brain(self.settings.db_path, self.settings.brain_dir)

    def jarvis(self, transport: FakeTransport) -> Jarvis:
        return Jarvis(self.brain, self.settings, backend=OpenAICompatBackend(self.settings, transport))

    def test_streamed_tool_call_loop(self):
        transport = FakeTransport([
            [
                chunk({"role": "assistant", "content": "Notiert"}),
                chunk({"content": "."}),
                chunk({"tool_calls": [{"index": 0, "id": "call_abc", "type": "function",
                                       "function": {"name": "remember", "arguments": '{"content": "Heißt '}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'Nathanael", "category": "identitaet"}'}}]}),
                chunk(finish="tool_calls"),
            ],
            [chunk({"content": "Freut mich, Nathanael."}), chunk(finish="stop")],
        ])
        events = []
        conv = self.jarvis(transport).chat(None, "Ich heiße Nathanael", emit=lambda e, d: events.append((e, d)))

        self.assertIn("Heißt Nathanael", self.brain.profile_text())
        self.assertEqual(events[-1][0], "done")
        text = "".join(d["delta"] for e, d in events if e == "text")
        self.assertEqual(text, "Notiert.Freut mich, Nathanael.")

        first, second = transport.requests
        self.assertEqual(first["model"], "gemini-flash-latest")
        self.assertTrue(first["stream"])
        self.assertEqual(first["messages"][0]["role"], "system")
        self.assertIn("JARVIS", first["messages"][0]["content"])
        tool_names = [t["function"]["name"] for t in first["tools"]]
        self.assertIn("remember", tool_names)
        self.assertIn("web_search", tool_names)
        # Zweite Anfrage: Assistent mit tool_calls, danach die passende tool-Nachricht.
        assistant, tool = second["messages"][-2:]
        self.assertEqual(assistant["tool_calls"][0]["function"]["name"], "remember")
        self.assertEqual(tool["role"], "tool")
        self.assertEqual(tool["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertIn("Heißt Nathanael", second["messages"][0]["content"])

        stored = self.brain.messages(conv)
        self.assertEqual([m["role"] for m in stored], ["user", "assistant", "user", "assistant"])

    def test_error_is_reported(self):
        transport = FakeTransport([LLMError("Kostenloses Limit erreicht", 429)])
        events = []
        self.jarvis(transport).chat(None, "Hallo", emit=lambda e, d: events.append((e, d)))
        self.assertEqual(events[-1], ("error", {"message": "Kostenloses Limit erreicht"}))

    def test_invalid_tool_json_is_retried(self):
        broken = [chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "remember", "arguments": "{kaputt"}}]}),
                  chunk(finish="tool_calls")]
        transport = FakeTransport([broken, [chunk({"content": "Ok."}), chunk(finish="stop")]])
        events = []
        self.jarvis(transport).chat(None, "Hallo", emit=lambda e, d: events.append(e))
        self.assertEqual(events[-1], "done")
        self.assertEqual(len(transport.requests), 2)

    def test_reflection_with_messy_json(self):
        fid = self.brain.add_fact("Wohnt in Hamburg", "identitaet")
        answer = ("```json\n" + json.dumps({
            "new_facts": [{"category": "denkweise", "content": "Plant gern im Voraus"}, {"content": ""}],
            "updated_facts": [{"id": str(fid), "content": "Wohnt in Berlin"}],
            "obsolete_fact_ids": [],
            "episode_summary": "Umzug besprochen.",
            "title": "Umzug",
        }) + "\n```")
        transport = FakeTransport([
            [chunk({"content": "Klingt gut."}), chunk(finish="stop")],
            [{"choices": [{"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}],
        ])
        jarvis = self.jarvis(transport)
        conv = jarvis.chat(None, "Ich bin nach Berlin gezogen")
        result = jarvis.reflect(conv)

        self.assertEqual(result["title"], "Umzug")
        self.assertEqual(len(result["new_facts"]), 1)
        profile = self.brain.profile_text()
        self.assertIn("Berlin", profile)
        self.assertIn("Plant gern im Voraus", profile)
        self.assertEqual(transport.requests[1]["response_format"], {"type": "json_object"})

    def test_message_conversion(self):
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "<kontext>…</kontext>"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                {"type": "text", "text": "Was ist das?"},
            ]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "…", "signature": "x"},
                {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a"}},
                {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "b"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Inhalt", "is_error": True},
                {"type": "tool_result", "tool_use_id": "fremd", "content": "verwaist"},
            ]},
            {"role": "assistant", "content": []},
            {"role": "user", "content": [{"type": "text", "text": "Danke"}]},
        ]
        out = to_openai_messages("SYS", history)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "tool", "user"])
        self.assertEqual(out[1]["content"][1]["image_url"]["url"], "data:image/png;base64,AAAA")
        ids = [c["id"] for c in out[2]["tool_calls"]]
        self.assertTrue(all(len(i) == 9 and i.isalnum() for i in ids))
        self.assertEqual(out[3], {"role": "tool", "tool_call_id": ids[0], "content": "FEHLER: Inhalt"})
        self.assertEqual(out[4]["tool_call_id"], ids[1])  # fehlendes Ergebnis wird ergänzt
        self.assertEqual(out[5]["content"], "Danke")

    def test_parse_json_object(self):
        self.assertEqual(parse_json_object('Hier: {"a": 1} fertig'), {"a": 1})
        self.assertIsNone(parse_json_object("kein json"))


class ConfigTests(unittest.TestCase):
    def tearDown(self):
        for name in PROVIDER_ENV:
            os.environ.pop(name, None)

    def test_provider_detection_and_defaults(self):
        self.assertEqual(Settings().provider, "gemini")
        self.assertIn("GEMINI_API_KEY", Settings().setup_problem())
        os.environ["GROQ_API_KEY"] = "gsk_test"
        s = Settings()
        self.assertEqual((s.provider, s.api_key), ("groq", "gsk_test"))
        self.assertTrue(s.base_url.startswith("https://api.groq.com"))
        self.assertIsNone(s.setup_problem())
        os.environ["JARVIS_PROVIDER"] = "ollama"
        s = Settings()
        self.assertEqual(s.provider, "ollama")
        self.assertIsNone(s.setup_problem())  # lokal, kein Schlüssel nötig
        os.environ["JARVIS_PROVIDER"] = "quatsch"
        self.assertIn("Unbekannter Anbieter", Settings().setup_problem())


class WebToolTests(unittest.TestCase):
    def test_parse_ddg_results(self):
        page = (
            '<div class="result"><a rel="nofollow" class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fseite&amp;rut=x">Beispiel <b>Seite</b></a>'
            '<a class="result__snippet" href="#">Ein <b>kurzer</b> Text &amp; mehr</a></div>'
            '<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Werbung</a></div>'
        )
        results = parse_ddg_results(page, 5)
        self.assertEqual(results, [{"title": "Beispiel Seite", "url": "https://example.org/seite",
                                    "snippet": "Ein kurzer Text & mehr"}])

    def test_html_to_text(self):
        title, text = html_to_text("<html><head><title>Hallo</title><style>x{}</style></head>"
                                   "<body><nav>Menü</nav><h1>Kopf</h1><p>Erster&nbsp;Absatz</p>"
                                   "<script>alert(1)</script><ul><li>Punkt</li></ul></body></html>")
        self.assertEqual(title, "Hallo")
        self.assertEqual(text, "Kopf\nErster\xa0Absatz\n- Punkt")


if __name__ == "__main__":
    unittest.main()
