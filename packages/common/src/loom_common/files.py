"""Telling text from binary files (photos, PDFs, archives) so deliveries can carry both."""

from __future__ import annotations

import base64

MAX_DELIVERY_BYTES = 25_000_000


def as_text(data: bytes) -> str | None:
    """The bytes as text when they are UTF-8 without NUL bytes, otherwise None (the file is binary)."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def to_payload(data: bytes) -> dict[str, str]:
    """JSON fields carrying the bytes: text as ``content``, anything else base64 as ``content_base64``."""
    text = as_text(data)
    return {"content": text} if text is not None else {"content_base64": base64.b64encode(data).decode()}


def from_payload(item: dict) -> bytes:
    if item.get("content_base64") is not None:
        return base64.b64decode(item["content_base64"])
    return (item.get("content") or "").encode()


def describe_size(size: int) -> str:
    if size < 1_000:
        return f"{size} B"
    if size < 1_000_000:
        return f"{size / 1_000:.0f} KB"
    return f"{size / 1_000_000:.1f} MB"
