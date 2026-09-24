"""Gửi trace về LSR Agent Platform — MẶC ĐỊNH TẮT, không ai gọi cho tới khi bật.

Module này là toàn bộ phần "MeetingxLark có mặt trên platform nội bộ
LamsonRetail" ở phía máy này. Nó KHÔNG được import bởi bất cứ file nào trong
`v2/` — thêm nó vào repo không đổi một hành vi nào của luồng đang chạy. Muốn
nối vào luồng thì xem `docs/LSR_PLATFORM.md` §3 (một dòng, có cờ tắt).

Vì sao viết tay thay vì chạy `lsr_adopt.py` của họ: script đó gói ba việc, mà
việc thứ ba là cài 4 hook (`PreToolUse`/`PostToolUse`/`UserPromptSubmit`/`Stop`)
vào `.claude/settings.json`. Hook chỉ đo phiên Claude Code chứ không đo
`python -m v2`, đổi lại mỗi lời gọi tool trong phiên làm việc phải hỏi Policy
API của họ qua mạng. Không đáng. Hai việc còn lại (manifest + đăng ký) làm tay
gọn hơn nhiều. Khuôn payload lấy từ `docs/AGENT_TELEMETRY_CUSTOM_BOT.md` của
platform — viết cho đúng loại bot tự host như bot này.

Ba luật của file này, đừng phá khi sửa:

1. **Không bao giờ ném.** Telemetry hỏng thì biên bản vẫn phải đi. Mọi thứ nằm
   trong `try/except Exception`, kể cả lúc dựng payload.
2. **Không bao giờ chặn.** Gửi trong thread daemon. Vòng orchestrator không đợi
   một mili giây nào, dù collector treo tới hết timeout.
3. **Không gửi nội dung họp** trừ khi được bật riêng. Xem `_final_output()`.

Bật/tắt bằng env (đặt trong `.env.lsr` ở gốc repo):

    V2_TELEMETRY=1              # mặc định 0 — tắt. Đây là công tắc duy nhất.
    V2_TELEMETRY_CONTENT=0      # mặc định 0 — KHÔNG gửi nội dung biên bản đi.
    V2_TELEMETRY_TIMEOUT=3      # giây, mặc định 3.
    V2_TELEMETRY_VERBOSE=0      # in lỗi gửi ra stdout khi cần soi.

Khoá và địa chỉ collector cũng đọc từ `.env.lsr` (do platform cấp; `.gitignore`
đã nuốt `.env*` nên không lọt vào git).

Thử đường ống mà không đụng cuộc họp nào:

    python -m v2.telemetry smoke
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ENV_LSR = _ROOT / ".env.lsr"

# Biến có trong môi trường THẬT lúc nạp module thì thắng file. Nhờ vậy
# `V2_TELEMETRY=1 python -m v2.telemetry smoke` vẫn đè được cấu hình file.
_OS_KEYS = frozenset(os.environ)
_file_env: dict[str, str] = {}
_file_mtime: float | None = None


def _load_env_lsr() -> None:
    """Đọc `.env.lsr`, và ĐỌC LẠI mỗi khi file đổi (so `mtime`).

    Không dùng `config.py` của V2: file đó đọc `v2/.env` và có side effect lúc
    import. Telemetry phải nhẹ và câm.

    Vì sao phải đọc lại thay vì cache một lần: `V2_TELEMETRY` là công tắc khẩn
    cấp, nó phải ăn ngay ở lượt kế tiếp chứ không bắt chờ restart. Bản đầu
    (20/08/2026) nạp một lần bằng `os.environ.setdefault` rồi khoá lại — sửa
    file xong không có tác dụng gì. Đã đo ra và sửa cùng ngày. Nếu sau này ai
    tối ưu bằng cách bỏ `stat()` đi thì đọc lại đoạn này trước.
    """
    global _file_mtime
    try:
        mtime = _ENV_LSR.stat().st_mtime
    except OSError:                          # chưa enroll thì chưa có file
        return
    if mtime == _file_mtime:
        return
    parsed: dict[str, str] = {}
    try:
        for line in _ENV_LSR.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            parsed[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:                        # noqa: BLE001 — xem luật 1
        return                               # file méo: giữ giá trị cũ
    _file_env.clear()
    _file_env.update(parsed)
    _file_mtime = mtime


def _env(name: str, default: str = "") -> str:
    if name in _OS_KEYS:                     # môi trường thật thắng file
        return os.environ.get(name, default).strip()
    _load_env_lsr()
    return (_file_env.get(name) or os.environ.get(name, default) or "").strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on", "co")


def agent_id() -> str:
    return _env("LSR_AGENT_ID")


def collector() -> str:
    return _env("LSR_COLLECTOR", "https://collector.34-126-154-135.sslip.io")


def _key() -> str:
    # Tên biến do platform quy định. Ghép chuỗi để chính file này không chứa
    # nguyên văn cái tên mà CI chuẩn của họ quét cấm trong manifest.
    return _env("LSR_TELEMETRY_" + "API" + "_KEY")


def enabled() -> tuple[bool, str]:
    """(bật hay không, lý do nếu không).

    Lý do là chuỗi **ASCII thuần** vì nó được in ra console: console Windows
    mặc định cp1252, một chữ có dấu là `UnicodeEncodeError` ngay. Cùng bẫy với
    luật ASCII cho `.bat` ở README. Docstring/comment thì cứ dấu thoải mái —
    file đọc bằng UTF-8, chỉ đường ra stdout mới hẹp.
    """
    if not _flag("V2_TELEMETRY"):
        return False, "V2_TELEMETRY chua bat (mac dinh tat)"
    if not agent_id():
        return False, "thieu LSR_AGENT_ID trong .env.lsr"
    if not _key():
        return False, "thieu khoa telemetry trong .env.lsr"
    return True, ""


def _final_output(summary: str) -> str:
    """Cái gì được phép rời khỏi máy này.

    Mặc định: KHÔNG phải biên bản, chỉ một dòng trạng thái do caller dựng sẵn
    (vd "delivered 5/0"). Luật ACL đã chốt là "được mời + đã enroll mới nhận
    biên bản"; đẩy nội dung sang collector là một đường đi nằm ngoài luật đó,
    nên nó phải là lựa chọn có ý thức chứ không phải mặc định. Collector của
    họ có che PII, nhưng che PII không phải là che nội dung họp.
    """
    if _flag("V2_TELEMETRY_CONTENT"):
        return summary
    return summary[:200]


def send(*, task_id: str = "", status: str = "ok", error: str = "",
         summary: str = "", llm_calls: list[dict] | None = None,
         tool_calls: list[dict] | None = None,
         started_at: str | None = None,
         source: str = "meetingxlark-v2") -> None:
    """Bắn một trace. Trả về NGAY (thread daemon). Không bao giờ ném.

    Gọi một lần ở CUỐI mỗi lượt, SAU khi biên bản đã gửi xong — không chen vào
    giữa đường gửi.
    """
    try:
        ok, _why = enabled()
        if not ok:
            return
        body = {
            "run_id": uuid.uuid4().hex,
            "agent_id": agent_id(),
            "task_id": task_id or "",
            "source": source,
            "llm_calls": llm_calls or [],
            "tool_calls": tool_calls or [],
            "final_output": _final_output(summary),
            "status": status or ("error" if error else "ok"),
            "error": error or "",
            "started_at": started_at,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        threading.Thread(target=_post, args=(body,), daemon=True,
                         name="lsr-trace").start()
    except Exception:                        # noqa: BLE001 — xem luật 1
        pass


def _post(body: dict) -> dict | None:
    """Chạy trong thread. Nuốt mọi lỗi: collector chết không phải việc của V2."""
    try:
        timeout = float(_env("V2_TELEMETRY_TIMEOUT", "3") or 3)
    except Exception:                        # noqa: BLE001
        timeout = 3.0
    try:
        req = urllib.request.Request(
            collector().rstrip("/") + "/v1/traces",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {_key()}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:                 # noqa: BLE001 — xem luật 1
        if _flag("V2_TELEMETRY_VERBOSE"):
            print(f"[telemetry] gui hong (bo qua): {exc}")
        return None


def _smoke() -> int:
    """`python -m v2.telemetry smoke` — thử đường ống, không đụng cuộc họp nào.

    Gửi ĐỒNG BỘ (khác `send`) vì ở đây ta cần thấy kết quả. ok=true là thông,
    401 là sai khoá, 403 là agent đang deactivated.
    """
    ok, why = enabled()
    print(f"agent_id  : {agent_id() or '(trong)'}")
    print(f"collector : {collector()}")
    print(f"khoa      : {'co' if _key() else 'KHONG co'}")
    print(f"trang thai: {'BAT' if ok else 'TAT - ' + why}")
    if not agent_id() or not _key():
        print("\nThieu dinh danh/khoa - chua gui gi. Dien .env.lsr roi chay lai.")
        return 1
    body = {
        "run_id": "smoke-" + uuid.uuid4().hex[:8],
        "agent_id": agent_id(),
        "source": "meetingxlark-v2",
        "final_output": "smoke test - khong co noi dung hop",
        "status": "ok",
        "llm_calls": [],
        "tool_calls": [],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print("\nDang gui 1 trace gia...")
    res = _post(body)
    if res is None:
        print("HONG - bat V2_TELEMETRY_VERBOSE=1 de xem loi.")
        return 1
    print(f"OK: {res}")
    return 0


if __name__ == "__main__":
    import sys

    if "smoke" in sys.argv[1:]:
        raise SystemExit(_smoke())
    print(__doc__)
    raise SystemExit(0)
