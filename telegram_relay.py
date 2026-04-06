"""HTTP → Telegram relay. Configuration in gitignored telegram.local.json."""

import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_CONFIG_PATH = Path(
    os.environ.get("TELEGRAM_CONFIG", Path(__file__).resolve().parent / "telegram.local.json")
)


def _load_telegram_config():
    if not _CONFIG_PATH.is_file():
        return None
    try:
        data = json.loads(_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    token = data.get("token") or data.get("TOKEN")
    chats = data.get("chats") or {}
    if not token or not isinstance(chats, dict):
        return None
    # Case-insensitive chat alias lookup
    normalized = {str(k).upper(): str(v) for k, v in chats.items()}
    return {"token": str(token), "chats": normalized}


def _chat_id(cfg, chat_key: str) -> str:
    cid = cfg["chats"].get(chat_key.upper())
    if not cid:
        raise HTTPException(status_code=404, detail="Unknown chat key")
    return cid


router = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramBody(BaseModel):
    text: str


@router.post("/{chat_key}")
async def relay_post(chat_key: str, body: TelegramBody):
    """Send message body to the named chat."""
    cfg = _load_telegram_config()
    if not cfg:
        raise HTTPException(status_code=503, detail="Telegram config missing or invalid")

    text = body.text
    if not text.strip():
        raise HTTPException(status_code=400, detail="text required")

    chat_id = _chat_id(cfg, chat_key)
    url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload)

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Telegram error: {r.text[:500]}")

    return {"ok": True}


@router.get("/{chat_key}/{message:path}")
async def relay_get_path(chat_key: str, message: str):
    """GET relay: path after chat key is the message (URL segments unescaped)."""
    cfg = _load_telegram_config()
    if not cfg:
        raise HTTPException(status_code=503, detail="Telegram config missing or invalid")

    if not message.strip():
        raise HTTPException(status_code=400, detail="message path required")

    chat_id = _chat_id(cfg, chat_key)
    url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload)

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Telegram error: {r.text[:500]}")

    return {"ok": True}
