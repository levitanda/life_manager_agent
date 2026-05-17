"""Yandex Alice skill webhook — reads the morning digest aloud."""

import logging
import os
import re

from flask import Flask, jsonify, request

import config

logger = logging.getLogger(__name__)
app = Flask(__name__)

CHUNK_SIZE = 900  # chars per Alice response (safe TTS limit)


def _load_digest() -> str:
    if os.path.exists(config.ALICE_DIGEST_FILE):
        with open(config.ALICE_DIGEST_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return "Дайджест на сегодня ещё не готов. Он появится в 6:30 утра."


def _clean_for_tts(text: str) -> str:
    """Strip markdown so Alice reads clean text."""
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)  # bold / italic
    text = re.sub(r"#{1,6}\s*", "", text)                # headings
    text = re.sub(r"`(.+?)`", r"\1", text)               # inline code
    text = re.sub(r"\n{3,}", "\n\n", text)               # extra blank lines
    return text.strip()


def _split_chunks(text: str) -> list[str]:
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= CHUNK_SIZE:
            current = (current + "\n\n" + paragraph).strip()
        else:
            if current:
                chunks.append(current)
            current = paragraph[:CHUNK_SIZE]
    if current:
        chunks.append(current)
    return chunks or [""]


def _make_response(text: str, session: dict, end: bool = True) -> dict:
    return {
        "version": "1.0",
        "session": session,
        "response": {
            "text": text,
            "tts": text,
            "end_session": end,
        },
    }


@app.route("/alice", methods=["POST"])
def alice():
    body = request.get_json(force=True, silent=True) or {}
    session = body.get("session", {})
    state = body.get("state", {}).get("session", {})
    command = body.get("request", {}).get("command", "").lower().strip()

    digest = _clean_for_tts(_load_digest())
    chunks = _split_chunks(digest)
    total = len(chunks)

    # Determine current chunk index
    chunk_idx = state.get("chunk", 0)

    # New session or explicit start command → always from beginning
    if session.get("new") or any(w in command for w in ("дайджест", "план", "начни", "привет")):
        chunk_idx = 0

    # Navigation commands
    if any(w in command for w in ("дальше", "продолжай", "следующий", "ещё")):
        chunk_idx = min(chunk_idx + 1, total - 1)
    if any(w in command for w in ("повтори", "ещё раз", "сначала")):
        chunk_idx = max(chunk_idx - 1, 0)
    if any(w in command for w in ("стоп", "хватит", "всё", "спасибо")):
        return jsonify(_make_response("Хорошего дня!", session, end=True))

    chunk_text = chunks[chunk_idx] if chunks else digest
    is_last = chunk_idx >= total - 1

    if not is_last:
        chunk_text += "\n\nПродолжить?"

    response = _make_response(chunk_text, session, end=is_last)
    # Save position so Alice knows where we are
    response["session_state"] = {"chunk": chunk_idx + (0 if is_last else 1)}

    return jsonify(response)


def run(host: str = "0.0.0.0", port: int | None = None) -> None:
    app.run(host=host, port=port or config.ALICE_PORT, use_reloader=False)
