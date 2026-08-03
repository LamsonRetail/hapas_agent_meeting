"""
Seam LLM (V2_LONGTERM §4.2).

    summarize(transcript, meta) -> Recap

Điều kiện để seam thật sự dùng được: KHÔNG để khái niệm riêng của provider rò
ra ngoài. Pipeline chỉ đưa Transcript + MeetingMeta vào, nhận Recap ra —
không truyền messages/tools/model từ pipeline. Đổi OpenAI -> Hermes -> thứ
khác chỉ là viết một implementation khác của hàm này.

HAI kiểu "không có recap", và chúng KHÁC NHAU (sửa 31/07/2026):

  - Không có `LLM_API_KEY`: đây là TRẠNG THÁI CẤU HÌNH, thử lại bao nhiêu lần
    cũng vậy -> trả Recap giữ chỗ, pipeline phát transcript trần. Như cũ.
  - Gọi LLM thất bại (timeout, 429, 5xx, provider từ chối): đây là lỗi HẠ TẦNG
    TẠM THỜI -> ném `RecapUnavailable` để caller hoãn và thử lại.

Trước 31/07/2026 cả hai cùng trả Recap giữ chỗ, nên một cú 429 lẻ là cuộc họp
đó VĨNH VIỄN không có tóm tắt: thẻ "Chưa sinh được recap" phát cho tất cả, job
thành `delivered`, không gì chạy lại. Bước phiên âm được bảo vệ rất kỹ
(`TranscribeUnavailable`) trong khi bước rẻ nhất để thử lại thì không có lưới —
đó là chỗ bất đối xứng cần vá.
"""

from __future__ import annotations

import json
import re

import httpx

from . import config
from .models import ActionItem, MeetingMeta, Recap, Transcript


class RecapUnavailable(RuntimeError):
    """Gọi LLM thất bại — lỗi hạ tầng tạm thời, KHÔNG phải lỗi của cuộc họp.

    Song song với `transcribe.TranscribeUnavailable` và caller xử y như vậy:
    không tiêu quota `MAX_ATTEMPTS`, giữ job ở `queued`, vòng sau dùng lại
    transcript đã lưu nên chỉ làm lại phần recap.
    """

# Thuật ngữ hay bị phiên âm sai (tiếng Việt xen tiếng Anh) -> nhắc LLM viết đúng.
GLOSSARY = [
    "MCP", "Claude", "Node.js", "Lark", "connector", "authorize",
    "terminal", "macOS", "Windows", "restart", "install", "token", "OAuth",
]

_SYSTEM = (
    "Bạn là trợ lý tóm tắt biên bản họp bằng tiếng Việt. Chỉ dùng thông tin "
    "trong bản ghi, tuyệt đối không bịa. Bản ghi do máy phiên âm nên có thể "
    "sai chính tả, nhất là thuật ngữ kỹ thuật; gặp từ nghe giống các thuật "
    "ngữ sau thì viết đúng lại: " + ", ".join(GLOSSARY) + "."
)

_INSTRUCT = (
    "Trả về DUY NHẤT một object JSON, không kèm giải thích, theo schema:\n"
    '{"summary": "tóm tắt nội dung chính, vài câu hoặc gạch đầu dòng",\n'
    ' "decisions": ["quyết định đã chốt", ...],\n'
    ' "action_items": [{"task":"việc cần làm","owner":"người phụ trách nếu '
    'có, else \\"\\"","due":"hạn nếu có, else \\"\\""}, ...]}\n'
    "Nếu bản ghi quá ngắn hoặc không rõ nội dung, để decisions/action_items "
    "rỗng và nói thẳng trong summary."
)


def _post(body: dict) -> str:
    """Một lời gọi chat/completions. Ném để caller quyết retry."""
    r = httpx.post(
        f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}",
                 "Content-Type": "application/json"},
        json=body, timeout=float(config.LLM_TIMEOUT),
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _call_llm(transcript_text: str, title: str) -> str | None:
    """Gọi LLM, trả về text JSON thô. None nếu KHÔNG có key.

    Ném `RecapUnavailable` khi có key mà gọi thất bại — xem docstring module:
    thiếu key và gọi-hỏng là hai chuyện khác nhau và phải xử khác nhau.

    Provider-neutral: chỉ dùng base_url + wire format OpenAI. Đổi GPT->Hermes
    chỉ là đổi LLM_BASE_URL (Hermes phơi endpoint OpenAI-compatible).
    """
    if not config.LLM_API_KEY:
        # Đừng im lặng: thiếu key và gọi-lỗi cho ra cùng một recap rỗng, người
        # vận hành phải phân biệt được ngay trên log.
        print("[summarize] KHÔNG có LLM_API_KEY -> gửi transcript trần, "
              "không có recap. Đặt LLM_API_KEY trong v2/.env.")
        return None

    user = (f"Tiêu đề cuộc họp: {title}\n\n{_INSTRUCT}\n\n"
            f"--- BẢN GHI ---\n{transcript_text[:60000]}")
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    if config.LLM_JSON_MODE:
        body["response_format"] = {"type": "json_object"}

    try:
        return _post(body)
    except httpx.HTTPStatusError as exc:
        # Provider (vd Hermes) không nhận response_format -> thử lại không kèm.
        if config.LLM_JSON_MODE and exc.response.status_code in (400, 404, 422):
            print("[summarize] provider từ chối response_format, thử lại "
                  "không kèm (đặt LLM_JSON_MODE=0 để bỏ hẳn).")
            body.pop("response_format", None)
            try:
                return _post(body)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc2:
                raise RecapUnavailable(str(exc2)) from exc2
        raise RecapUnavailable(str(exc)) from exc
    except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
        raise RecapUnavailable(str(exc)) from exc


def _json_block(raw: str) -> str | None:
    """Rút khối JSON ra khỏi câu trả lời có kèm văn xuôi / ```json.

    Vì sao cần: gọi thẳng API (json_object mode) thì `raw` là JSON thuần, nhưng
    đường Hermes là một AGENT — nó hay mở đầu bằng một câu rồi bọc JSON trong
    ```json. Thiếu bước này thì `_parse` rơi về nhánh "không phải JSON" và
    LẶNG LẼ trả về Recap chỉ có summary, mất hết decisions/action_items.
    """
    s = raw.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    # ```json … ```  hoặc  ``` … ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
    if fence:
        return fence.group(1)
    # Không có fence: lấy từ '{' đầu tới '}' cuối.
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        return s[i:j + 1]
    return None


def _parse(raw: str) -> Recap:
    """Bóc JSON của LLM thành Recap. Không parse được thì giữ nguyên văn."""
    block = _json_block(raw)
    try:
        d = json.loads(block) if block is not None else json.loads(raw)
    except json.JSONDecodeError:
        return Recap(summary=raw.strip(), raw=raw)
    if not isinstance(d, dict):
        return Recap(summary=raw.strip(), raw=raw)

    items = []
    for a in d.get("action_items", []) or []:
        if isinstance(a, dict):
            items.append(ActionItem(task=a.get("task", ""),
                                    owner=a.get("owner", ""),
                                    due=a.get("due", "")))
        elif isinstance(a, str):
            items.append(ActionItem(task=a))
    return Recap(
        summary=(d.get("summary") or "").strip(),
        decisions=[str(x) for x in (d.get("decisions") or [])],
        action_items=items,
        raw=raw,
    )


# Câu mở đầu của mọi recap GIỮ CHỖ. Là hằng số vì nó không chỉ để người đọc:
# `orchestrator._backfill_recaps` dùng đúng chuỗi này để tìm lại những cuộc họp
# đã phát với tóm tắt rỗng và làm lại chúng khi LLM sống lại. Đổi câu chữ ở đây
# mà quên chỗ kia là những cuộc họp đó im lặng không bao giờ được vá.
PLACEHOLDER_PREFIX = "Chưa sinh được recap"


def placeholder(why: str) -> Recap:
    """Recap giữ chỗ khi chắc chắn không có tóm tắt. Nói RÕ nguyên nhân nào —
    đừng bắt người đọc thẻ đoán vì sao thẻ trống."""
    return Recap(summary=f"{PLACEHOLDER_PREFIX} — {why}. "
                         "Bản ghi đầy đủ ở file đính kèm.")


def is_placeholder(recap: Recap | None) -> bool:
    """Recap này là bản giữ chỗ (chưa có tóm tắt thật) hay không.

    Coi recap RỖNG cũng là giữ chỗ: job cũ trước 31/07/2026 lưu summary trống
    thay vì câu giữ chỗ, và chúng cần được vá y hệt.
    """
    if recap is None:
        return True
    if (recap.summary or "").strip().startswith(PLACEHOLDER_PREFIX):
        return True
    return not ((recap.summary or "").strip() or recap.decisions
                or recap.action_items)


def summarize(transcript: Transcript, meta: MeetingMeta) -> Recap:
    """Sinh recap cấu trúc từ transcript. Provider-neutral.

    Ném `RecapUnavailable` nếu gọi LLM thất bại (caller hoãn rồi thử lại).
    Thiếu `LLM_API_KEY` thì KHÔNG ném: đó là cấu hình, thử lại không đổi gì.
    """
    raw = _call_llm(transcript.text, meta.title)
    if raw is None:
        return placeholder("chưa đặt LLM_API_KEY trong v2/.env")
    return _parse(raw)


def recap_from_text(text: str, title: str) -> Recap:
    """Tóm tắt một khối text bất kỳ (vd bản Minute của Lark) -> Recap.

    Dùng cho tin báo tự động khi họp xong (mô hình kéo, 03/08/2026): tóm tắt
    NỘI DUNG MINUTE LARK để gửi kèm NGAY, không chờ whisper.

    KHÁC `summarize`: KHÔNG BAO GIỜ ném. Tin báo phải gửi được cả khi LLM lỗi —
    hỏng thì trả placeholder, tin vẫn có Minute link + lời mời transcript.
    """
    text = (text or "").strip()
    if not text:
        return placeholder("bản Minute của Lark chưa có nội dung")
    try:
        raw = _call_llm(text, title)
    except RecapUnavailable as exc:
        return placeholder(f"LLM tạm thời không tóm tắt được ({exc})")
    if raw is None:
        return placeholder("chưa đặt LLM_API_KEY trong v2/.env")
    return _parse(raw)
