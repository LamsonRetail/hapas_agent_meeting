"""Cửa vào bot biên bản họp: chưa cấp quyền OAuth thì không được vào agent.

Vì sao ở đây mà không ở tầng allowlist của Hermes: user muốn người mới **tự**
cấp quyền và bản ghi nằm trong DB của V2 (state.db), không phải DB của Hermes.
`FEISHU_ALLOW_ALL_USERS=true` mở cửa Hermes, plugin này là cửa thật.

Hook `pre_gateway_dispatch` chạy TRƯỚC cửa auth của Hermes (gateway/run.py) và
nhận `MessageEvent`. Trả về:
    {"action": "skip", "reason": ...}   -> bỏ tin, agent không thấy
    {"action": "allow"}                 -> đi tiếp
    {"action": "rewrite", "text": ...}  -> đổi nội dung tin rồi đi tiếp

VÉ PHIÊN (thêm 31/07/2026): khi cho vào, V2 trả kèm `asker_token` — vé định danh
người gửi. Plugin chèn nó vào ĐẦU tin bằng `rewrite` để agent truyền lại qua
tham số `asker_token` của tool MCP. Đó là cách duy nhất `qa.py` biết ai đang hỏi:
MCP server là MỘT tiến trình dùng chung cho cả tenant, Hermes spawn nó một lần
với env tĩnh, nên lời gọi tool không mang danh tính. Xem `v2/askers.py`.

Không có vé thì vẫn `allow`: cửa fail-closed nằm ở tầng dữ liệu của V2 (một chỗ
duy nhất), không phải ở đây. Chặn ở đây nữa thì bot im mà không ai biết vì sao.

Định danh: `SessionSource.user_id_alt` = union_id của Feishu (theo chính chú thích
trong gateway/session.py) — đó cũng là khoá V2 lưu trong bảng `tokens`, nên khớp
được mà không phải tra danh bạ.

CHỈ CHAT 1-1 (thêm 02/08/2026): tin trong group/channel/thread bị bỏ. Lý do
không phải "sợ agent lẫn vé của hai người" — Hermes để
`group_sessions_per_user: true` nên mỗi người trong group đã là một session
riêng. Lý do thật: bộ lọc của V2 cấp quyền cho **người HỎI**, còn câu trả lời
thì **cả phòng đọc được**, kể cả người không có trong danh sách người dự.

FAIL-CLOSED: gọi V2 hỏng, hết thời gian, JSON xấu -> `skip`. Bot im lặng an toàn
hơn bot trả lời người chưa cấp quyền. Log ở %LOCALAPPDATA%\\hermes\\logs\\gateway.log.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Đường dẫn launcher. Đổi được bằng env cho lúc di trú máy.
GATE_BAT = os.environ.get("V2_GATE_BAT", r"E:\meetingxlark\v2-gate.bat")

# Cửa chỉ áp cho Feishu/Lark. Các nền tảng khác (CLI, telegram…) không liên quan.
PLATFORM = "feishu"

TIMEOUT_S = float(os.environ.get("V2_GATE_TIMEOUT", "25"))


def _ask_v2(union_id: str, user_id: str, name: str,
            chat_type: str = "") -> dict:
    """Gọi v2-gate.bat, đọc một dòng JSON ở stdout."""
    try:
        proc = subprocess.run(
            [GATE_BAT, union_id, user_id, name, chat_type],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            encoding="utf-8", errors="replace", shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"decision": "wait", "reason": "v2 gate quá thời gian"}
    except OSError as exc:
        return {"decision": "wait", "reason": f"không chạy được {GATE_BAT}: {exc}"}

    out = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not out:
        return {"decision": "wait",
                "reason": f"v2 gate rc={proc.returncode} "
                          f"stderr={(proc.stderr or '')[-200:]}"}
    try:
        return json.loads(out[-1])
    except json.JSONDecodeError:
        return {"decision": "wait", "reason": f"JSON xấu: {out[-1][:120]}"}


def _on_pre_dispatch(**kwargs):
    event = kwargs.get("event")
    source = getattr(event, "source", None)
    if source is None:
        return None

    platform = getattr(source, "platform", None)
    pname = getattr(platform, "value", platform)
    if str(pname).lower() != PLATFORM:
        return None                       # không phải Lark -> không can thiệp

    union_id = (getattr(source, "user_id_alt", "") or "").strip()
    user_id = (getattr(source, "user_id", "") or "").strip()
    name = (getattr(source, "user_name", "") or "").strip()
    chat_type = (getattr(source, "chat_type", "") or "").strip().lower()

    # CHỈ chat 1-1. Chặn ngay ở đây, không phiền tới V2: trong phòng nhiều
    # người, bộ lọc của V2 cấp quyền cho NGƯỜI HỎI nhưng câu trả lời thì cả
    # phòng đọc — kể cả người không có trong danh sách người dự. V2 cũng chặn
    # lần nữa (`gate._refuse_group`), đó mới là lớp có test; lớp này chỉ để
    # khỏi tốn một tiến trình con cho tin chắc chắn bị bỏ.
    #
    # `chat_type` rỗng thì ĐỪNG tự suy: để V2 quyết (nó cảnh báo rồi cho đi
    # tiếp). Đoán "rỗng nghĩa là dm" ở đây là dựng một luật thứ hai song song
    # với luật của V2, và hai luật phân quyền lệch nhau thì cái lỏng hơn thắng.
    if chat_type and chat_type != "dm":
        logger.info("[v2-gate] bỏ tin trong %s (%s) — bot chỉ trả lời chat 1-1",
                    chat_type, getattr(source, "chat_id", ""))
        return {"action": "skip", "reason": "v2: chi tra loi chat 1-1"}

    res = _ask_v2(union_id, user_id, name, chat_type)
    decision = res.get("decision")

    if decision == "allow":
        logger.info("[v2-gate] cho vào: %s (%s)", name or user_id,
                    res.get("open_id", ""))
        token = (res.get("asker_token") or "").strip()
        if not token:
            # V2 không cấp được vé (lỗi DB?) -> vẫn cho vào, nhưng nói to: tầng
            # dữ liệu sẽ từ chối và người dùng sẽ thấy bot "không nhận ra tôi".
            logger.warning("[v2-gate] KHÔNG có asker_token cho %s — bot sẽ "
                           "không đọc được biên bản của người này", union_id)
            return {"action": "allow"}
        text = getattr(event, "text", "") or ""
        # Chèn ĐẦU tin, giữ nguyên phần người dùng gõ. Kèm một câu chỉ dẫn ngắn
        # vì đây là thứ agent đọc như tin của người dùng: không nói rõ thì nó
        # hoặc bỏ qua vé, hoặc nhắc lại vé cho người dùng xem (rác + lộ vé).
        marker = (f"[V2-ASKER: {token}] (hệ thống chèn — truyền chuỗi này vào "
                  f"tham số asker_token khi gọi tool meetings; đừng nhắc lại "
                  f"với người dùng)\n")
        return {"action": "rewrite", "text": marker + text}

    if decision == "invite":
        logger.info("[v2-gate] đã gửi link cấp quyền cho %s (%s)",
                    name or user_id, union_id)
        return {"action": "skip", "reason": "v2: đã gửi link cấp quyền"}

    logger.info("[v2-gate] chặn %s (%s): %s", name or user_id, union_id,
                res.get("reason", "chưa cấp quyền"))
    return {"action": "skip", "reason": f"v2: {res.get('reason', 'chưa cấp quyền')}"}


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_dispatch)
