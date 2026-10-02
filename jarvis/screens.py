"""Bildschirme und Fenster erkennen.

Damit erscheint das Jarvis-Fenster auf dem freien Monitor und nie vor einer Anwendung im
Vollbild (z. B. einem Spiel), und die PC-Werkzeuge wissen, wo welcher Bildschirm liegt.
Die Fenster-Erkennung gibt es unter Windows; auf anderen Systemen gilt nur der Hauptbildschirm.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

IGNORED_WINDOW_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
HUD_TITLE = "JARVIS"


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        return self.width * self.height

    def overlap(self, other: Rect) -> int:
        w = min(self.right, other.right) - max(self.left, other.left)
        h = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(0, w) * max(0, h)

    def covers(self, other: Rect) -> bool:
        return (self.left <= other.left and self.top <= other.top
                and self.right >= other.right and self.bottom >= other.bottom)


@dataclass(frozen=True)
class Monitor:
    number: int  # 1 = Hauptbildschirm
    rect: Rect
    work: Rect  # ohne Taskleiste
    primary: bool


@dataclass(frozen=True)
class Window:
    title: str
    rect: Rect
    maximized: bool = False
    foreground: bool = False


def is_fullscreen(window: Window, monitor: Monitor) -> bool:
    """Vollbild = bedeckt den ganzen Monitor inklusive Taskleiste und ist kein normales maximiertes Fenster."""
    return not window.maximized and window.rect.covers(monitor.rect)


def choose_monitor(monitors: list[Monitor], windows: list[Window], preferred: int | None = None) -> Monitor | None:
    """Wählt den Bildschirm fürs Jarvis-Fenster.

    Bildschirme mit Vollbild-Anwendung scheiden aus. Von den übrigen gewinnt der, auf dem am
    wenigsten Fenster liegen (am besten nur der Desktop); bei Gleichstand der, auf dem du gerade
    nicht arbeitest. ``preferred`` erzwingt einen Bildschirm – außer dort läuft etwas im Vollbild.
    Rückgabe ``None``: alle Bildschirme sind belegt, das Fenster bleibt zu.
    """
    windows = [w for w in windows if w.title != HUD_TITLE]
    free = [m for m in monitors if not any(is_fullscreen(w, m) for w in windows)]
    if preferred is not None:
        return next((m for m in free if m.number == preferred), None)
    if not free:
        return None

    def score(monitor: Monitor) -> tuple[float, bool, int]:
        covered = sum(w.rect.overlap(monitor.work) for w in windows)
        coverage = min(1.0, covered / max(1, monitor.work.area))
        has_foreground = any(w.foreground and w.rect.overlap(monitor.rect) > 0 for w in windows)
        return round(coverage, 2), has_foreground, monitor.number

    return min(free, key=score)


# ================================================================ Windows
def make_dpi_aware() -> None:
    """Echte Pixel statt hochskalierter Koordinaten (wichtig bei mehreren Monitoren mit Skalierung)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _win_monitors() -> list[Monitor]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    found: list[tuple[Rect, Rect, bool]] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def callback(hmonitor, hdc, rect_ptr, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r, w = info.rcMonitor, info.rcWork
            found.append((Rect(r.left, r.top, r.right, r.bottom), Rect(w.left, w.top, w.right, w.bottom),
                          bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    found.sort(key=lambda m: (not m[2], m[0].left, m[0].top))
    return [Monitor(i, rect, work, primary) for i, (rect, work, primary) in enumerate(found, 1)]


def _win_windows() -> list[Window]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
    foreground = user32.GetForegroundWindow()
    result: list[Window] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = -20, 0x80, 14

    def callback(hwnd, data):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value in IGNORED_WINDOW_CLASSES:
            return True
        if dwmapi is not None:
            cloaked = wintypes.DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True  # unsichtbare UWP-Fenster, andere virtuelle Desktops
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if not title.value and hwnd != foreground:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        result.append(Window(title.value, Rect(r.left, r.top, r.right, r.bottom),
                             maximized=bool(user32.IsZoomed(hwnd)), foreground=hwnd == foreground))
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return result


def monitors(fallback_size: tuple[int, int] | None = None) -> list[Monitor]:
    """Alle Bildschirme (Windows) bzw. nur der Hauptbildschirm (andere Systeme)."""
    if sys.platform == "win32":
        try:
            found = _win_monitors()
            if found:
                return found
        except (AttributeError, OSError):
            pass
    width, height = fallback_size or (1920, 1080)
    rect = Rect(0, 0, width, height)
    return [Monitor(1, rect, rect, True)]


def windows() -> list[Window]:
    if sys.platform == "win32":
        try:
            return _win_windows()
        except (AttributeError, OSError):
            return []
    return []
