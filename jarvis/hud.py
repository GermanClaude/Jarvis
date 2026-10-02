"""Das Jarvis-Fenster: rahmenlos, immer im Vordergrund, im Iron-Man-Look.

Es erscheint bei "Hey Jarvis" auf dem freien Bildschirm (nie vor einer Vollbild-Anwendung),
zeigt, ob Jarvis zuhört, nachdenkt, arbeitet oder spricht, und das ganze Gespräch als Text.
Das Fenster nimmt nie den Fokus – ein laufendes Spiel wird also nicht minimiert.

Tkinter muss im Haupt-Thread laufen; die Sprachsteuerung ruft die Methoden aus ihrem eigenen
Thread auf, deshalb läuft alles über eine Queue.
"""

from __future__ import annotations

import math
import queue
import signal
import sys
import time
from typing import Callable

from . import screens

WIDTH, HEIGHT = 520, 660
OFFSCREEN = "+-32000+-32000"
BG = "#050b14"
PANEL = "#0a1424"
CYAN = "#29d3ff"
CYAN_DIM = "#0f5c73"
CYAN_DARK = "#0a3344"
TEXT = "#e6f7ff"
MUTED = "#7fa7b8"
WARN = "#ffb347"

STATES = {
    "idle": ("Bereit", CYAN_DIM),
    "listening": ("Ich höre zu …", CYAN),
    "transcribing": ("Ich verstehe …", CYAN),
    "thinking": ("Ich denke nach …", CYAN),
    "acting": ("Ich arbeite …", WARN),
    "speaking": ("Ich spreche …", CYAN),
}


class NullHud:
    """Ohne Fenster (JARVIS_WINDOW=off oder kein Tkinter verfügbar)."""

    def show(self) -> None: ...
    def hide_later(self, seconds: float) -> None: ...
    def clear(self) -> None: ...
    def set_state(self, state: str, detail: str = "") -> None: ...
    def set_level(self, level: float) -> None: ...
    def user(self, text: str) -> None: ...
    def answer(self, delta: str) -> None: ...

    def run(self, worker: Callable[[], None]) -> None:
        worker()


class Hud(NullHud):
    def __init__(self, mode: str = "auto", log: Callable[[str], None] = print) -> None:
        import tkinter as tk

        screens.make_dpi_aware()
        self.tk = tk
        self.mode = mode
        self.log = log
        self.queue: queue.Queue = queue.Queue()
        self.state, self.detail = "idle", ""
        self.level = 0.0
        self.phase = 0.0
        self.visible = False
        self.hide_at: float | None = None
        self.closed_by_user = False

        sans = "Segoe UI" if sys.platform == "win32" else "Helvetica"
        self.root = root = tk.Tk()
        root.title(screens.HUD_TITLE)
        root.overrideredirect(True)
        root.configure(bg=BG)
        root.geometry(f"{WIDTH}x{HEIGHT}{OFFSCREEN}")
        root.attributes("-topmost", True)
        self._alpha(0.0)

        self.canvas = canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self._frame()
        canvas.create_text(28, 30, text="J.A.R.V.I.S.", anchor="w", fill=CYAN, font=(sans, 15, "bold"))
        canvas.create_text(28, 52, text="Just A Rather Very Intelligent System", anchor="w", fill=MUTED,
                           font=(sans, 8))
        close = canvas.create_text(WIDTH - 26, 30, text="✕", fill=MUTED, font=(sans, 13))
        canvas.tag_bind(close, "<Button-1>", lambda e: self._close())
        self.status = canvas.create_text(WIDTH // 2, 278, text="", fill=CYAN, font=(sans, 13, "bold"))
        self.reactor_items: list[int] = []

        self.text = tk.Text(root, bg=PANEL, fg=TEXT, bd=0, highlightthickness=1, highlightbackground=CYAN_DARK,
                            wrap="word", font=(sans, 11), padx=14, pady=12, cursor="arrow")
        self.text.tag_configure("you", foreground=MUTED, font=(sans, 10, "italic"), spacing3=6)
        self.text.tag_configure("jarvis", foreground=TEXT, spacing1=2, spacing3=4)
        self.text.configure(state="disabled")
        canvas.create_window(24, 306, anchor="nw", window=self.text, width=WIDTH - 48, height=HEIGHT - 330)

        # Fenster mit der Maus verschieben, Doppelklick schließt.
        canvas.bind("<ButtonPress-1>", self._drag_start)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<Double-Button-1>", lambda e: self._close())
        root.bind("<Escape>", lambda e: self._close())

        root.update_idletasks()
        self._no_activate()
        root.after(33, self._tick)

    # ------------------------------------------------------------ Thread-sichere Schnittstelle
    def show(self) -> None:
        self.queue.put(("show", ()))

    def hide_later(self, seconds: float) -> None:
        self.queue.put(("hide_later", (seconds,)))

    def clear(self) -> None:
        self.queue.put(("clear", ()))

    def set_state(self, state: str, detail: str = "") -> None:
        self.queue.put(("state", (state, detail)))

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))  # nur ein float – ohne Queue

    def user(self, text: str) -> None:
        self.queue.put(("user", (text,)))

    def answer(self, delta: str) -> None:
        self.queue.put(("answer", (delta,)))

    def run(self, worker: Callable[[], None]) -> None:
        """Startet die Sprachsteuerung in einem Thread und das Fenster im Haupt-Thread."""
        import threading

        errors: list[BaseException] = []

        def target() -> None:
            try:
                worker()
            except BaseException as exc:  # noqa: BLE001 – an den Haupt-Thread weiterreichen
                errors.append(exc)
            finally:
                self.queue.put(("quit", ()))

        threading.Thread(target=target, daemon=True).start()
        # Strg+C beendet sauber – Tkinter würde den KeyboardInterrupt sonst im Fenster-Takt verschlucken.
        interrupted: list[bool] = []
        signal.signal(signal.SIGINT, lambda *_: (interrupted.append(True), self.queue.put(("quit", ()))))
        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except self.tk.TclError:
                pass
        if interrupted:
            raise KeyboardInterrupt
        if errors:
            raise errors[0]

    # ------------------------------------------------------------ Darstellung
    def _alpha(self, value: float) -> None:
        try:
            self.root.attributes("-alpha", value)
        except self.tk.TclError:
            pass

    def _no_activate(self) -> None:
        """Windows: Fenster nimmt nie den Fokus und erscheint nicht in der Taskleiste."""
        if sys.platform != "win32":
            return
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x80, 0x08000000, 0x8
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)

    def _frame(self) -> None:
        c = self.canvas
        for inset, color in ((1, CYAN_DARK), (4, CYAN_DIM)):
            c.create_rectangle(inset, inset, WIDTH - inset, HEIGHT - inset, outline=color)
        for x, y, dx, dy in ((8, 8, 1, 1), (WIDTH - 8, 8, -1, 1), (8, HEIGHT - 8, 1, -1),
                             (WIDTH - 8, HEIGHT - 8, -1, -1)):
            c.create_line(x, y, x + 34 * dx, y, fill=CYAN, width=2)
            c.create_line(x, y, x, y + 34 * dy, fill=CYAN, width=2)
        c.create_line(28, 68, WIDTH - 28, 68, fill=CYAN_DARK)

    def _draw_reactor(self) -> None:
        c = self.canvas
        for item in self.reactor_items:
            c.delete(item)
        items = self.reactor_items = []
        cx, cy = WIDTH // 2, 168
        label, color = STATES.get(self.state, STATES["idle"])
        active = self.state != "idle"
        speed = {"thinking": 4.0, "acting": 5.0, "transcribing": 3.0}.get(self.state, 1.5 if active else 0.5)
        self.phase = (self.phase + speed) % 360
        pulse = 0.5 + 0.5 * math.sin(time.time() * (6 if self.state == "speaking" else 2))
        energy = self.level if self.state == "listening" else (pulse * 0.6 if active else 0.1)

        for r, width, col in ((86, 1, CYAN_DARK), (74, 2, CYAN_DIM), (40, 1, CYAN_DARK)):
            items.append(c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=col, width=width))
        for i in range(3):  # drei rotierende Bögen außen
            start = self.phase + i * 120
            items.append(c.create_arc(cx - 80, cy - 80, cx + 80, cy + 80, start=start, extent=70,
                                      style="arc", outline=color, width=3))
        for i in range(2):  # gegenläufige Bögen innen
            start = -self.phase * 1.6 + i * 180
            items.append(c.create_arc(cx - 56, cy - 56, cx + 56, cy + 56, start=start, extent=110,
                                      style="arc", outline=color if active else CYAN_DIM, width=2))
        for i in range(12):  # Segmente wie beim Arc-Reactor
            a = math.radians(i * 30 + self.phase / 3)
            x1, y1 = cx + 62 * math.cos(a), cy + 62 * math.sin(a)
            x2, y2 = cx + 70 * math.cos(a), cy + 70 * math.sin(a)
            items.append(c.create_line(x1, y1, x2, y2, fill=CYAN_DIM, width=2))
        core = 16 + 14 * energy
        items.append(c.create_oval(cx - core - 8, cy - core - 8, cx + core + 8, cy + core + 8,
                                   outline=CYAN_DARK, width=6))
        items.append(c.create_oval(cx - core, cy - core, cx + core, cy + core, fill=color, outline=""))
        text = f"{label} {self.detail}".strip() if self.state == "acting" else label
        c.itemconfigure(self.status, text=text, fill=color)

    def _append(self, text: str, tag: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    # ------------------------------------------------------------ Ablauf im Haupt-Thread
    def _tick(self) -> None:
        while True:
            try:
                action, args = self.queue.get_nowait()
            except queue.Empty:
                break
            if action == "quit":
                self.root.quit()
                return
            try:
                getattr(self, f"_do_{action}")(*args)
            except Exception as exc:  # noqa: BLE001 – das Fenster soll weiterlaufen
                self.log(f"[Jarvis-Fenster] {action}: {exc}")
        try:
            if self.hide_at is not None and time.time() >= self.hide_at:
                self._hide()
            if self.visible:
                self._draw_reactor()
        finally:
            self.root.after(33, self._tick)

    def _do_show(self) -> None:
        self.hide_at = None
        self.closed_by_user = False
        if self.mode == "off":
            return
        preferred = int(self.mode) if self.mode.isdigit() else None
        size = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        monitor = screens.choose_monitor(screens.monitors(size), screens.windows(), preferred)
        if monitor is None:
            self.log("(Vollbild-Anwendung erkannt – das Jarvis-Fenster bleibt zu.)")
            self._hide()
            return
        work = monitor.work
        x = work.left + (work.width - WIDTH) // 2
        y = work.top + (work.height - HEIGHT) // 2
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self.root.attributes("-topmost", True)
        self._alpha(0.94)
        self.visible = True

    def _hide(self) -> None:
        self.hide_at = None
        self.visible = False
        self._alpha(0.0)
        self.root.geometry(f"{WIDTH}x{HEIGHT}{OFFSCREEN}")

    def _close(self) -> None:
        self.closed_by_user = True
        self._hide()

    def _do_hide_later(self, seconds: float) -> None:
        if seconds > 0 and self.visible:
            self.hide_at = time.time() + seconds

    def _do_clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _do_state(self, state: str, detail: str) -> None:
        self.state, self.detail = state, detail
        if self.visible:
            self.hide_at = None

    def _do_user(self, text: str) -> None:
        prefix = "\n" if self.text.get("1.0", "end").strip() else ""
        self._append(f"{prefix}Du: {text}\n", "you")

    def _do_answer(self, delta: str) -> None:
        self._append(delta, "jarvis")

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x, event.y)

    def _drag(self, event) -> None:
        dx, dy = event.x - self._drag_origin[0], event.y - self._drag_origin[1]
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")


def create_hud(mode: str, log: Callable[[str], None] = print) -> NullHud:
    """Jarvis-Fenster – oder ein stilles Ersatzobjekt, wenn es abgeschaltet oder nicht möglich ist."""
    if mode == "off":
        return NullHud()
    try:
        return Hud(mode, log)
    except Exception as exc:  # noqa: BLE001 – z. B. kein Tkinter oder kein Bildschirm
        log(f"(Jarvis-Fenster nicht verfügbar: {exc} – es geht ohne Fenster weiter.)")
        return NullHud()
