"""
Cấu hình V2 — đọc từ biến môi trường / file .env, không hardcode.

Kỷ luật di trú (V2_LONGTERM §4.3):
    - Không hardcode đường dẫn Windows. Mọi path từ env, mặc định tương đối.
    - Không giả định múi giờ hệ thống. Lưu UTC, hiển thị +07 khi in ra.
    - Toàn bộ state di trú được gói trong data/state.db + .env.

Không dùng thư viện ngoài để đọc .env: tự parse cho khỏi thêm phụ thuộc.
Biến môi trường thật (đã export) luôn thắng giá trị trong .env.
"""

from __future__ import annotations

import os
from pathlib import Path

# ------------------------------------------------------- nạp .env (một lần)

_ENV_LOADED = False


def _load_dotenv() -> None:
    """Đọc file .env cạnh package nếu có. Không ghi đè biến đã tồn tại."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _get(key: str, default: str = "") -> str:
    _load_dotenv()
    return os.environ.get(key, default)


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_bool(key: str, default: bool) -> bool:
    return _get(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# ------------------------------------------------------------- Lark app

# Domain: "lark" (larksuite.com quốc tế) hoặc "feishu" (feishu.cn nội địa TQ).
LARK_DOMAIN = _get("LARK_DOMAIN", "lark")

APP_ID = _get("LARK_APP_ID", "cli_aae288361ef89eed")   # Agent meeting (mặc định)
APP_SECRET = _get("LARK_APP_SECRET", "")

# OAuth: người dùng authorize để app đọc minute/calendar bằng token của họ.
# redirect_uri phải khớp CHÍNH XÁC với cái đăng ký trong Console.
# V2_LONGTERM §3.1: nên là domain riêng (meet.<cty>/oauth/callback), KHÔNG
# phải *.vercel.app, để sau đổi hạ tầng chỉ cần đổi DNS.
OAUTH_REDIRECT_URI = _get("OAUTH_REDIRECT_URI", "")

# Scope xin ngay từ đầu (V2_LONGTERM §3.2): kể cả `task` chưa dùng ở v1.
# Thêm scope sau = cả công ty duyệt lại, nên xin đủ một lần.
# offline_access BẮT BUỘC đứng đầu: endpoint token v2 chỉ trả refresh_token
# khi có scope này (thiếu -> complete() lỗi 'refresh_token').
# ĐO 30/07/2026 (V2_MAINTENANCE §10) — sửa hai điều từng ghi sai ở đây:
#
# 1. `vc:meeting.meetingevent:read` KHÔNG phải scope còn thiếu. Console đã duyệt
#    nó cho app (cả tenant lẫn user) và token đã mang nó từ trước. Nó chỉ mở
#    endpoint "sự kiện TRONG cuộc họp" (ai vào/ra), không liên quan tới việc nối
#    sự kiện lịch <-> cuộc họp. Vẫn giữ trong danh sách vì có xin thì có.
# 2. Scope thật sự còn thiếu để đạt `calendar[verified]`: **`vc:meeting:readonly`
#    ở danh tính NGƯỜI DÙNG** (hoặc `vc:meeting.meetingid:read`). App V2 chỉ có
#    nó ở danh tính tenant, nên mget_instance_relation_info trả entry mà LẶNG LẼ
#    bỏ meeting_id (không lỗi, không cảnh báo) -> người dự chỉ ghép theo tên/giờ.
#    Cần sửa Console + admin duyệt; xin xong thì enroll lại cho chắc.
#
# Lưu ý về chính biến này: tenant này cấp cho token **toàn bộ** scope user mà app
# được duyệt (196 cái), bất kể danh sách xin ở đây (đo: token trước và sau khi
# thêm scope vào đây đều có đúng 196 scope). Nên OAUTH_SCOPES gần như chỉ còn
# giá trị tài liệu — quyền thật do Console quyết.
OAUTH_SCOPES = _get(
    "OAUTH_SCOPES",
    "offline_access minutes:minutes:readonly calendar:calendar:readonly "
    "vc:record:readonly contact:user.base:readonly task:task:write "
    "vc:meeting.meetingevent:read vc:meeting:readonly",
)

# --------------------------------------------------------- hạ tầng / path

_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(_get("V2_DATA_DIR", str(_ROOT / "data")))
DB_PATH = Path(_get("V2_DB_PATH", str(DATA_DIR / "state.db")))
WORK_DIR = Path(_get("V2_WORK_DIR", str(DATA_DIR / "work")))       # file tạm
TRANSCRIPT_DIR = Path(_get("V2_TRANSCRIPT_DIR", str(DATA_DIR / "transcripts")))

# Khóa mã hóa token khi lưu (Fernet, base64 44 ký tự). BẮT BUỘC đặt ở prod.
FERNET_KEY = _get("V2_FERNET_KEY", "")

# --------------------------------------------------------- transcribe seam

# transcribe_server (giữ nguyên từ V1). localhost khi cùng máy; sau lên VPS
# đổi thành <ip-nội-bộ>:8502, không sửa code (V2_LONGTERM §6 bước 5).
TRANSCRIBE_URL = _get("TRANSCRIBE_URL", "http://localhost:8000")
# Ngôn ngữ gợi ý cho Whisper (để trống -> tự phát hiện).
TRANSCRIBE_LANG = _get("TRANSCRIBE_LANG", "vi")
# KHÔNG thêm lại hằng "tên engine" ở đây: transcribe.py lấy tên model THẬT từ
# /status của server (faster-whisper/<model>). Một hằng số dán cứng sẽ nói dối
# hồ sơ transcript ngay lần đầu ai đó đổi model trong run-server.bat.

# ------------------------------------------------------------- summarize seam

LLM_API_KEY = _get("LLM_API_KEY") or _get("OPENAI_API_KEY")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = _get("LLM_MODEL", "gpt-4o-mini")

# Seam LLM đổi provider chỉ bằng LLM_BASE_URL (V2_LONGTERM §4.2). Hermes phơi
# endpoint OpenAI-compatible nên đổi GPT->Hermes chỉ là đổi base_url + api_key.
# response_format=json_object là tính năng riêng của OpenAI; Hermes (lớp agent)
# có thể không nhận -> đặt LLM_JSON_MODE=0. summarize.py cũng tự thử lại không
# kèm field này nếu provider từ chối, nên quên đổi cờ vẫn chạy.
LLM_JSON_MODE = _get_bool("LLM_JSON_MODE", True)
# Agent loop của Hermes chạy lâu hơn GPT gọi thẳng -> timeout rộng.
LLM_TIMEOUT = _get_int("LLM_TIMEOUT", 240)

# ------------------------------------------------------------- hành vi poller

POLL_INTERVAL = _get_int("POLL_INTERVAL", 300)     # giây giữa 2 vòng quét
LOOKBACK_DAYS = _get_int("LOOKBACK_DAYS", 2)       # quét lùi để bắt bù
SETTLE_MINUTES = _get_int("SETTLE_MINUTES", 3)     # chờ Lark liên kết bản ghi
CAL_WINDOW_HOURS = _get_int("CAL_WINDOW_HOURS", 3)
# Khi KHÔNG xác minh được sự kiện <-> cuộc họp bằng meeting_id (thiếu scope),
# chỉ dám tin sự kiện nếu trùng chính xác tên HOẶC lệch giờ trong ngần này phút.
# Đặt 0 = tuyệt đối không đoán, chỉ nhận sự kiện đã xác minh hoặc trùng tên.
CAL_STRICT_MINUTES = _get_int("CAL_STRICT_MINUTES", 30)
MAX_EVENTS_TO_CHECK = _get_int("MAX_EVENTS_TO_CHECK", 12)

# Không tra được người dự -> gửi cho chính người phát hiện ra minute.
FALLBACK_TO_OWNER = _get_bool("FALLBACK_TO_OWNER", True)

# Cửa duyệt đã BỎ (30/07/2026): họp xong phát ngay cho người dự, không ai duyệt.
# REQUIRE_APPROVAL / APPROVAL_TIMEOUT_HOURS không còn tồn tại — nếu .env cũ còn
# đặt chúng thì bị bỏ qua, không phải lỗi. Công tắc dừng khẩn vẫn là PAUSED,
# và SEND_MODE=0 (dry-run) vẫn là lưới an toàn trước khi phát thật.

# Số lần thử lại một job trước khi bỏ và báo failed.
MAX_ATTEMPTS = _get_int("MAX_ATTEMPTS", 5)

# TTL nonce OAuth (giây). Luồng Vercel cần thời gian relay code -> để rộng.
# 24 GIỜ, không phải 15 phút như bản đầu. Lý do đổi (31/07/2026): luồng enroll
# tự phục vụ (§14) là bot gửi link rồi người ta bấm khi nào rảnh — bữa trưa,
# hôm sau. Nonce 15 phút biến chuyện đó thành "phiên enroll đã hết hạn" và họ
# phải nhắn lại bot. Đã tự vướng đúng lỗi này.
# Rủi ro của TTL dài: ai có link đó thì tự enroll được (họ chỉ enroll CHÍNH họ,
# và phải ở trong tenant) — chấp nhận được, đổi lại luồng dùng được thật.
OAUTH_NONCE_TTL = _get_int("OAUTH_NONCE_TTL", 86400)

# Bao lâu thì bot gửi lại link cho người chưa cấp quyền. TÁCH khỏi TTL nonce:
# nếu dùng chung thì TTL 24h nghĩa là mất link phải chờ 24h mới có cái mới.
ENROLL_REINVITE_MINUTES = _get_int("ENROLL_REINVITE_MINUTES", 30)

# Công tắc dừng khẩn (đọc lại mỗi vòng). Đặt 1 để tạm dừng phát.
PAUSED = _get_bool("PAUSED", False)

# Chế độ an toàn mặc định: không gửi thật trừ khi bật.
SEND_MODE = _get_bool("SEND_MODE", False)

# ------------------------------------------- cảnh báo qua Lark DM (sổ tay §17)

# Ai nhận cảnh báo khi hệ thống hỏng. Danh sách union_id, ngăn bằng dấu phẩy.
# TRỐNG = TẮT HẲN: không gửi cho ai, kể cả lúc hỏng. Dùng union_id (không phải
# open_id) vì im_send_text mặc định id_type="union_id" — xem pipeline.deliver.
ALERT_UNION_IDS = [s.strip() for s in _get("ALERT_UNION_IDS", "").split(",")
                   if s.strip()]

# Whisper phải gọi KHÔNG được liên tục ngần này phút thì mới báo. Vì sao không
# báo ngay lần đầu: restart whisper hay một cú timeout lẻ là chuyện thường; báo
# ngay thì cảnh báo mất giá trị và người ta bắt đầu bỏ qua nó — lúc hỏng thật
# cũng không ai đọc.
ALERT_WHISPER_AFTER_MIN = _get_int("ALERT_WHISPER_AFTER_MIN", 15)

# -------------------------------------- Base "nội dung đã chốt" (draft/final)

# Sinh bằng `python -m v2 base-init` (một lần) rồi dán vào .env. Để trống ->
# tắt hẳn phần ghi Base; phát biên bản vẫn chạy bình thường.
BITABLE_APP_TOKEN = _get("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID = _get("BITABLE_TABLE_ID", "")

# Hỏi đáp về cuộc họp: KHÔNG có config riêng ở đây. Bot chat là Hermes (app Lark
# riêng của nó, cấu hình trong ~/.hermes/), còn V2 chỉ phơi dữ liệu qua MCP
# (`python -m v2 mcp`) và đọc Base bằng BITABLE_* ở trên. `python -m v2 ask`
# dùng LLM_* ở trên. Xem docs/V2_MAINTENANCE.md §12.

# ------------------------------------------------- dashboard trạng thái (đẩy)

# Máy local ĐẨY snapshot đã ẩn danh lên hàm serverless Vercel; Vercel không bao
# giờ query ngược về đây (V2_ARCHITECTURE §3). Để trống -> tắt hẳn dashboard.
STATUS_PUSH_URL = _get("STATUS_PUSH_URL", "")
# Bearer dùng chung, phải khớp env STATUS_PUSH_SECRET trên Vercel.
STATUS_PUSH_SECRET = _get("STATUS_PUSH_SECRET", "")
STATUS_PUSH_TIMEOUT = _get_int("STATUS_PUSH_TIMEOUT", 15)

# Hộp thư code OAuth trên Vercel (enroll tự phục vụ, §13 sổ tay). Để trống là
# tắt: khi đó enroll vẫn chạy nhưng phải dán code bằng tay như trước.
# Dùng CHUNG bearer với dashboard (STATUS_PUSH_SECRET) — cùng một deployment,
# cùng một chủ; code Lark lại dùng-một-lần và Vercel không có app_secret nên tự
# nó không đổi được code thành token.
OAUTH_PULL_URL = _get(
    "OAUTH_PULL_URL",
    (STATUS_PUSH_URL.rsplit("/api/", 1)[0] + "/api/oauth-pending")
    if STATUS_PUSH_URL else "",
)
# Giãn cách tối thiểu giữa 2 lần đẩy trong vòng run() (giây). 0 = mỗi vòng.
STATUS_PUSH_EVERY = _get_int("STATUS_PUSH_EVERY", 0)


# ----------------------------------------------------------------- helpers


def reload_switches() -> bool:
    """Đọc lại công tắc dừng khẩn PAUSED từ .env cho tiến trình ĐANG chạy.

    Vì sao phải có hàm này: `PAUSED` ở trên được đánh giá MỘT LẦN lúc import, nên
    sửa `.env` không hề ảnh hưởng tiến trình đang chạy — trong khi sổ tay vẫn ghi
    "(đọc lại mỗi vòng)". Đo 31/07/2026: đúng là không ăn, tức công tắc dừng khẩn
    chỉ là niềm tin. Đúng lúc cần nó nhất (một cuộc họp thật sắp bị phát cho sai
    người) thì cách duy nhất là kill tiến trình.

    Chỉ đọc lại PAUSED. KHÔNG đọc lại SEND_MODE: cờ `--send` do người gõ lệnh
    quyết, đọc lại file sẽ ghi đè ý họ.

    Trả về True nếu giá trị vừa đổi (để caller in log).
    """
    global PAUSED
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() != "PAUSED":
            continue
        new = val.strip().strip('"').strip("'").lower() in ("1", "true", "yes", "on")
        if new != PAUSED:
            PAUSED = new
            return True
        return False
    return False


def base_url() -> str:
    """Gốc Open API theo domain."""
    return ("https://open.larksuite.com" if LARK_DOMAIN == "lark"
            else "https://open.feishu.cn")


def ensure_dirs() -> None:
    for d in (DATA_DIR, WORK_DIR, TRANSCRIPT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def summary() -> str:
    """Chuỗi tóm tắt cấu hình để in lúc khởi động (không lộ secret)."""
    def mask(s: str) -> str:
        return (s[:6] + "…") if s else "(trống)"
    return (
        f"domain={LARK_DOMAIN} app_id={APP_ID} "
        f"app_secret={mask(APP_SECRET)} fernet={'có' if FERNET_KEY else 'TRỐNG'}\n"
        f"redirect_uri={OAUTH_REDIRECT_URI or '(chưa đặt)'}\n"
        f"transcribe={TRANSCRIBE_URL} llm={LLM_MODEL if LLM_API_KEY else '(không có key)'}\n"
        f"poll={POLL_INTERVAL}s lookback={LOOKBACK_DAYS}d settle={SETTLE_MINUTES}m "
        f"send={'THẬT' if SEND_MODE else 'dry-run'} paused={PAUSED}\n"
        f"dashboard={STATUS_PUSH_URL or '(tắt)'}\n"
        f"base={'bảng ' + BITABLE_TABLE_ID if BITABLE_APP_TOKEN else '(tắt)'} "
        f"cảnh báo={str(len(ALERT_UNION_IDS)) + ' người' if ALERT_UNION_IDS else '(tắt)'}"
    )
