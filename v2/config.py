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

    # `utf-8-sig`, KHÔNG phải `utf-8` (sửa 02/08/2026). Trên Windows, cả
    # `Out-File -Encoding utf8` của PowerShell 5.1 lẫn Notepad đều ghi file KÈM
    # BOM — và `v2\.env` hiện ĐANG có BOM (đo được). Đọc bằng `utf-8` thì ký tự
    # BOM dính vào đầu dòng đầu tiên, nên dòng đó không còn bắt đầu bằng `#` và
    # cũng không còn là tên biến đúng.
    #
    # Hôm nay vô hại vì dòng đầu là comment. Nhưng nó là mìn: ngày nào có người
    # đưa một cấu hình THẬT lên dòng đầu thì nó bị bỏ qua LẶNG LẼ, và vì
    # `LARK_APP_ID` có giá trị mặc định dán cứng ngay dưới đây, triệu chứng sẽ
    # là "V2 chạy bằng app khác" chứ không phải một lỗi đọc được.
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
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

APP_ID = _get("LARK_APP_ID", "cli_aae288361ef89eed")   # Meeting Agent CĐS (mặc định)
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
# ⛔ ĐỪNG tin lại câu từng ghi ở đây: "tenant này cấp toàn bộ 196 scope bất kể
# danh sách xin, nên OAUTH_SCOPES chỉ còn giá trị tài liệu". **SAI**, và nó sống
# sót nhiều phiên vì cả hệ thống chỉ có MỘT người dùng. Phản chứng sạch, đo
# 31/07/2026 khi người THỨ HAI enroll:
#
#     Thẩm  (enroll 30/07, đã bấm Đồng ý nhiều lần lúc phát triển) -> 197 scope
#     Chi   (enroll 31/07, bấm Đồng ý đúng một lần)                ->   9 scope
#
# Cùng app, cùng OAUTH_SCOPES, khác nhau ở chỗ Lark **cộng dồn** các lần cấp
# quyền trước của TỪNG người. 197 của Thẩm là di sản, không phải quy tắc; người
# mới chỉ nhận đúng những gì xin ở dòng dưới (+ auth:user.id:read).
#
# Hệ quả đã cắn thật: `minutes_search` của Chi trả 99991679 đòi
# `minutes:minutes.search:read` — scope Console đã duyệt từ lâu nhưng dòng này
# không xin, nên suốt thời gian một người dùng KHÔNG AI THẤY.
#
# ⇒ Quy tắc đúng, cả BA điều đều cần: Console duyệt (đúng danh tính user) +
#   tên có trong OAUTH_SCOPES + người đó enroll lại.
# ⇒ Và vì thêm scope = MỌI người phải enroll lại, hãy xin đủ một lần
#   (V2_LONGTERM §3.2). `minutes.transcript:export` xin sẵn cho hướng "dùng
#   transcript của Lark thay whisper" dù pipeline hiện chưa gọi.
# ⚠️ Nguồn sự thật của danh sách này là `scopecheck.SCOPES_NEEDED` — mỗi scope
# ở đó ứng với MỘT lời gọi có thật trong `scopecheck._checks()`. Sinh lại bằng
# `python -m v2 scopes --print-all` rồi dán vào `v2/.env`. Chuỗi dưới đây chỉ là
# bản dự phòng cho máy chưa có `.env`; giữ hai chỗ khớp nhau.
# Siết 124 -> 18 scope ngày 01/08/2026 (sổ tay §22).
OAUTH_SCOPES = _get(
    "OAUTH_SCOPES",
    "offline_access minutes:minutes:readonly minutes:minutes.basic:read "
    "minutes:minutes.search:read minutes:minutes.media:export "
    "minutes:minutes.transcript:export calendar:calendar:readonly "
    "calendar:calendar:read calendar:calendar.event:read vc:meeting:readonly "
    "vc:record:readonly vc:recording:read contact:user.base:readonly "
    "contact:user.id:readonly contact:user.employee_id:readonly "
    "task:task:write task:task:read docs:document.media:upload",
)

# `docs:document.media:upload` để làm gì: `bitable.base_media_upload` đính file
# transcript vào ô attachment của Base bằng USER token qua
# `/drive/v1/medias/upload_all`. Đo 31/07 bằng cách gọi với `parent_node` bịa:
# Thẩm trả `1061044 parent node not exist` (qua được cửa quyền), Chi trả
# `99991679` liệt kê 8 scope thay thế được — trong đó CHỈ `docs:document.media:
# upload` và `mail:user_mailbox.message:modify` là Console đã duyệt ở danh tính
# user. Chọn cái đầu vì nó hẹp nhất (`drive:drive` mở cả Drive, quá rộng cho
# việc chỉ upload một file .txt).

# --------------------------------------------------------- hạ tầng / path

_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(_get("V2_DATA_DIR", str(_ROOT / "data")))
DB_PATH = Path(_get("V2_DB_PATH", str(DATA_DIR / "state.db")))
WORK_DIR = Path(_get("V2_WORK_DIR", str(DATA_DIR / "work")))       # file tạm
TRANSCRIPT_DIR = Path(_get("V2_TRANSCRIPT_DIR", str(DATA_DIR / "transcripts")))

# Khóa mã hóa token khi lưu (Fernet, base64 44 ký tự). BẮT BUỘC đặt ở prod.
FERNET_KEY = _get("V2_FERNET_KEY", "")

# ----------------------------------------------------- sao lưu (sổ tay §6)
#
# Mất `state.db` KHÔNG chỉ là "cả công ty enroll lại". Nó còn xoá luôn PHÂN
# QUYỀN HỎI ĐÁP: `qa.viewers_index()` dựng danh sách "ai được xem cuộc họp nào"
# từ `jobs.meta_json.attendees`. Base còn nguyên biên bản, nhưng không có job
# tương ứng thì `_may_see` trả False cho tất cả (trừ admin) — cả công ty mất
# quyền đọc biên bản của chính mình, im lặng.
#
# Vì vậy backup chạy TỰ ĐỘNG trong vòng `run`, không phải một việc phải nhớ.
BACKUP_DIR = Path(_get("V2_BACKUP_DIR", str(DATA_DIR / "backups")))
BACKUP_KEEP = _get_int("V2_BACKUP_KEEP", 14)        # giữ bao nhiêu bản
BACKUP_EVERY_HOURS = _get_int("V2_BACKUP_EVERY_HOURS", 24)   # 0 = tắt hẳn

# Khóa Fernet để RIÊNG CHỖ với DB (sổ tay §6: "backup cùng nhau nhưng để tách
# chỗ"). DB không có key = vô dụng; key không có DB = vô dụng. Mặc định nằm
# NGOÀI repo và ngoài `data/`, nên hai tai nạn hay gặp nhất — sửa hỏng `v2\.env`
# và xoá nhầm `v2\data\` — không thổi bay cả hai cùng lúc.
#
# ⚠️ Giới hạn phải biết: trên máy này C:, D:, E: đều là PHÂN VÙNG của MỘT ổ vật
# lý (đo 02/08/2026, Disk #0). Nên mặc định chống được xoá nhầm / hỏng file / DB
# corrupt, KHÔNG chống được chết ổ. Muốn chống chết ổ thì trỏ `V2_BACKUP_DIR`
# sang OneDrive hoặc ổ ngoài — nhưng nhớ bản sao đó chứa recap nội dung họp ở
# dạng đọc được, nên đó là quyết định về dữ liệu, không phải về kỹ thuật.
KEY_BACKUP_PATH = Path(_get(
    "V2_KEY_BACKUP_PATH", str(Path.home() / ".meetingxlark" / "fernet-key.txt")))

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

# Quét lùi bao nhiêu ngày để BẮT BÙ. Đây là toàn bộ khả năng phục hồi của hệ
# thống sau một lần gián đoạn: máy tắt / ngủ / Windows Update khởi động lại rồi
# kẹt ở màn hình đăng nhập / đi công tác lâu hơn ngần này ngày = mọi cuộc họp
# trong khoảng đó KHÔNG BAO GIỜ được quét lại, không lỗi, không cảnh báo.
#
# 2 -> 7 ngày (02/08/2026): 2 ngày không sống nổi một kỳ nghỉ lễ hay một cuối
# tuần dài, mà V2 chạy trên PC cá nhân dưới phiên đăng nhập của người dùng nên
# gián đoạn nhiều ngày là chuyện sẽ xảy ra, không phải nếu.
#
# Chi phí của số lớn hơn gần như bằng 0: `minutes_list` phân trang (5 trang x 30
# = 150 minute/người/vòng, thừa sức cho 7 ngày), và minute đã xử lý bị loại ngay
# bằng `db.is_claimed` / `jobstore.get` trước khi tốn bất kỳ lời gọi nào khác.
LOOKBACK_DAYS = _get_int("LOOKBACK_DAYS", 7)
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

# Gọi LLM hỏng bao nhiêu lần LIÊN TIẾP thì thôi chờ và phát bản không có recap.
# Vì sao có ngưỡng thay vì chờ mãi: LLM hỏng vì 429/timeout thì vòng sau là chạy
# được, nhưng hỏng vì key sai/hết tiền thì chờ đến bao giờ cũng vậy — mà người dự
# vẫn đang đợi biên bản. Đếm ở cột `jobs.recap_fails`, KHÔNG tiêu `MAX_ATTEMPTS`.
# Với POLL_INTERVAL=300s thì 3 lần ≈ 15 phút hoãn trước khi chịu phát bản trần.
RECAP_MAX_TRIES = _get_int("RECAP_MAX_TRIES", 3)

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

# ------------------------------------------- hỏi đáp: ai được xem biên bản nào
#
# Bot CHỈ trả về cuộc họp mà người hỏi có trong danh sách người dự (hoặc là chủ
# cuộc họp) — chốt 31/07/2026, xem V2_MAINTENANCE §20. Trước đó `qa.py` đọc toàn
# bộ Base cho bất kỳ ai đã enroll, tức enroll = đọc được biên bản của cả nhà.
#
# QA_ADMIN_UNION_IDS: ai được xem TẤT CẢ (để chẩn lỗi). Mặc định lấy đúng
# ALERT_UNION_IDS vì hôm nay người nhận cảnh báo và người chẩn lỗi là một.
# TRỐNG = KHÔNG ai là admin. Đây là mặc định fail-closed, KHÔNG phải "mở hết".
QA_ADMIN_UNION_IDS = [s.strip() for s in
                      _get("QA_ADMIN_UNION_IDS",
                           ",".join(ALERT_UNION_IDS)).split(",") if s.strip()]

# Vé phiên của người hỏi sống bao lâu (giây). Mỗi tin nhắn mới đều gia hạn, nên
# đây là "im lặng bao lâu thì phải xin vé mới", không phải giới hạn hội thoại.
QA_TOKEN_TTL = _get_int("QA_TOKEN_TTL", 900)

# ------------------------------------------ glossary tự cải thiện (part B)
#
# Thuật ngữ/tên riêng đã DUYỆT được nhồi vào initial_prompt whisper — gửi ĐỘNG
# qua form `prompt` (cùng đường tên người dự, part A), KHÔNG ghi vào vi-prompt.txt
# của server: file đó chỉ đọc lúc startup nên ghi vào sẽ phải restart mỗi lần
# duyệt. Ứng viên do Hermes trích sau mỗi cuộc, admin duyệt QUA BOT (nhắn 'duyệt
# <từ>'). Người duyệt = QA_ADMIN_UNION_IDS; digest tuần gửi tới ALERT_UNION_IDS.
GLOSSARY_ENABLED = _get_bool("V2_GLOSSARY_ENABLED", True)
# Chỉ nổi ứng viên gặp >= ngần này CUỘC trong digest (cắt nhiễu nghe-nhầm một lần).
GLOSSARY_MIN_COUNT = _get_int("V2_GLOSSARY_MIN_COUNT", 2)
# Cắt số thuật ngữ đã duyệt nhồi vào prompt (server còn cắt theo token thật nữa).
GLOSSARY_MAX_TERMS = _get_int("V2_GLOSSARY_MAX_TERMS", 60)

# Whisper phải gọi KHÔNG được liên tục ngần này phút thì mới báo. Vì sao không
# báo ngay lần đầu: restart whisper hay một cú timeout lẻ là chuyện thường; báo
# ngay thì cảnh báo mất giá trị và người ta bắt đầu bỏ qua nó — lúc hỏng thật
# cũng không ai đọc.
ALERT_WHISPER_AFTER_MIN = _get_int("ALERT_WHISPER_AFTER_MIN", 15)

# LLM (recap) gọi không được liên tục ngần này phút thì báo. Cùng lý lẽ với
# whisper, nhưng hậu quả KHÁC và tệ hơn: whisper chết thì job nằm chờ (không
# mất gì), còn LLM chết quá RECAP_MAX_TRIES vòng thì biên bản PHÁT ĐI với tóm
# tắt rỗng — mất vĩnh viễn nếu không có ai vá lại (xem orchestrator._backfill_recaps).
ALERT_LLM_AFTER_MIN = _get_int("ALERT_LLM_AFTER_MIN", 15)

# Vòng `run` im lặng quá ngần này phút thì báo. 0 = tắt.
#
# Vì sao mục này quan trọng hơn vẻ ngoài của nó: `alerts.check_all()` chạy TỪ
# TRONG vòng `run`, nên vòng đó chết là mọi cảnh báo khác chết theo — đúng cái
# tính chất mà `alerts` sinh ra để chữa (dashboard cũng chỉ sống khi `run` sống).
# Phép kiểm này là cái DUY NHẤT phải chạy từ ngoài, bằng Task Scheduler gọi
# `python -m v2 alerts` (xem run-v2-alerts.bat + sổ tay §28).
#
# Và nó gấp gáp hơn "biết muộn vài giờ": refresh token của Lark chỉ sống 7 NGÀY
# và được gia hạn bởi CHÍNH vòng `run` mỗi lần refresh (đo 02/08/2026 — mọi
# token đều có refresh_exp = updated_at + 7 ngày). `run` tắt quá 7 ngày là mọi
# người phải enroll lại, mà cảnh báo "token sắp hết" cũng nằm trong `run`.
#
# 30 phút = 6 vòng POLL_INTERVAL mặc định. Đừng đặt thấp hơn ~10 phút: heartbeat
# ghi mỗi 60s nhưng máy ngủ / DB bận có thể làm trễ vài nhịp.
ALERT_RUN_STALE_MIN = _get_int("ALERT_RUN_STALE_MIN", 30)

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
#
# Mặc định 7200 chứ KHÔNG phải 0 (đổi 02/08/2026): mỗi lần đẩy là một thao tác
# ghi Vercel Blob, tính vào hạn mức "Advanced Requests" — **2.000 thao
# tác/THÁNG** ở gói free. Với 0 thì đẩy mỗi vòng `POLL_INTERVAL`; đo từ log là
# 109 lần/ngày, và Vercel đã gửi thư báo dùng hết 75% sau ~3,5 ngày (đọc trên
# dashboard ngày 02/08: 1.643/2.000, toàn bộ đốt trong 4 ngày). Cạn hạn mức thì
# hộp thư OAuth chết theo, tức KHÔNG AI ENROLL ĐƯỢC — mất thứ quan trọng hơn
# hẳn cái dashboard.
#
# ⚠️ Biến này KHÔNG liên quan gì tới việc báo lỗi. Nhầm chỗ này là nhầm nguy
# hiểm, vì nó dẫn tới "đừng giảm kẻo mất cảnh báo". Cảnh báo đi bằng DM Lark từ
# `alerts.py`, hai đường song song và KHÔNG đụng Vercel:
#     alerts.check_all() trong vòng `run`  -> mỗi POLL_INTERVAL (5 phút)
#     Scheduled Task `V2_Alerts`           -> mỗi 15 phút, chạy TỪ NGOÀI
# `STATUS_PUSH_EVERY` chỉ quyết định trang dashboard cũ bao nhiêu.
#
# Vì sao 7200 mà không phải 21600 (chốt với chủ hệ thống 02/08): 6 tiếng thì
# lúc cần nhìn lại phải chờ quá lâu. 2 tiếng ra ~4-5 lần đẩy/ngày (máy ngủ nên
# duty cycle chỉ ~38%), tức ~9 ops/ngày kể cả các lần khởi động lại — 357 thao
# tác còn lại của chu kỳ đủ dùng ~40 ngày.
STATUS_PUSH_EVERY = _get_int("STATUS_PUSH_EVERY", 7200)


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
    # `utf-8-sig` vì cùng lý do với `_load_dotenv` — và ở đây hậu quả nặng hơn:
    # đây là CÔNG TẮC DỪNG KHẨN. `PAUSED` nằm ở dòng đầu file (vì bất kỳ lý do
    # gì) mà bị BOM nuốt thì công tắc im lặng không ăn, đúng lúc người ta đang
    # hoảng. Công tắc này đã một lần "chỉ là niềm tin" rồi (xem docstring).
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
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
