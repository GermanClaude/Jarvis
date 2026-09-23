"""Offline-Tests: Gedächtnis, Tools und die Agent-Schleife mit einer simulierten API."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from anthropic.types.beta import BetaMessage

from jarvis.agent import Jarvis, trim_history
from jarvis.brain import Brain
from jarvis.config import Settings
from jarvis.tools import Toolbox, validate


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


def message(content: list[dict], stop_reason: str) -> BetaMessage:
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


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(self.tmp)
        self.settings.reflect_every = 100
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


if __name__ == "__main__":
    unittest.main()
