"""Cloudflare Worker + Queue transport for the OAuth callback.

Cloudflare never receives the Lark app secret or any user token.  It only
validates a short-lived HMAC state, queues the one-time authorization code,
and lets this local process exchange/store the token.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from . import config


_STATE_VERSION = "v1"
_STATE_RE = re.compile(
    r"^v1\.([0-9a-z]+)\.([A-Za-z0-9_-]{24,64})\.([A-Za-z0-9_-]{43})$")
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class QueueMessage:
    lease_id: str
    body: dict
    attempts: int = 0


def _now_s() -> int:
    return int(time.time())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signature(secret: str, payload: str) -> str:
    return _b64url(hmac.new(secret.encode("utf-8"), payload.encode("ascii"),
                            hashlib.sha256).digest())


def state_enabled() -> bool:
    """True only after the relay URL and shared signing secret are both set."""
    return bool(config.CF_RELAY_URL and len(config.OAUTH_STATE_SECRET) >= 32)


def queue_enabled() -> bool:
    """True only for the complete cutover configuration.

    Requiring ``state_enabled`` is deliberate: the token can be saved ahead of
    cutover without silently switching a running/restarted app away from the
    still-live Vercel mailbox.
    """
    return bool(
        state_enabled()
        and config.CF_ACCOUNT_ID
        and config.CF_QUEUE_ID
        and config.CF_QUEUE_API_TOKEN
    )


def make_state(raw_nonce: str, ttl_s: int) -> str:
    """Create the compact state understood by both local code and the Worker."""
    if not config.OAUTH_STATE_SECRET:
        raise RuntimeError("thiếu OAUTH_STATE_SECRET cho Cloudflare OAuth relay")
    expiry = _now_s() + max(60, int(ttl_s))
    expiry_raw = _base36(expiry)
    payload = f"{_STATE_VERSION}.{expiry_raw}.{raw_nonce}"
    return f"{payload}.{_signature(config.OAUTH_STATE_SECRET, payload)}"


def verify_state(state: str, *, now_s: int | None = None) -> bool:
    """Verify syntax, expiry, bounded future TTL and HMAC in constant time."""
    if not config.OAUTH_STATE_SECRET or not isinstance(state, str) or len(state) > 256:
        return False
    match = _STATE_RE.fullmatch(state)
    if not match:
        return False
    expiry_raw, _nonce, supplied = match.groups()
    try:
        expiry = int(expiry_raw, 36)
    except ValueError:
        return False
    now = _now_s() if now_s is None else int(now_s)
    if expiry < now or expiry > now + 2 * 86400:
        return False
    payload = state.rsplit(".", 1)[0]
    return hmac.compare_digest(supplied, _signature(config.OAUTH_STATE_SECRET, payload))


def short_link(state: str) -> str:
    if not state_enabled() or not verify_state(state):
        return ""
    return f"{config.CF_RELAY_URL.rstrip('/')}/e/{quote(state, safe='')}"


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    out = ""
    while number:
        number, rem = divmod(number, 36)
        out = alphabet[rem] + out
    return out


def _queue_url(suffix: str) -> str:
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CF_ACCOUNT_ID}/queues/{config.CF_QUEUE_ID}/messages/{suffix}"
    )


def _headers() -> dict[str, str]:
    return {
        "authorization": f"Bearer {config.CF_QUEUE_API_TOKEN}",
        "content-type": "application/json",
    }


def _response_json(resp: httpx.Response, operation: str) -> dict:
    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cloudflare Queue {operation} trả dữ liệu không phải JSON (HTTP {resp.status_code})"
        ) from exc
    if resp.status_code != 200 or not payload.get("success", False):
        errors = payload.get("errors") or []
        detail = "; ".join(str(e.get("message") or e.get("code") or "unknown")
                           for e in errors[:3])
        raise RuntimeError(
            f"Cloudflare Queue {operation} lỗi HTTP {resp.status_code}"
            + (f": {detail}" if detail else ""))
    return payload


def pull_messages(*, batch_size: int | None = None) -> list[QueueMessage]:
    """Lease a small batch. Nothing is deleted until ``settle_messages`` ACKs."""
    if not queue_enabled():
        return []
    size = max(1, min(int(batch_size or config.CF_QUEUE_BATCH_SIZE), 20))
    resp = httpx.post(
        _queue_url("pull"),
        headers=_headers(),
        json={
            "visibility_timeout_ms": max(10_000, config.CF_QUEUE_VISIBILITY_SECONDS * 1000),
            "batch_size": size,
        },
        timeout=config.CF_QUEUE_TIMEOUT,
    )
    result = _response_json(resp, "pull").get("result") or {}
    messages: list[QueueMessage] = []
    for raw in result.get("messages") or []:
        lease_id = str(raw.get("lease_id") or "")
        body = raw.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {"_malformed": True}
        if not isinstance(body, dict):
            body = {"_malformed": True}
        if lease_id:
            messages.append(QueueMessage(
                lease_id=lease_id,
                body=body,
                attempts=int(raw.get("attempts") or 0),
            ))
    return messages


def settle_messages(*, ack: list[str], retry: list[str]) -> None:
    """ACK terminal/success messages and release transient failures for retry."""
    if not ack and not retry:
        return
    resp = httpx.post(
        _queue_url("ack"),
        headers=_headers(),
        json={
            "acks": [{"lease_id": value} for value in ack],
            "retries": [{"lease_id": value} for value in retry],
        },
        timeout=config.CF_QUEUE_TIMEOUT,
    )
    _response_json(resp, "ack")


def _safe_api_error(resp: httpx.Response) -> str:
    try:
        errors = (resp.json() or {}).get("errors") or []
        return "; ".join(str(e.get("message") or e.get("code") or "unknown")
                         for e in errors[:3])
    except Exception:  # noqa: BLE001 - never print raw body/token from setup
        return ""


def _validate_queue_token(account_id: str, queue_id: str, token: str) -> None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/queues/{queue_id}"
    try:
        resp = httpx.get(url, headers={"authorization": f"Bearer {token}"}, timeout=20)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"không kết nối được Cloudflare API: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        detail = _safe_api_error(resp)
        raise RuntimeError(
            f"token/Queue không hợp lệ (HTTP {resp.status_code})"
            + (f": {detail}" if detail else ""))


def live_health() -> str:
    """Read-only health check for doctor; never pulls or mutates a message."""
    if not queue_enabled():
        raise RuntimeError("cấu hình Cloudflare relay/Queue chưa đủ")
    try:
        resp = httpx.get(f"{config.CF_RELAY_URL}/health", timeout=config.CF_QUEUE_TIMEOUT)
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Worker /health không đọc được: {exc.__class__.__name__}") from exc
    if resp.status_code != 200 or not payload.get("ok") or not payload.get("configured"):
        raise RuntimeError(
            f"Worker chưa sẵn sàng (HTTP {resp.status_code}, configured={payload.get('configured')})")
    _validate_queue_token(
        config.CF_ACCOUNT_ID, config.CF_QUEUE_ID, config.CF_QUEUE_API_TOKEN)
    return "Worker đã cấu hình; Queue token đọc được đúng queue (không pull message)"


def _write_dotenv(values: dict[str, str]) -> None:
    """Atomically update only named keys while preserving the existing .env."""
    path = Path(config.__file__).resolve().parent / ".env"
    if not path.exists():
        raise RuntimeError(f"không thấy file cấu hình {path}")
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = dict(values)
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if key in pending and key not in seen and not stripped.startswith("#"):
            output.append(f"{key}={pending[key]}")
            seen.add(key)
        elif key in pending and key in seen and not stripped.startswith("#"):
            continue  # remove duplicate active definitions; first-value-wins parser is unsafe here
        else:
            output.append(line)
    missing = [key for key in pending if key not in seen]
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.append("# --- Cloudflare OAuth relay (generated locally; secrets stay off git) ---")
        output.extend(f"{key}={pending[key]}" for key in missing)
    rendered = newline.join(output) + newline
    encoded = rendered.encode("utf-8")
    if had_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    fd, tmp_name = tempfile.mkstemp(prefix=".env-cloudflare-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def configure_interactive(account_id: str, queue_id: str, *, token: str | None = None) -> None:
    """Read/verify the token, then save pre-cutover values locally.

    ``token`` supports a stdin pipe on legacy Windows consoles where hidden
    ``getpass`` input does not accept Ctrl+V. It is still never placed in the
    command line, printed, or logged.
    """
    if not _HEX32_RE.fullmatch(account_id or ""):
        raise RuntimeError("Cloudflare account ID phải là 32 ký tự hex")
    if not _HEX32_RE.fullmatch(queue_id or ""):
        raise RuntimeError("Cloudflare queue ID phải là 32 ký tự hex")
    if token is None:
        token = getpass.getpass(
            "Dán Cloudflare Queue API token (màn hình sẽ không hiện): ")
    token = token.strip()
    if len(token) < 20:
        raise RuntimeError("token trống hoặc quá ngắn; chưa ghi thay đổi")
    _validate_queue_token(account_id, queue_id, token)
    signing_secret = config.OAUTH_STATE_SECRET or secrets.token_urlsafe(48)
    _write_dotenv({
        "CF_ACCOUNT_ID": account_id,
        "CF_QUEUE_ID": queue_id,
        "CF_QUEUE_API_TOKEN": token,
        "OAUTH_STATE_SECRET": signing_secret,
        # Intentionally blank until the Worker URL is known and cutover is approved.
        "CF_RELAY_URL": config.CF_RELAY_URL,
    })
    token = ""  # shorten plaintext lifetime in this process
    print("✓ Token Queue đã được kiểm tra và lưu vào v2/.env (không hiển thị).")
    print("✓ Đã sinh khóa ký state trên máy local.")
    print("Chưa kích hoạt Cloudflare relay; callback live hiện tại không bị thay đổi.")
