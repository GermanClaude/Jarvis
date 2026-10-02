"""Sprachsteuerung für den PC: "Hey Jarvis" → Befehl sprechen → Jarvis antwortet laut.

Bausteine (alle kostenlos, Zusatzpakete aus requirements-voice.txt):

- Wake-Word: openWakeWord mit dem fertigen Modell "hey_jarvis" (offline)
- Spracherkennung: faster-whisper (offline, Modell wird beim ersten Start geladen)
- Sprachausgabe: Microsoft-Neuralstimmen über edge-tts (online) oder die Systemstimme (pyttsx3)
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable, Iterator

from .hud import NullHud, create_hud

SAMPLE_RATE = 16_000
FRAME = 1280  # 80 ms – Blockgröße, die openWakeWord erwartet
IDLE_NEW_CONVERSATION = 10 * 60  # nach 10 Minuten Pause beginnt ein neues Gespräch

VOICE_HINT = (
    "Der Nutzer spricht mit dir per Sprache, deine Antwort wird vorgelesen und im Jarvis-Fenster angezeigt. "
    "Führe ein ganz normales, natürliches Gespräch – genauso ausführlich und durchdacht wie im Chat, so "
    "lang wie die Frage es braucht (Smalltalk kurz, Erklärungen, Ideen und Ratschläge gern ausführlich). "
    "Formuliere so, dass es sich gut anhört: ganze Sätze statt Stichpunkte, keine Tabellen, Emojis oder "
    "Links. Code schreibst du in Dateien oder zeigst ihn, liest ihn aber nicht vor. Wenn du am PC etwas "
    "tust, sag kurz und natürlich, was du machst. Die Spracherkennung kann sich verhören – wenn etwas "
    "keinen Sinn ergibt, frag nach."
)
STOP_PHRASES = {"stopp", "stop", "abbrechen", "danke das wars", "danke das war's", "das wars", "das war's",
                "nichts", "vergiss es", "schon gut"}


# ================================================================ Text fürs Vorlesen
def speech_text(text: str) -> str:
    """Macht aus Markdown vorlesbaren Text."""
    text = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "Link", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|[-*+>]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~|#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceStream:
    """Sammelt gestreamten Text und gibt fertige, vorlesbare Sätze zurück.

    Codeblöcke werden nicht vorgelesen, sondern einmal kurz erwähnt.
    """

    def __init__(self) -> None:
        self.buffer = ""
        self.in_code = False

    def feed(self, delta: str) -> list[str]:
        self.buffer += delta
        out: list[str] = []
        while True:
            if self.in_code:
                end = self.buffer.find("```")
                if end < 0:
                    return out
                self.buffer = self.buffer[end + 3:]
                self.in_code = False
                continue
            fence = self.buffer.find("```")
            match = re.search(r"[.!?…:;](\s|$)|\n", self.buffer)
            if fence >= 0 and (match is None or fence < match.start()):
                out += self._clean([self.buffer[:fence]])
                out.append("Den Code lese ich nicht vor.")
                self.buffer = self.buffer[fence + 3:]
                self.in_code = True
                continue
            if match is None or match.group(1) == "":
                return out  # Satzzeichen am Pufferende – es könnte noch weitergehen (z. B. "3.5")
            out += self._clean([self.buffer[:match.end()]])
            self.buffer = self.buffer[match.end():]

    def flush(self) -> list[str]:
        rest, self.buffer = ("" if self.in_code else self.buffer), ""
        self.in_code = False
        return self._clean([rest])

    @staticmethod
    def _clean(parts: list[str]) -> list[str]:
        return [t for t in (speech_text(p) for p in parts) if re.search(r"\w", t)]


def is_stop(text: str) -> bool:
    return re.sub(r"[^\wäöüß' ]", "", text.lower()).strip() in STOP_PHRASES


# ================================================================ Erkennung von Sprechpausen
def rms(frame) -> float:
    import numpy as np

    return float(np.sqrt(np.mean(np.square(frame.astype(np.float32))))) if len(frame) else 0.0


class Endpointer:
    """Erkennt anhand der Lautstärke, wann ein gesprochener Befehl anfängt und endet."""

    def __init__(self, noise_floor: float, start_timeout: float = 5.0, silence: float = 1.0,
                 max_length: float = 20.0) -> None:
        self.threshold = max(noise_floor * 3.0, 300.0)
        self.frame_seconds = FRAME / SAMPLE_RATE
        self.start_timeout = start_timeout
        self.silence = silence
        self.max_length = max_length
        self.elapsed = 0.0
        self.quiet = 0.0
        self.started = False

    def feed(self, frame) -> str:
        """Rückgabe: 'waiting', 'speaking', 'done' oder 'timeout'."""
        self.elapsed += self.frame_seconds
        loud = rms(frame) >= self.threshold
        if not self.started:
            if loud:
                self.started = True
                return "speaking"
            return "timeout" if self.elapsed >= self.start_timeout else "waiting"
        self.quiet = 0.0 if loud else self.quiet + self.frame_seconds
        if self.quiet >= self.silence or self.elapsed >= self.max_length:
            return "done"
        return "speaking"


# ================================================================ Hardware-Bausteine
def _require(module: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise RuntimeError(
            f"Für die Sprachsteuerung fehlt '{module}'. Installieren mit:\n"
            "  pip install -r requirements-voice.txt"
        ) from exc
    except OSError as exc:  # z. B. PortAudio fehlt unter Linux
        raise RuntimeError(f"'{module}' lässt sich nicht laden: {exc}\n"
                           "Unter Linux: sudo apt install libportaudio2") from exc


class Microphone:
    def __init__(self, device: int | str | None = None) -> None:
        sd = _require("sounddevice")
        self.queue: queue.Queue = queue.Queue()
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     device=device, callback=self._callback)
        self.stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        self.queue.put(indata[:, 0].copy())

    def frames(self) -> Iterator:
        while True:
            yield self.queue.get()

    def clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


class WakeWord:
    def __init__(self, name: str, threshold: float) -> None:
        _require("openwakeword")
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        if not name.endswith((".onnx", ".tflite")):
            download_models([name])  # lädt nur, was noch fehlt
        framework = "tflite" if name.endswith(".tflite") else "onnx"
        self.model = Model(wakeword_models=[name], inference_framework=framework)
        self.threshold = threshold

    def detected(self, frame) -> bool:
        scores = self.model.predict(frame)
        return max(scores.values(), default=0.0) >= self.threshold

    def reset(self) -> None:
        self.model.reset()


class Transcriber:
    def __init__(self, model_size: str, language: str) -> None:
        _require("faster_whisper")
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.language = language

    def __call__(self, audio) -> str:
        import numpy as np

        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(samples, language=self.language, vad_filter=True,
                                            initial_prompt="Hey Jarvis.")
        return " ".join(s.text.strip() for s in segments).strip()


class Speaker:
    """Liest Sätze nacheinander in einem eigenen Thread vor (Jarvis denkt derweil weiter)."""

    def __init__(self, mode: str, voice: str, log: Callable[[str], None] = print) -> None:
        self.mode = mode
        self.voice = voice
        self.log = log
        self.queue: queue.Queue = queue.Queue()
        self._engine = None
        if mode != "off":
            threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text: str) -> None:
        if self.mode != "off" and text.strip():
            self.queue.put(text)

    def wait(self) -> None:
        if self.mode != "off":
            self.queue.join()

    def chime(self) -> None:
        """Kurzer Ton: "Ich höre zu"."""
        try:
            import numpy as np
            import sounddevice as sd

            t = np.linspace(0, 0.15, int(24_000 * 0.15), endpoint=False)
            tone = 0.25 * np.sin(2 * np.pi * 880 * t) * np.linspace(1, 0, t.size)
            sd.play(tone.astype(np.float32), 24_000, blocking=True)
        except Exception:  # noqa: BLE001 – der Ton ist nur Kosmetik
            pass

    def _worker(self) -> None:
        while True:
            text = self.queue.get()
            try:
                self._speak(text)
            except Exception as exc:  # noqa: BLE001
                self.log(f"[Sprachausgabe] {exc}")
            finally:
                self.queue.task_done()

    def _speak(self, text: str) -> None:
        if self.mode == "edge":
            try:
                self._speak_edge(text)
                return
            except Exception as exc:  # noqa: BLE001 – offline oder Dienst nicht erreichbar
                self.log(f"[Sprachausgabe] Online-Stimme nicht verfügbar ({exc}) – nutze die Systemstimme.")
                self.mode = "system"
        self._speak_system(text)

    def _speak_edge(self, text: str) -> None:
        import edge_tts
        import miniaudio
        import numpy as np
        import sounddevice as sd

        result: dict = {}

        def synthesize() -> None:
            try:
                communicate = edge_tts.Communicate(text, self.voice, connect_timeout=8, receive_timeout=20)
                result["data"] = b"".join(c["data"] for c in communicate.stream_sync() if c["type"] == "audio")
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        worker = threading.Thread(target=synthesize, daemon=True)
        worker.start()
        worker.join(30)
        if "error" in result or not result.get("data"):
            raise RuntimeError(result.get("error", "Zeitüberschreitung"))
        sound = miniaudio.decode(result["data"], nchannels=1, sample_rate=24_000)
        sd.play(np.frombuffer(sound.samples, dtype=np.int16), 24_000, blocking=True)

    def _speak_system(self, text: str) -> None:
        if self._engine is None:
            pyttsx3 = _require("pyttsx3")
            try:  # Windows-Sprachausgabe (COM) im eigenen Thread initialisieren
                import comtypes

                comtypes.CoInitialize()
            except Exception:  # noqa: BLE001 – nicht Windows oder schon initialisiert
                pass
            self._engine = pyttsx3.init()
            lang = self.voice[:2].lower()
            for voice in self._engine.getProperty("voices"):
                langs = " ".join(str(v) for v in getattr(voice, "languages", []) or [])
                if lang in f"{voice.id} {voice.name} {langs}".lower() or (lang == "de" and "german" in voice.name.lower()):
                    self._engine.setProperty("voice", voice.id)
                    break
        self._engine.say(text)
        self._engine.runAndWait()


# ================================================================ Ablauf
class VoiceAssistant:
    def __init__(self, jarvis, settings, mic, wake, transcribe, speaker,
                 log: Callable[[str], None] = print, push_to_talk: bool = False,
                 wait_for_enter: Callable[[], None] = input, hud: NullHud | None = None) -> None:
        self.hud = hud or NullHud()
        self.jarvis = jarvis
        self.settings = settings
        self.mic = mic
        self.wake = wake
        self.transcribe = transcribe
        self.speaker = speaker
        self.log = log
        self.push_to_talk = push_to_talk
        self.wait_for_enter = wait_for_enter
        self.noise_floor = 200.0
        self.conv_id: int | None = None
        self.last_activity = 0.0

    def run(self) -> None:
        if self.push_to_talk:
            self.log("Bereit. Enter drücken und sprechen (Strg+C beendet).")
            while True:
                self.wait_for_enter()
                self.converse()
        self.log("Bereit. Sag „Hey Jarvis“ (Strg+C beendet).")
        for frame in self.mic.frames():
            level = rms(frame)
            if level < self.noise_floor * 2:  # Grundrauschen langsam mitlernen
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
            if self.wake.detected(frame):
                self.converse()
                self.log("Bereit. Sag „Hey Jarvis“.")

    def converse(self) -> None:
        """Ein Gespräch: Befehl plus Nachfragen ohne erneutes Wake-Word."""
        self.hud.clear()
        self.hud.show()
        self.hud.set_state("listening")
        self.speaker.chime()
        self.mic.clear()
        audio = self.listen(start_timeout=5.0)
        while audio is not None:
            self.log("… verstehe …")
            self.hud.set_state("transcribing")
            text = self.transcribe(audio)
            if not text:
                break
            self.log(f"Du: {text}")
            self.hud.user(text)
            if is_stop(text):
                self.speaker.say("Alles klar.")
                self.speaker.wait()
                break
            self.answer(text)
            if self.settings.followup_seconds <= 0:
                break
            self.hud.set_state("listening")
            audio = self.listen(start_timeout=self.settings.followup_seconds)
        self.hud.set_state("idle")
        self.hud.hide_later(self.settings.window_hide_seconds)
        if self.wake is not None:
            self.wake.reset()
        self.mic.clear()

    def listen(self, start_timeout: float):
        import numpy as np

        endpointer = Endpointer(self.noise_floor, start_timeout=start_timeout)
        chunks = []
        for frame in self.mic.frames():
            state = endpointer.feed(frame)
            self.hud.set_level(rms(frame) / (endpointer.threshold * 4))
            if state == "timeout":
                return None
            if state != "waiting":
                chunks.append(frame)
            if state == "done":
                return np.concatenate(chunks)
        return None

    def answer(self, text: str) -> None:
        if time.time() - self.last_activity > IDLE_NEW_CONVERSATION:
            self.conv_id = None
        sentences = SentenceStream()
        spoken: list[str] = []
        self.hud.set_state("thinking")

        def say(sentence: str) -> None:
            spoken.append(sentence)
            self.speaker.say(sentence)
            self.hud.set_state("speaking")

        def emit(event: str, data: dict) -> None:
            if event == "text":
                self.hud.answer(data["delta"])
                for sentence in sentences.feed(data["delta"]):
                    say(sentence)
            elif event == "tool":
                self.log(f"  ⚙ {data['name']} …")
                self.hud.set_state("acting", data["name"])
            elif event == "tool_result":
                self.hud.set_state("thinking")
            elif event == "error":
                self.hud.answer(f"\n⚠ {data['message']}\n")
                say(data["message"])

        self.conv_id = self.jarvis.chat(self.conv_id, text, emit=emit, hint=VOICE_HINT)
        for sentence in sentences.flush():
            say(sentence)
        self.hud.answer("\n")
        self.log("JARVIS: " + " ".join(spoken))
        self.speaker.wait()
        self.mic.clear()  # nicht die eigene Stimme als nächsten Befehl verstehen
        self.last_activity = time.time()


def list_devices() -> str:
    sd = _require("sounddevice")
    return str(sd.query_devices())


def run_voice(jarvis, settings, device=None, push_to_talk: bool = False) -> None:
    log = print
    log("Lade Spracherkennung (beim ersten Start wird das Modell heruntergeladen) …")
    transcribe = Transcriber(settings.stt_model, settings.language)
    wake = None if push_to_talk else WakeWord(settings.wake_word, settings.wake_threshold)
    speaker = Speaker(settings.tts, settings.tts_voice, log)
    hud = create_hud(settings.window, log)

    def worker() -> None:
        mic = Microphone(device)
        try:
            VoiceAssistant(jarvis, settings, mic, wake, transcribe, speaker, log, push_to_talk, hud=hud).run()
        finally:
            mic.close()

    hud.run(worker)
