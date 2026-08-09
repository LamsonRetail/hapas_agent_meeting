"""Ước tính "còn bao lâu nữa thì có bản nguyên văn" — bằng số đo, không bằng cảm giác.

Vì sao có (user chốt 07/08/2026): khi người dùng xin bản nguyên văn của một cuộc
chưa phiên âm xong, câu trả lời cũ là *"Cuộc ngắn vài phút, cuộc dài thì lâu
hơn"*. Câu đó đúng mà vô dụng — người ta hỏi để biết có nên ngồi chờ hay đi làm
việc khác, và "vài phút" với "lâu hơn" không giúp họ quyết được gì.

Ước tính được là vì máy này chạy ổn định đến mức đáng ngạc nhiên. Đo 15 job gần
nhất trên chính DB này (07/08/2026), whisper CPU model `medium`:

    audio 406s → 61s   |  audio 4770s → 690s  |  audio 6079s → 898s
    tỉ lệ whisper/audio: 0,11 – 0,16;  gộp 15 cuộc = **0,14**

Tức phiên âm nhanh gấp ~7 lần thời gian thật, và biên độ hẹp. Một cuộc 1 tiếng
mất ~8,5 phút whisper. Đó là con số đáng nói ra.

Ba thứ CỘNG THÊM ngoài whisper, và vì sao không bỏ qua được:
  * tải bản ghi + tách audio bằng ffmpeg — tỉ lệ thuận độ dài;
  * gọi LLM dựng recap — `LLM_TIMEOUT` tới 240s;
  * chờ tới vòng quét kế tiếp — `POLL_INTERVAL` (mặc định 300s), vì
    `process_queue` KHÔNG cắt ngang job đang chạy.

Nguyên tắc viết ở đây: **thà nói dài hơn thực tế**. Người được hứa 10 phút mà
đợi 25 phút thì mất tin; người được hứa 20 phút mà nhận sau 12 phút thì hài
lòng. Nên mọi hằng số dưới đây làm tròn LÊN, và câu chữ luôn là "khoảng".
"""

from __future__ import annotations

import statistics
from typing import Any

from . import config, db

# Đo được 0,14 (15 job gần nhất, whisper CPU/medium). Lấy 0,16 làm mặc định khi
# chưa có số đo — mép trên của dải quan sát, đúng nguyên tắc nói dài hơn.
DEFAULT_RATIO = 0.16

# Chặn hai đầu cho số đo. Job 3 giây audio hay job hỏng giữa chừng cho ra tỉ lệ
# vô nghĩa (đo được cả 0,01 lẫn 5,84 trong lịch sử), và một con số như thế lọt
# vào ước tính thì bot hứa "1 phút" cho cuộc 2 tiếng.
RATIO_MIN, RATIO_MAX = 0.05, 1.20

# Số job gần nhất dùng để đo. Đủ nhiều để một cuộc bất thường không kéo lệch,
# đủ ít để phản ánh máy HIỆN TẠI (đổi model/CPU là tỉ lệ đổi hẳn).
RATIO_SAMPLE = 15

# Phần cứng của mỗi job, không phụ thuộc độ dài: gọi LLM recap + dựng file.
FIXED_OVERHEAD_S = 240

# Phần tỉ lệ thuận độ dài, ngoài whisper: tải mp4 về + ffmpeg tách audio.
IO_RATIO = 0.06

# Khi không biết cuộc dài bao nhiêu (job dựng từ search hit chưa tra được giờ),
# coi như một cuộc họp trung bình. Đo trên DB này: trung vị ~40 phút.
UNKNOWN_AUDIO_S = 2400


def whisper_ratio() -> float:
    """Tỉ lệ (giây whisper / giây audio) đo từ các job gần nhất.

    Đọc hỏng thì trả mặc định: một ước tính hơi lệch vẫn tốt hơn là làm hỏng
    câu trả lời người dùng đang chờ.
    """
    try:
        rows = db.conn().execute(
            "SELECT audio_seconds, whisper_seconds FROM jobs "
            "WHERE audio_seconds > 0 AND whisper_seconds > 0 "
            "ORDER BY COALESCE(transcribed_at, 0) DESC LIMIT ?",
            (RATIO_SAMPLE,)).fetchall()
    except Exception as exc:                       # noqa: BLE001 — xem docstring
        print(f"[eta] không đọc được số đo whisper: {exc}")
        return DEFAULT_RATIO
    vals = [r["whisper_seconds"] / r["audio_seconds"] for r in rows
            if (r["audio_seconds"] or 0) > 0
            and RATIO_MIN <= (r["whisper_seconds"] / r["audio_seconds"]) <= RATIO_MAX]
    if not vals:
        return DEFAULT_RATIO
    return float(statistics.median(vals))


def audio_seconds(row: dict[str, Any]) -> float:
    """Cuộc này dài bao nhiêu giây audio. 0 nếu chịu.

    Ba nguồn theo độ tin cậy giảm dần: số đo thật sau khi phiên âm, độ dài Lark
    ghi trong meta, rồi hiệu hai mốc giờ. Nguồn cuối yếu nhất — `end` của lịch
    là giờ ĐẶT PHÒNG, không phải giờ họp thật — nhưng vẫn hơn không có gì.
    """
    if (v := row.get("audio_seconds") or 0) > 0:
        return float(v)
    try:
        from . import jobstore
        meta = jobstore.meta_from_json(row["meta_json"])
    except Exception:                              # noqa: BLE001
        return 0.0
    if meta.duration_sec and meta.duration_sec > 0:
        # Lark ghi bằng mili-giây ở một số bản ghi; một "cuộc họp" 3.600.000
        # giây là 41 ngày nên ngưỡng này không thể đụng nhầm cuộc thật.
        d = float(meta.duration_sec)
        return d / 1000.0 if d > 86_400 else d
    if meta.start and meta.end and meta.end > meta.start:
        return float(meta.end - meta.start)
    return 0.0


def job_seconds(row: dict[str, Any], ratio: float | None = None) -> float:
    """Ước tính thời gian XỬ LÝ riêng job này, tính từ lúc nó được nhặt lên."""
    if ratio is None:
        ratio = whisper_ratio()
    audio = audio_seconds(row) or UNKNOWN_AUDIO_S
    return audio * (ratio + IO_RATIO) + FIXED_OVERHEAD_S


def estimate(minute_token: str) -> float:
    """Còn khoảng bao nhiêu GIÂY nữa thì cuộc này có bản nguyên văn.

    Gồm cả hàng chờ: `process_queue` không cắt ngang job đang chạy, nên một
    người xin lúc hệ thống đang dịch cuộc 2 tiếng vẫn phải chờ hết cuộc đó.
    Người xin được nâng lên `priority=2` nên chỉ đứng sau các job cực cao khác
    và job đang chạy dở — không phải sau cả hàng backlog.

    Trả 0 khi không tra được job (caller nói "chưa ước tính được", đừng bịa).
    """
    from . import jobstore
    try:
        rows = jobstore.active_by_priority()
    except Exception as exc:                       # noqa: BLE001
        print(f"[eta] không đọc được hàng đợi: {exc}")
        rows = []
    me = next((r for r in rows if r["minute_token"] == minute_token), None)
    if me is None:
        me = jobstore.get(minute_token)
        if not me:
            return 0.0
        rows = rows + [dict(me)]

    ratio = whisper_ratio()
    total = 0.0
    for r in rows:
        if r["minute_token"] == minute_token:
            total += job_seconds(r, ratio)
            break
        st = str(r.get("status") or "")
        if st in ("transcribing", "recapping"):
            # Đang chạy dở: không biết còn bao nhiêu, tính một nửa. Đoán quá tay
            # ở đây làm mọi ước tính phồng lên gấp đôi vì hầu như lúc nào cũng
            # có đúng một job đang chạy.
            total += job_seconds(r, ratio) * 0.5
        elif int(r.get("priority") or 1) >= 2:
            # Job cực cao khác đứng trước (có người khác cũng đang chờ).
            total += job_seconds(r, ratio)
        # priority 0/1 đứng SAU job cực cao -> không cộng.
    # Vòng quét ngủ `POLL_INTERVAL` giữa hai lần; trung bình phải chờ nửa nhịp
    # trước khi job được nhặt lên.
    return total + config.POLL_INTERVAL / 2


def phrase(seconds: float) -> str:
    """Số giây -> câu tiếng Việt để nói với người dùng. "" nếu không ước tính được.

    Làm tròn LÊN theo bậc thô dần: người nghe "khoảng 12 phút" tưởng đó là con
    số chính xác, còn "khoảng 15 phút" thì hiểu đúng là ước lượng. Độ chính xác
    giả tạo là một lời hứa mình không giữ được.
    """
    s = max(0.0, float(seconds or 0))
    if s <= 0:
        return ""
    minutes = s / 60.0
    if minutes <= 5:
        return "khoảng 5 phút"
    if minutes <= 20:
        return f"khoảng {int(minutes + 4.999) // 5 * 5} phút"
    if minutes <= 90:
        return f"khoảng {int(minutes + 9.999) // 10 * 10} phút"
    hours = minutes / 60.0
    if hours <= 1.5:
        return "khoảng 1 tiếng rưỡi"
    return f"khoảng {int(hours + 0.999)} tiếng"


def human(minute_token: str) -> str:
    """`estimate` + `phrase` — thứ caller thường cần. "" nếu chịu."""
    try:
        return phrase(estimate(minute_token))
    except Exception as exc:                       # noqa: BLE001 — chỉ là một câu ước tính
        print(f"[eta] ước tính {minute_token} hỏng (bỏ qua): {exc}")
        return ""
