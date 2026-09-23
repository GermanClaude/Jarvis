"""Startpunkt: ``python -m jarvis [serve|chat|reflect|profile]``."""

from __future__ import annotations

import argparse
import os
import sys

from .brain import Brain
from .config import settings


def _check_key() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print(
            "Kein API-Schlüssel gefunden.\n"
            f"Lege die Datei {settings.data_dir / '.env'} an mit:\n"
            "  ANTHROPIC_API_KEY=sk-ant-...\n"
            "Schlüssel gibt es unter https://console.anthropic.com/",
            file=sys.stderr,
        )
        sys.exit(1)


def _make_jarvis():
    from .agent import Jarvis

    _check_key()
    settings.ensure_dirs()
    return Jarvis(Brain(settings.db_path, settings.brain_dir), settings)


def cmd_serve(args) -> None:
    from .server import serve

    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.token:
        import secrets

        settings.token = secrets.token_urlsafe(16)
    serve(_make_jarvis(), settings)


def cmd_chat(args) -> None:
    jarvis = _make_jarvis()
    conv_id = None
    print("JARVIS im Terminal. '/neu' startet ein neues Gespräch, '/lernen' reflektiert, '/exit' beendet.\n")

    def emit(event: str, data: dict) -> None:
        if event == "text":
            print(data["delta"], end="", flush=True)
        elif event == "tool":
            print(f"\n  ⚙ {data['name']} …", flush=True)
        elif event == "tool_result":
            print(f"  {'✓' if data['ok'] else '✗'} {data['preview'][:160]!r}", flush=True)
        elif event == "error":
            print(f"\n[Fehler] {data['message']}", flush=True)

    while True:
        try:
            text = input("\nDu › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            break
        if text == "/neu":
            if conv_id:
                jarvis.reflect(conv_id)
            conv_id = None
            print("Neues Gespräch.")
            continue
        if text == "/lernen":
            if conv_id:
                result = jarvis.reflect(conv_id)
                print(f"Gelernt: {len(result['new_facts']) if result else 0} neue Fakten.")
            continue
        print("JARVIS › ", end="", flush=True)
        conv_id = jarvis.chat(conv_id, text, emit=emit)
        print()
    if conv_id:
        jarvis.reflect(conv_id)


def cmd_reflect(args) -> None:
    jarvis = _make_jarvis()
    for conv in jarvis.brain.conversations():
        if jarvis.brain.message_count(conv["id"]) > conv["reflected_at"]:
            result = jarvis.reflect(conv["id"])
            if result:
                print(f"#{conv['id']} {result['title']}: +{len(result['new_facts'])} Fakten")


def cmd_profile(args) -> None:
    settings.ensure_dirs()
    brain = Brain(settings.db_path, settings.brain_dir)
    print(brain.profile_text())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Dein persönlicher KI-Assistent.")
    sub = parser.add_subparsers(dest="cmd")
    p_serve = sub.add_parser("serve", help="Web-Oberfläche starten (Standard)")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    sub.add_parser("chat", help="Im Terminal chatten")
    sub.add_parser("reflect", help="Aus allen Gesprächen lernen")
    sub.add_parser("profile", help="Zeigen, was Jarvis über dich weiß")
    args = parser.parse_args(argv)
    handlers = {"chat": cmd_chat, "reflect": cmd_reflect, "profile": cmd_profile}
    if args.cmd is None:
        args.host = args.port = None
    handlers.get(args.cmd, cmd_serve)(args)


if __name__ == "__main__":
    main()
