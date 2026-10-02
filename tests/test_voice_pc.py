"""Offline-Tests für Sprachsteuerung, Jarvis-Fenster (Bildschirmwahl) und PC-Steuerung.

Ohne Mikrofon, Lautsprecher, Bildschirm oder Internet: Hardware wird simuliert.
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from test_jarvis import FakeTransport, chunk, make_settings

from jarvis.agent import Jarvis
from jarvis.brain import Brain
from jarvis.llm import OpenAICompatBackend, to_openai_messages
from jarvis.screens import Monitor, Rect, Window, choose_monitor
from jarvis.tools import ToolOutput, Toolbox, _obj
from jarvis.voice import VOICE_HINT, SentenceStream, VoiceAssistant, is_stop, speech_text

try:
    import numpy as np
except ImportError:  # numpy kommt mit requirements-voice.txt
    np = None


# ================================================================ Bildschirmwahl
def monitor(number: int, left: int, primary: bool = False) -> Monitor:
    rect = Rect(left, 0, left + 1920, 1080)
    work = Rect(left, 0, left + 1920, 1040) if primary else rect  # Taskleiste nur auf dem Hauptbildschirm
    return Monitor(number, rect, work, primary)


MAIN, SECOND = monitor(1, 0, primary=True), monitor(2, 1920)


class ChooseMonitorTests(unittest.TestCase):
    def test_avoids_fullscreen_game(self):
        game = Window("Spiel", Rect(0, 0, 1920, 1080), foreground=True)
        self.assertEqual(choose_monitor([MAIN, SECOND], [game]), SECOND)

    def test_prefers_empty_desktop(self):
        browser = Window("Browser", Rect(1912, -8, 3848, 1088), maximized=True)
        self.assertEqual(choose_monitor([MAIN, SECOND], [browser]), MAIN)
        editor = Window("Editor", Rect(100, 100, 900, 700), foreground=True)
        self.assertEqual(choose_monitor([MAIN, SECOND], [editor]), SECOND)

    def test_maximized_window_is_not_fullscreen(self):
        # Auf dem Zweitbildschirm ohne Taskleiste ragt ein maximiertes Fenster über den Rand hinaus.
        browser = Window("Browser", Rect(1912, -8, 3848, 1088), maximized=True)
        game = Window("Spiel", Rect(0, 0, 1920, 1080), foreground=True)
        self.assertEqual(choose_monitor([MAIN, SECOND], [browser, game]), SECOND)

    def test_everything_fullscreen_means_no_window(self):
        windows = [Window("Spiel", Rect(0, 0, 1920, 1080)), Window("Video", Rect(1920, 0, 3840, 1080))]
        self.assertIsNone(choose_monitor([MAIN, SECOND], windows))

    def test_preferred_monitor(self):
        self.assertEqual(choose_monitor([MAIN, SECOND], [], preferred=2), SECOND)
        video = Window("Video", Rect(1920, 0, 3840, 1080))
        self.assertIsNone(choose_monitor([MAIN, SECOND], [video], preferred=2))

    def test_ignores_own_window(self):
        hud = Window("JARVIS", Rect(0, 0, 1920, 1080))
        self.assertEqual(choose_monitor([MAIN], [hud]), MAIN)


# ================================================================ Vorlesen
class SpeechTextTests(unittest.TestCase):
    def test_speech_text(self):
        self.assertEqual(speech_text("## Plan\n- **Erstens** [hier](https://x.de) `code`"), "Plan Erstens hier code")
        self.assertEqual(speech_text("Siehe https://example.org/a"), "Siehe Link")

    def test_sentence_stream(self):
        stream = SentenceStream()
        out = []
        for delta in ["Es ist 3", ".5 Grad warm. Hier ", "der Code:\n```py\nprint(1)\n", "```\nFertig", "!"]:
            out += stream.feed(delta)
        out += stream.flush()
        self.assertEqual(out, ["Es ist 3.5 Grad warm.", "Hier der Code:", "Den Code lese ich nicht vor.", "Fertig!"])

    def test_stop_phrases(self):
        self.assertTrue(is_stop("Stopp."))
        self.assertTrue(is_stop("Danke, das war's!"))
        self.assertFalse(is_stop("Stoppe die Musik"))


# ================================================================ Sprachablauf
class FakeMic:
    def __init__(self, frames):
        self._frames = iter(frames)

    def frames(self):
        yield from self._frames

    def clear(self):
        pass


class FakeWake:
    def detected(self, frame) -> bool:
        return int(frame[0]) == 7777

    def reset(self):
        pass


class FakeSpeaker:
    def __init__(self):
        self.said: list[str] = []

    def say(self, text):
        self.said.append(text)

    def wait(self):
        pass

    def chime(self):
        pass


class FakeHud:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


@unittest.skipIf(np is None, "numpy nicht installiert (requirements-voice.txt)")
class VoiceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(self.tmp)
        self.settings.reflect_every = 100
        self.settings.provider, self.settings.model = "gemini", "gemini-flash-latest"
        self.settings.followup_seconds = 1.0
        self.brain = Brain(self.settings.db_path, self.settings.brain_dir)

    def frames(self) -> list:
        quiet = np.zeros(1280, dtype=np.int16)
        loud = np.full(1280, 3000, dtype=np.int16)
        wake = quiet.copy()
        wake[0] = 7777
        # Rauschen, "Hey Jarvis", Befehl (0,4 s), Pause (1,2 s), dann Stille ohne Nachfrage.
        return [quiet] * 5 + [wake] + [loud] * 5 + [quiet] * 15 + [quiet] * 20

    def assistant(self, transport, texts):
        jarvis = Jarvis(self.brain, self.settings, backend=OpenAICompatBackend(self.settings, transport))
        heard = list(texts)
        self.speaker, self.hud = FakeSpeaker(), FakeHud()
        return VoiceAssistant(jarvis, self.settings, FakeMic(self.frames()), FakeWake(),
                              lambda audio: heard.pop(0), self.speaker, log=lambda *_: None, hud=self.hud)

    def test_wake_word_question_and_spoken_answer(self):
        transport = FakeTransport([[
            chunk({"content": "Es ist 15 Uhr. "}), chunk({"content": "Brauchst du noch etwas?"}), chunk(finish="stop"),
        ]])
        self.assistant(transport, ["Wie spät ist es?"]).run()

        self.assertEqual(self.speaker.said, ["Es ist 15 Uhr.", "Brauchst du noch etwas?"])
        context = transport.requests[0]["messages"][-1]["content"]
        self.assertIn(VOICE_HINT, context)
        self.assertIn("Wie spät ist es?", context)
        names = [c[0] for c in self.hud.calls]
        self.assertIn("show", names)
        self.assertIn(("user", "Wie spät ist es?"), self.hud.calls)
        self.assertIn(("set_state", "idle"), self.hud.calls)
        self.assertEqual(names[-1], "hide_later")
        answer = "".join(c[1] for c in self.hud.calls if c[0] == "answer")
        self.assertIn("Es ist 15 Uhr.", answer)

    def test_stop_phrase_skips_model(self):
        transport = FakeTransport([])
        self.assistant(transport, ["Stopp"]).run()
        self.assertEqual(self.speaker.said, ["Alles klar."])
        self.assertEqual(transport.requests, [])


# ================================================================ PC-Steuerung
class FakeGui(types.ModuleType):
    KEYBOARD_KEYS = ["ctrl", "c", "v", "enter", "win", "d", "volumeup", "command"]

    def __init__(self):
        super().__init__("pyautogui")
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.calls.append((name, args, kwargs))

    def size(self):
        return (3840, 2160)

    def position(self):
        return (10, 10)


class PcToolTests(unittest.TestCase):
    def setUp(self):
        self.gui = FakeGui()
        self.clipboard = types.ModuleType("pyperclip")
        self.clipboard.copied = []
        self.clipboard.copy = self.clipboard.copied.append
        self.saved = {name: sys.modules.get(name) for name in ("pyautogui", "pyperclip")}
        sys.modules["pyautogui"], sys.modules["pyperclip"] = self.gui, self.clipboard
        tmp = Path(tempfile.mkdtemp())
        self.settings = make_settings(tmp)
        self.settings.pc_control = True
        self.box = Toolbox(Brain(self.settings.db_path, self.settings.brain_dir), self.settings)

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_tools_only_when_enabled(self):
        self.assertIn("mouse", self.box.tools)
        self.settings.pc_control = False
        plain = Toolbox(Brain(self.settings.db_path, self.settings.brain_dir), self.settings)
        self.assertNotIn("mouse", plain.tools)

    def test_click_maps_screenshot_coordinates(self):
        self.box._screen_scale, self.box._screen_offset = 3.0, (1920, 0)  # verkleinertes Bild von Monitor 2
        out, err = self.box.execute("mouse", {"action": "click", "x": 100, "y": 50})
        self.assertFalse(err, out)
        self.assertEqual(self.gui.calls[-1], ("click", (), {"x": 2220, "y": 150}))

    def test_keyboard(self):
        self.box.execute("keyboard", {"action": "type", "text": "Grüße"})
        self.assertEqual(self.clipboard.copied, ["Grüße"])  # Umlaute über die Zwischenablage
        self.assertEqual(self.gui.calls[-1][:2], ("hotkey", ("ctrl", "v")))
        self.box.execute("keyboard", {"action": "hotkey", "keys": "win + d"})
        self.assertEqual(self.gui.calls[-1][:2], ("hotkey", ("win", "d")))
        out, err = self.box.execute("keyboard", {"action": "press", "keys": "quatsch"})
        self.assertTrue(err)
        self.assertIn("Unbekannte Taste", out)

    def test_media_keys(self):
        self.box.execute("pc", {"action": "volume_up", "times": 3})
        self.assertEqual(self.gui.calls[-1], ("press", ("volumeup",), {"presses": 3}))


# ================================================================ Bilder in Werkzeug-Ergebnissen
class ToolImageTests(unittest.TestCase):
    def test_conversion_moves_tool_images_into_user_message(self):
        history = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "screenshot", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": "Bildschirm 1"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QQ=="}},
            ]}]},
        ]
        out = to_openai_messages("SYS", history)
        self.assertEqual([m["role"] for m in out], ["system", "assistant", "tool", "user"])
        self.assertEqual(out[2]["content"], "Bildschirm 1")
        self.assertEqual(out[3]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,QQ==")

    def test_agent_sends_latest_image_and_stores_placeholder(self):
        tmp = Path(tempfile.mkdtemp())
        settings = make_settings(tmp)
        settings.reflect_every = 100
        settings.provider, settings.model = "gemini", "gemini-flash-latest"
        brain = Brain(settings.db_path, settings.brain_dir)

        def call(cid):
            return [chunk({"tool_calls": [{"index": 0, "id": cid, "function": {"name": "bild", "arguments": "{}"}}]}),
                    chunk(finish="tool_calls")]

        transport = FakeTransport([call("c1"), call("c2"), [chunk({"content": "Gesehen."}), chunk(finish="stop")]])
        jarvis = Jarvis(brain, settings, backend=OpenAICompatBackend(settings, transport))
        jarvis.toolbox.add("bild", "Testbild", _obj({}, []))(lambda: ToolOutput("ein Bild", [("image/png", "AAAA")]))
        conv = jarvis.chat(None, "Schau mal")

        def images(request):
            return sum(1 for m in request["messages"] if isinstance(m["content"], list)
                       for p in m["content"] if p.get("type") == "image_url")

        self.assertEqual(images(transport.requests[1]), 1)
        self.assertEqual(images(transport.requests[2]), 1)  # nur das neueste Bild wird mitgeschickt
        stored = [b for m in brain.messages(conv) if m["role"] == "user" for b in m["content"]
                  if b.get("type") == "tool_result"]
        self.assertTrue(all(isinstance(b["content"], str) and "nicht gespeichert" in b["content"] for b in stored))


if __name__ == "__main__":
    unittest.main()
