"""Liên kết giữa các cuộc họp (V3 YC5) — hỏi một cuộc thì thấy các cuộc liên quan.

Ba loại, tính TĂNG DẦN khi một cuộc được phát / index (không quét lại cả kho):
  series   — cùng chuỗi họp định kỳ: tên chuẩn hoá (bỏ ngày, số, "buổi N")
             trùng nhau và cách nhau <= SERIES_MAX_DAYS.
  entity   — >= 2 thực thể chung: người phụ trách việc cần làm, thuật ngữ đã
             duyệt trong từ điển (glossary).
  semantic — vector tóm tắt (YC3) gần nhau >= LINK_MIN_SCORE.

LIÊN QUAN KHÔNG PHẢI GIẤY THÔNG HÀNH: mọi chỗ HIỆN liên kết đều lọc theo quyền
của người xem (`qa._may_see`). Không nêu số cuộc liên quan bị ẩn — cùng lý do
user bỏ `_hidden_note` 04/08/2026 (đếm cuộc bị ẩn là đo hoạt động công ty).
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import db, jobstore

SERIES_MAX_DAYS = 45
# ponytail: ngưỡng khởi điểm cho text-embedding-3-small; chỉnh bằng một cặp
# dương + một cặp âm thật sau khi chốt model (docs/V3_SPECS.md YC5).
LINK_MIN_SCORE = 0.75


def series_key(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"\b(buổi|buoi|tuần|tuan|week|w|số|so|kỳ|ky)\s*\d+\b", " ", t)
    t = re.sub(r"[\d/.\-|:#()\[\]]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _entities(token: str) -> set[str]:
    from . import confirm
    recap = confirm.current_recap(token)
    if not recap:
        return set()
    out = {a.owner.strip().lower() for a in recap.action_items if (a.owner or "").strip()}
    text = recap.to_markdown().lower()
    for r in db.conn().execute(
            "SELECT term FROM glossary_candidates WHERE status='approved'"):
        term = (r["term"] or "").strip().lower()
        if len(term) >= 3 and re.search(rf"\b{re.escape(term)}\b", text):
            out.add(term)
    return out


def _recap_vec(token: str):
    from . import semantic
    r = db.conn().execute("SELECT vec FROM embeddings WHERE minute_token=? AND "
                          "kind='recap' ORDER BY chunk_id LIMIT 1", (token,)).fetchone()
    return semantic._unpack(r["vec"]) if r else None


def _put(c, a: str, b: str, score: float, reason: str, now: int) -> None:
    a, b = sorted((a, b))
    c.execute("INSERT INTO meeting_links(token_a, token_b, score, reason, created_at)"
              " VALUES (?,?,?,?,?) ON CONFLICT(token_a, token_b, reason) DO UPDATE"
              " SET score=excluded.score", (a, b, round(score, 3), reason, now))


def update(token: str) -> int:
    """Tính liên kết của MỘT cuộc với mọi cuộc đã có nội dung. Trả số liên kết."""
    job = jobstore.get(token)
    if not job:
        return 0
    me = jobstore.meta_from_json(job["meta_json"])
    key, st = series_key(me.title), job.get("start_ts") or 0
    ents, vec = _entities(token), _recap_vec(token)
    now = int(time.time() * 1000)
    n = 0
    with db.tx() as c:
        c.execute("DELETE FROM meeting_links WHERE token_a=? OR token_b=?", (token, token))
        for other in jobstore.all_jobs():
            ot = other["minute_token"]
            if ot == token or other.get("status") not in ("held", "delivered"):
                continue
            try:
                title = jobstore.meta_from_json(other["meta_json"]).title
            except (ValueError, KeyError, TypeError):
                continue
            gap = abs((other.get("start_ts") or 0) - st) / 86_400_000
            if key and series_key(title) == key and gap <= SERIES_MAX_DAYS:
                _put(c, token, ot, 1.0, "series", now)
                n += 1
            shared = ents & _entities(ot)
            if len(shared) >= 2:
                _put(c, token, ot, float(len(shared)), "entity", now)
                n += 1
            if vec is not None:
                ov = _recap_vec(ot)
                if ov is not None:
                    score = sum(x * y for x, y in zip(vec, ov))
                    if score >= LINK_MIN_SCORE:
                        _put(c, token, ot, score, "semantic", now)
                        n += 1
    return n


def related(token: str) -> list[dict[str, Any]]:
    """Mọi cuộc liên quan (CHƯA lọc quyền — caller phải lọc), mạnh nhất trước."""
    rows = db.conn().execute(
        "SELECT CASE WHEN token_a=? THEN token_b ELSE token_a END AS other,"
        " reason, score FROM meeting_links WHERE token_a=? OR token_b=?",
        (token, token, token)).fetchall()
    best: dict[str, dict[str, Any]] = {}
    rank = {"series": 3, "entity": 2, "semantic": 1}
    for r in rows:
        cur = best.setdefault(r["other"], {"token": r["other"], "reasons": []})
        cur["reasons"].append(r["reason"])
    out = list(best.values())
    for o in out:
        job = jobstore.get(o["token"]) or {}
        o["start_ts"] = job.get("start_ts") or 0
        try:
            o["title"] = jobstore.meta_from_json(job["meta_json"]).title
        except (ValueError, KeyError, TypeError):
            o["title"] = o["token"]
    out.sort(key=lambda o: (-max(rank[x] for x in o["reasons"]), -o["start_ts"]))
    return out


_LABEL = {"series": "cùng chuỗi họp", "entity": "cùng người/thuật ngữ",
          "semantic": "cùng chủ đề"}


def _line(o: dict[str, Any]) -> str:
    when = (time.strftime("%d/%m/%Y", time.localtime(o["start_ts"] / 1000))
            if o["start_ts"] else "?")
    why = ", ".join(dict.fromkeys(_LABEL[r] for r in o["reasons"]))
    return f"{o['title']} — {when} ({why})"


def visible_related(who: dict[str, Any] | None, token: str,
                    limit: int = 5) -> list[dict[str, Any]]:
    from . import qa
    idx = qa.viewers_index()
    return [o for o in related(token) if qa._may_see(o["token"], who, idx)][:limit]


def block_for(who: dict[str, Any] | None, token: str) -> str:
    """Khối "Cuộc họp liên quan" cho `qa.get_meeting` — đã lọc theo người hỏi."""
    rel = visible_related(who, token)
    if not rel:
        return ""
    return "\n\n**Cuộc họp liên quan:**\n" + "\n".join(f"• {_line(o)}" for o in rel)


def related_lines(token: str) -> list[str]:
    """Cho file .md (đọc bởi MỌI người có quyền cuộc này): chỉ nêu cuộc liên
    quan mà TẤT CẢ họ đều xem được — không thì file lộ tên cuộc cho người ngoài."""
    from . import qa
    idx = qa.viewers_index()
    readers = idx.get(token, set())
    return [_line(o) for o in related(token)
            if readers and readers <= idx.get(o["token"], set())][:5]


def text_for(who: dict[str, Any] | None, query: str) -> str:
    """Tool MCP `related_meetings`."""
    from . import qa, sendfile
    if not who:
        return qa.NO_ASKER
    token, err = sendfile._resolve_token(who, query)
    if err:
        return err
    if not qa._may_see(token, who, qa.viewers_index()):
        return sendfile.NO_MEETING
    rel = visible_related(who, token, limit=10)
    if not rel:
        return "Chưa thấy cuộc họp nào liên quan (trong phạm vi bạn được xem)."
    return qa.append_agent_note(
        "Cuộc họp liên quan:\n" + "\n".join(f"• {_line(o)}" for o in rel),
        "\n".join(f"minute_token {o['token']}" for o in rel))
