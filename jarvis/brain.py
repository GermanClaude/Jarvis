"""Das Second Brain: Langzeitgedächtnis, Notizen, Aufgaben und Gesprächsverlauf.

- Fakten über dich (Vorlieben, Ziele, Denkweise, Menschen, Projekte) in SQLite
- Notizen als normale Markdown-Dateien in ``brain/`` (auch mit Obsidian & Co. nutzbar)
- Episoden: kurze Zusammenfassungen vergangener Gespräche
- Aufgaben/Erinnerungen
- Gesprächsverläufe inkl. aller Tool-Aufrufe
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

FACT_CATEGORIES = [
    "identitaet",      # Name, Wohnort, Beruf, Alter ...
    "vorliebe",        # was du magst / nicht magst
    "ziel",            # kurz- und langfristige Ziele
    "projekt",         # woran du arbeitest
    "person",          # Menschen in deinem Leben
    "gewohnheit",      # Routinen, Tagesablauf
    "denkweise",       # wie du Probleme angehst, Werte, Prinzipien
    "kommunikation",   # wie du angesprochen werden willst, Stil
    "wissen",          # Fachwissen, Fähigkeiten
    "sonstiges",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER,
    summary TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    due TEXT,
    notes TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    completed REAL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Neues Gespräch',
    created REAL NOT NULL,
    updated REAL NOT NULL,
    reflected_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""

_WORD = re.compile(r"[\wäöüß]{3,}", re.IGNORECASE)


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(w.lower() for w in _WORD.findall(query)))


def _score(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(low.count(t) for t in terms)


def slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip()
    slug = re.sub(r"\s+", " ", slug)
    return slug[:100] or "Notiz"


@dataclass
class SearchHit:
    kind: str
    ref: str
    text: str
    score: int


class Brain:
    def __init__(self, db_path: Path, notes_dir: Path) -> None:
        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # ---------------------------------------------------------------- Fakten
    def add_fact(self, content: str, category: str = "sonstiges") -> int:
        category = category if category in FACT_CATEGORIES else "sonstiges"
        content = content.strip()
        existing = self._all("SELECT id FROM facts WHERE lower(content) = lower(?)", (content,))
        if existing:
            return existing[0]["id"]
        now = time.time()
        cur = self._exec(
            "INSERT INTO facts (category, content, created, updated) VALUES (?, ?, ?, ?)",
            (category, content, now, now),
        )
        return int(cur.lastrowid)

    def update_fact(self, fact_id: int, content: str, category: str | None = None) -> bool:
        if category and category in FACT_CATEGORIES:
            cur = self._exec(
                "UPDATE facts SET content = ?, category = ?, updated = ? WHERE id = ?",
                (content.strip(), category, time.time(), fact_id),
            )
        else:
            cur = self._exec(
                "UPDATE facts SET content = ?, updated = ? WHERE id = ?",
                (content.strip(), time.time(), fact_id),
            )
        return cur.rowcount > 0

    def delete_fact(self, fact_id: int) -> bool:
        return self._exec("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    def facts(self) -> list[dict]:
        return self._all("SELECT * FROM facts ORDER BY category, updated DESC")

    def profile_text(self, limit: int = 300) -> str:
        """Alles, was Jarvis über dich weiß – gruppiert, für den System-Prompt."""
        rows = self._all("SELECT * FROM facts ORDER BY updated DESC LIMIT ?", (limit,))
        if not rows:
            return "(Noch nichts gespeichert – lerne den Nutzer kennen.)"
        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["category"], []).append(f"- [#{r['id']}] {r['content']}")
        parts = []
        for cat in FACT_CATEGORIES:
            if cat in grouped:
                parts.append(f"## {cat}\n" + "\n".join(grouped[cat]))
        return "\n\n".join(parts)

    # -------------------------------------------------------------- Episoden
    def add_episode(self, summary: str, conversation_id: int | None = None) -> int:
        cur = self._exec(
            "INSERT INTO episodes (conversation_id, summary, created) VALUES (?, ?, ?)",
            (conversation_id, summary.strip(), time.time()),
        )
        return int(cur.lastrowid)

    def recent_episodes(self, limit: int = 5) -> list[dict]:
        return self._all("SELECT * FROM episodes ORDER BY created DESC LIMIT ?", (limit,))

    # --------------------------------------------------------------- Notizen
    def note_path(self, title: str) -> Path:
        name = title[:-3] if title.lower().endswith(".md") else title
        return self.notes_dir / f"{slugify(name)}.md"

    def save_note(self, title: str, content: str, mode: str = "overwrite") -> Path:
        path = self.note_path(title)
        if mode == "append" and path.exists():
            stamp = time.strftime("%Y-%m-%d %H:%M")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n\n<!-- {stamp} -->\n{content.strip()}\n")
        elif mode == "create" and path.exists():
            raise FileExistsError(f"Notiz '{path.stem}' existiert bereits.")
        else:
            body = content if content.lstrip().startswith("#") else f"# {title}\n\n{content}"
            path.write_text(body.rstrip() + "\n", encoding="utf-8")
        return path

    def read_note(self, title: str) -> str:
        path = self.note_path(title)
        if not path.exists():
            # unscharfe Suche nach dem Dateinamen
            matches = [p for p in self.notes_dir.rglob("*.md") if title.lower() in p.stem.lower()]
            if not matches:
                raise FileNotFoundError(f"Keine Notiz '{title}' gefunden.")
            path = matches[0]
        return path.read_text(encoding="utf-8")

    def delete_note(self, title: str) -> bool:
        path = self.note_path(title)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_notes(self) -> list[dict]:
        notes = []
        for p in sorted(self.notes_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            notes.append({
                "title": str(p.relative_to(self.notes_dir).with_suffix("")),
                "size": p.stat().st_size,
                "modified": p.stat().st_mtime,
            })
        return notes

    # ---------------------------------------------------------------- Suche
    def search(self, query: str, limit: int = 8) -> list[SearchHit]:
        terms = _terms(query)
        if not terms:
            return []
        hits: list[SearchHit] = []
        for f in self.facts():
            s = _score(f["content"], terms)
            if s:
                hits.append(SearchHit("fakt", f"#{f['id']}", f["content"], s + 2))
        for p in self.notes_dir.rglob("*.md"):
            text = p.read_text(encoding="utf-8", errors="replace")
            s = _score(p.stem, terms) * 3 + _score(text, terms)
            if s:
                hits.append(SearchHit("notiz", p.stem, _snippet(text, terms), s))
        for e in self._all("SELECT * FROM episodes ORDER BY created DESC LIMIT 500"):
            s = _score(e["summary"], terms)
            if s:
                day = time.strftime("%Y-%m-%d", time.localtime(e["created"]))
                hits.append(SearchHit("episode", day, e["summary"], s))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    # -------------------------------------------------------------- Aufgaben
    def add_task(self, title: str, due: str | None = None, notes: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO tasks (title, due, notes, created) VALUES (?, ?, ?, ?)",
            (title.strip(), due, notes, time.time()),
        )
        return int(cur.lastrowid)

    def complete_task(self, task_id: int) -> bool:
        cur = self._exec("UPDATE tasks SET done = 1, completed = ? WHERE id = ?", (time.time(), task_id))
        return cur.rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        return self._exec("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0

    def tasks(self, include_done: bool = False) -> list[dict]:
        where = "" if include_done else "WHERE done = 0"
        return self._all(
            f"SELECT * FROM tasks {where} ORDER BY done, (due IS NULL), due, created"
        )

    # ------------------------------------------------------------ Gespräche
    def new_conversation(self, title: str = "Neues Gespräch") -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO conversations (title, created, updated) VALUES (?, ?, ?)", (title, now, now)
        )
        return int(cur.lastrowid)

    def conversation(self, conv_id: int) -> dict | None:
        rows = self._all("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return rows[0] if rows else None

    def conversations(self, limit: int = 100) -> list[dict]:
        return self._all("SELECT * FROM conversations ORDER BY updated DESC LIMIT ?", (limit,))

    def rename_conversation(self, conv_id: int, title: str) -> None:
        self._exec("UPDATE conversations SET title = ? WHERE id = ?", (title[:120], conv_id))

    def delete_conversation(self, conv_id: int) -> bool:
        return self._exec("DELETE FROM conversations WHERE id = ?", (conv_id,)).rowcount > 0

    def mark_reflected(self, conv_id: int, message_count: int) -> None:
        self._exec("UPDATE conversations SET reflected_at = ? WHERE id = ?", (message_count, conv_id))

    def add_message(self, conv_id: int, role: str, content) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO messages (conversation_id, role, content, created) VALUES (?, ?, ?, ?)",
                (conv_id, role, json.dumps(content, ensure_ascii=False), now),
            )
            self._db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conv_id))
            self._db.commit()

    def messages(self, conv_id: int) -> list[dict]:
        rows = self._all(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,)
        )
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def message_count(self, conv_id: int) -> int:
        rows = self._all("SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conv_id,))
        return rows[0]["n"]


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    low = text.lower()
    pos = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, pos - width // 3)
    snippet = text[start:start + width].replace("\n", " ")
    return ("…" if start else "") + snippet + ("…" if start + width < len(text) else "")
