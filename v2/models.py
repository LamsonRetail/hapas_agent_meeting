"""
Kiểu dữ liệu dùng chung, không phụ thuộc nhà cung cấp nào.

Đây là "ngôn ngữ chung" chảy qua các seam (V2_LONGTERM §4):
    transcribe() -> Transcript
    summarize(Transcript, MeetingMeta) -> Recap

Không để khái niệm riêng của OpenAI / Whisper / Lark rò vào đây. Nhờ vậy
đổi engine phiên âm hay đổi LLM chỉ là viết một implementation khác trả về
đúng các dataclass này.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


# --------------------------------------------------------------- transcript


@dataclass
class Segment:
    """Một đoạn nói có mốc thời gian. Đơn vị: giây."""
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    """Kết quả phiên âm.

    Lưu dạng có `segments` (V2_LONGTERM §3.4): sau này làm được nhảy tới
    đoạn nói, tìm kiếm có ngữ cảnh, diarization, chấm điểm engine mới. Lưu
    text thuần thì mất vĩnh viễn vì bản ghi gốc trên Lark sẽ bị dọn.
    """
    minute_token: str
    lang: str
    duration: float                       # tổng độ dài audio, giây
    engine: str                           # vd "faster-whisper/large-v3"
    segments: list[Segment] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())

    @property
    def text(self) -> str:
        """Toàn văn ghép từ các segment, mỗi đoạn một dòng."""
        return "\n".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def to_json(self) -> dict[str, Any]:
        return {
            "minute_token": self.minute_token,
            "lang": self.lang,
            "duration": self.duration,
            "engine": self.engine,
            "created_at": self.created_at,
            "segments": [asdict(s) for s in self.segments],
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            minute_token=d["minute_token"],
            lang=d.get("lang", "vi"),
            duration=d.get("duration", 0.0),
            engine=d.get("engine", "unknown"),
            created_at=d.get("created_at", time.time()),
            segments=[Segment(**s) for s in d.get("segments", [])],
        )

    @classmethod
    def from_plain_text(cls, minute_token: str, text: str, *,
                        lang: str = "vi", engine: str = "unknown",
                        duration: float = 0.0) -> "Transcript":
        """Bọc transcript text thuần (vd nguồn Lark) thành Transcript.

        Không có mốc thời gian từng đoạn thì để cả bài làm một segment.
        """
        return cls(
            minute_token=minute_token, lang=lang, duration=duration,
            engine=engine, segments=[Segment(0.0, duration, text)],
        )


# ------------------------------------------------------------------ meeting


@dataclass
class Attendee:
    open_id: str = ""
    union_id: str = ""
    name: str = ""


@dataclass
class MeetingMeta:
    """Ngữ cảnh cuộc họp, dựng từ minute + calendar event."""
    minute_token: str
    title: str = "(không tiêu đề)"
    start: float | None = None            # epoch seconds, UTC
    end: float | None = None
    duration_sec: int | None = None
    owner_open_id: str = ""
    owner_name: str = ""
    app_link: str = ""
    attendees: list[Attendee] = field(default_factory=list)
    # Nguồn suy ra người dự: "calendar:<tên>" hoặc "fallback:owner"...
    participants_source: str = ""
    # chat_id của các NHÓM CHAT được mời thẳng trên lịch (Lark trả về dưới dạng
    # một mục `{"type": "chat", "chat_id": ...}` trong `event_attendees`).
    #
    # Giữ lại chứ không dùng-xong-vứt (26/08/2026): đây là bằng chứng DUY NHẤT,
    # do Lark cấp, cho câu hỏi "cuộc họp này thuộc về nhóm nào". Trước đó
    # `_chat_invitees` đọc `chat_id` để giãn ra người rồi bỏ đi, nên không cách
    # nào trả lời được câu đó về sau.
    #
    # Ghi CẢ KHI không giãn được người (thiếu quyền, nhóm quá đông, bot chưa
    # vào nhóm): việc "nhóm nào được mời" và việc "giãn ra được ai" là hai
    # chuyện khác nhau, và trộn chúng đúng là cách làm mất dữ liệu lần trước.
    invited_chats: list[str] = field(default_factory=list)

    @property
    def invitee_count(self) -> int:
        return len(self.attendees)


# ------------------------------------------------------------------- recap


@dataclass
class ActionItem:
    task: str
    owner: str = ""                       # tên người phụ trách nếu đoán được
    due: str = ""                         # hạn nếu có, dạng text tự do


@dataclass
class Recap:
    """Kết quả tóm tắt. Cấu trúc, không phải một khối text.

    summarize() trả về đúng cái này. Không nhận/không trả bất cứ thứ gì
    đặc thù của một provider (không messages, không tools, không model).
    """
    summary: str
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    # Nếu LLM không dựng được cấu trúc, giữ nguyên văn recap ở đây.
    raw: str = ""

    # Ký tự đầu dòng cho danh sách. `•` là KÝ TỰ THẬT, không phải cú pháp
    # markdown — và đó là lý do dùng nó (sửa 05/08/2026).
    #
    # Chuỗi này đi vào `cards.recap_card` / `cards.minute_notice_card`, tức vào
    # `lark_md`. Mà `lark_md` chỉ hiểu **đậm** và [nhãn](url) — KHÔNG có danh
    # sách, không có heading, không có nghiêng. Nên `- item` hiện ra đúng một
    # dấu gạch ngang, và `_Tên_` hiện ra đúng hai dấu gạch dưới. Người dùng đã
    # báo "nhiều chỗ chả thấy markdown gì cả" — đây là một trong các chỗ đó.
    #
    # `•` thì hiện thành dấu chấm tròn ở MỌI đường: lark_md, tin text trần,
    # và bản `post` mà Hermes gửi. Một ký tự chạy đúng ở cả ba chỗ đáng giá hơn
    # một cú pháp chỉ đúng ở một chỗ. Cùng ký tự với `bitable._bullets`, để nội
    # dung trên Base và nội dung trong thẻ trông giống nhau.
    BULLET = "• "

    def to_markdown(self) -> str:
        parts = [self.summary.strip()]
        if self.decisions:
            parts.append("\n**Quyết định đã chốt**")
            parts.extend(f"{self.BULLET}{d}" for d in self.decisions)
        if self.action_items:
            parts.append("\n**Việc cần làm**")
            for a in self.action_items:
                line = f"{self.BULLET}{a.task}"
                if a.owner:
                    # KHÔNG `_nghiêng_`: lark_md không có nghiêng, người dùng
                    # nhận về đúng hai dấu gạch dưới quanh tên đồng nghiệp.
                    line += f" — {a.owner}"
                if a.due:
                    line += f" (hạn {a.due})"
                parts.append(line)
        return "\n".join(parts).strip()
