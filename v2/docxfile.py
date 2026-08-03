"""
Xuất transcript ra file `.docx` — KHÔNG dùng thư viện ngoài.

Vì sao tự viết thay vì `pip install python-docx` (03/08/2026):

  1. **Thêm dependency là thêm một chỗ hỏng lúc chạy thật.** V2 chạy từ
     Scheduled Task bằng một interpreter mà không ai nhìn tận mắt mỗi ngày; cài
     nhầm vào python khác là biên bản gửi đi thiếu file, và hỏng ở đúng lúc
     không ai ngồi đó. Repo này đã cố ý tự viết cả giao thức MCP vì cùng lý do
     (`mcp_server.py`).
  2. Nội dung cần xuất là **đoạn văn phẳng** — tiêu đề, vài dòng thông tin, mỗi
     câu nói một dòng. Đó là phần nhỏ và ổn định nhất của OOXML.

Một file `.docx` là một file ZIP có ba thành phần bắt buộc:

    [Content_Types].xml     khai kiểu cho từng phần
    _rels/.rels             trỏ tới phần thân
    word/document.xml       thân bài

Không cần `styles.xml`: mọi định dạng ở đây đặt thẳng vào run (`w:rPr`), nên
không có style nào để tham chiếu. Thiếu `styles.xml` mà lại tham chiếu tên style
thì Word mở ra là chữ trần — im lặng, nên đừng thêm `w:pStyle` vào đây.

⚠️ `w:sz` là **nửa point**: `w:sz w:val="32"` = 16pt. Đặt 16 tưởng là 16pt thì ra
chữ 8pt, đọc không nổi mà không có lỗi nào báo.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Iterable

from .models import MeetingMeta, Transcript

# Ký tự XML 1.0 KHÔNG nhận. Whisper trả text đã sạch, nhưng tên cuộc họp thì
# đến từ Lark và đã thấy `&amp;` lẫn trong đó — một ký tự điều khiển lọt vào là
# Word báo "file bị lỗi" và không mở, chứ không bỏ qua ký tự đó.
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _esc(s: str) -> str:
    s = _BAD_XML.sub("", s or "")
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _run(text: str, *, bold: bool = False, size: int = 22,
         color: str = "", italic: bool = False) -> str:
    rpr = ""
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    rpr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    return (f"<w:r><w:rPr>{rpr}</w:rPr>"
            f'<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>')


def _para(runs: Iterable[str], *, after: int = 120) -> str:
    return (f'<w:p><w:pPr><w:spacing w:after="{after}"/></w:pPr>'
            + "".join(runs) + "</w:p>")


def _mmss(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60:02d}:{s % 60:02d}"


def _document_xml(t: Transcript, meta: MeetingMeta) -> str:
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=7))
    when = (datetime.fromtimestamp(meta.start, tz).strftime("%H:%M %d/%m/%Y")
            if meta.start else "(không rõ giờ)")

    body = [
        _para([_run(meta.title or "(không tiêu đề)", bold=True, size=32)],
              after=60),
        _para([_run(f"Nguyên văn cuộc họp · {when} · {t.duration:.0f} giây",
                    size=20, color="666666")], after=40),
        # Câu này phải nằm TRONG file, không chỉ trong tin nhắn gửi kèm: file rời
        # khỏi chat rất nhanh (chuyển tiếp, tải về, dán vào báo cáo) và lúc đó
        # không còn gì nói cho người đọc biết đây là bản máy nghe.
        _para([_run("Bản do máy (Whisper) phiên âm tự động — có lỗi nghe nhầm, "
                    "nhất là tên riêng và thuật ngữ. Đối chiếu bản ghi gốc "
                    "trước khi trích dẫn chính thức.",
                    size=18, color="999999", italic=True)], after=240),
    ]
    for s in t.segments:
        text = (s.text or "").strip()
        if not text:
            continue
        body.append(_para([
            _run(f"[{_mmss(s.start)}]  ", bold=True, size=20, color="7F7F7F"),
            _run(text, size=22),
        ]))
    if meta.app_link:
        body.append(_para([_run(f"Bản ghi gốc trên Lark Minutes: {meta.app_link}",
                                size=18, color="666666")], after=0))

    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{_W_NS}"><w:body>'
            + "".join(body)
            + "<w:sectPr/></w:body></w:document>")


def write_docx(t: Transcript, meta: MeetingMeta, dest: Path) -> Path:
    """Ghi transcript ra `dest` (.docx). Trả lại `dest`.

    Ghi qua file tạm rồi `replace`: tiến trình chết giữa lúc ghi mà để lại một
    ZIP cụt thì lần sau `dest.exists()` vẫn True (bitable._tracking_fields chỉ
    kiểm tồn tại) và ta đính một file hỏng vào Base.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", _document_xml(t, meta))
    tmp.replace(dest)
    return dest
