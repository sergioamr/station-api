"""Daily message endpoints + management panel."""

import os
import random
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/message", tags=["message"])

# Spanish & co: vowels with tilde/dieresis, enye, c-cedilla (Catalan/Valencian), inverted punctuation.
_SPANISH_TO_ASCII = str.maketrans({
    # Lowercase vowels (incl. common variants beyond plain acute)
    "á": "a", "à": "a", "ä": "a", "â": "a", "ã": "a", "å": "a",
    "é": "e", "è": "e", "ë": "e", "ê": "e",
    "í": "i", "ì": "i", "ï": "i", "î": "i",
    "ó": "o", "ò": "o", "ö": "o", "ô": "o", "õ": "o",
    "ú": "u", "ù": "u", "ü": "u", "û": "u",
    "ñ": "n",
    "ç": "c",
    # Uppercase
    "Á": "A", "À": "A", "Ä": "A", "Â": "A", "Ã": "A", "Å": "A",
    "É": "E", "È": "E", "Ë": "E", "Ê": "E",
    "Í": "I", "Ì": "I", "Ï": "I", "Î": "I",
    "Ó": "O", "Ò": "O", "Ö": "O", "Ô": "O", "Õ": "O",
    "Ú": "U", "Ù": "U", "Ü": "U", "Û": "U",
    "Ñ": "N",
    "Ç": "C",
    # Punctuation
    "¿": "?",
    "¡": "!",
})

_ASCII_EXTRA = str.maketrans({
    "ß": "ss",
    "æ": "ae",
    "Æ": "AE",
    "ø": "o",
    "Ø": "O",
    "ð": "d",
    "Ð": "D",
    "þ": "th",
    "Þ": "Th",
    "ł": "l",
    "Ł": "L",
    "œ": "oe",
    "Œ": "OE",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "—": "-",
    "–": "-",
    "…": "...",
    "€": "EUR",
    "£": "GBP",
    "©": "(c)",
    "®": "(R)",
    "™": "(TM)",
})


def to_ascii_display(text: str) -> str:
    """Fold text to 7-bit ASCII for embedded panels (storage stays UTF-8)."""
    if not text:
        return text
    s = text.translate(_SPANISH_TO_ASCII)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.translate(_ASCII_EXTRA)
    return s.encode("ascii", "replace").decode("ascii")

MESSAGES_FILE = Path(os.environ.get("MESSAGES_FILE", Path(__file__).parent / "messages.txt"))


def _load_messages():
    if not MESSAGES_FILE.exists():
        return []
    return [l for l in MESSAGES_FILE.read_text().splitlines() if l.strip()]


def _save_messages(messages):
    MESSAGES_FILE.write_text("\n".join(messages) + "\n")


def get_current_message():
    """Return the current hourly message (stable per hour)."""
    messages = _load_messages()
    if not messages:
        return ""
    now = datetime.now(timezone.utc)
    seed = (now.timetuple().tm_yday - 1) * 24 + now.hour
    random.seed(seed)
    return messages[random.randint(0, len(messages) - 1)]


@router.get("")
async def get_daily_message():
    """Return the current message as JSON (ASCII for embedded displays)."""
    return {"message": to_ascii_display(get_current_message())}


@router.get("/admin", response_class=HTMLResponse)
async def message_admin():
    """Management panel for messages."""
    messages = _load_messages()
    rows = ""
    for i, msg in enumerate(messages):
        rows += f"""<tr>
            <td>{i + 1}</td>
            <td>{msg}</td>
            <td><form method="post" action="/api/message/delete" style="margin:0">
                <input type="hidden" name="index" value="{i}">
                <button type="submit">Delete</button>
            </form></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>Daily Messages</title>
<style>
  body {{ font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
  th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }}
  input[type=text] {{ width: 100%; padding: 8px; box-sizing: border-box; }}
  button {{ padding: 6px 16px; cursor: pointer; }}
  .add {{ margin-top: 20px; display: flex; gap: 8px; }}
</style>
</head><body>
<h2>Daily Messages ({len(messages)})</h2>
<table><tr><th>#</th><th>Message</th><th></th></tr>{rows}</table>
<form method="post" action="/api/message/add" class="add">
    <input type="text" name="text" placeholder="Write a new message..." required>
    <button type="submit">Add</button>
</form>
</body></html>"""


@router.post("/add")
async def add_message(text: str = Form(...)):
    messages = _load_messages()
    messages.append(text.strip())
    _save_messages(messages)
    return RedirectResponse("/api/message/admin", status_code=303)


@router.post("/delete")
async def delete_message(index: int = Form(...)):
    messages = _load_messages()
    if 0 <= index < len(messages):
        messages.pop(index)
        _save_messages(messages)
    return RedirectResponse("/api/message/admin", status_code=303)
