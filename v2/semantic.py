"""Tìm cuộc họp theo NGỮ NGHĨA (V3 YC3) — qua LiteLLM của công ty.

    backfill(): mỗi vòng run, index (embed) vài cuộc đã phát mà vector cũ/thiếu
    search(who, q): LỌC QUYỀN TRƯỚC -> embed câu hỏi -> xếp hạng -> trả kết quả
                    kèm nhãn nguồn quyền (bạn dự / quyền quản lý nhánh …)

Seam giống `summarize`: chỉ base_url + wire format OpenAI (`/embeddings`), đổi
model = đổi `EMBED_MODEL`. Vector nằm trong `state.db` (bảng `embeddings`).

Lọc quyền TRƯỚC khi xếp hạng, cùng lý do `qa.search_meetings`: xếp hạng rồi mới
lọc thì số lượng/điểm số tự nó thành một phép dò nội dung cuộc họp của người khác.

LiteLLM chết hoặc chưa có key -> rơi về tìm theo từ khoá (`qa.search_meetings`),
nói rõ là đang rơi về. Mất tìm ngữ nghĩa không được là mất tìm kiếm.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from array import array
from typing import Any

import httpx

from . import config, db, jobstore, qa, summarize

CHUNK, OVERLAP = 1500, 200
# ponytail: tích vô hướng thuần Python — đủ nhanh tới ~10k đoạn; vượt nữa thì
# numpy hoặc sqlite-vec.
MIN_SCORE = 0.25          # dưới ngưỡng này coi như không liên quan (chỉnh theo model)


class EmbedUnavailable(RuntimeError):
    """Không gọi được LiteLLM — lỗi hạ tầng hoặc chưa cấu hình."""


def enabled() -> bool:
    return bool(config.LITELLM_API_KEY)


def embed(texts: list[str], model: str = "") -> list[list[float]]:
    if not enabled():
        raise EmbedUnavailable("chưa đặt LITELLM_API_KEY")
    try:
        r = httpx.post(f"{config.LITELLM_BASE_URL.rstrip('/')}/embeddings",
                       headers={"Authorization": f"Bearer {config.LITELLM_API_KEY}"},
                       json={"model": model or config.EMBED_MODEL, "input": texts},
                       timeout=60.0)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise EmbedUnavailable(str(exc)) from exc


def _pack(v: list[float]) -> bytes:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array("f", [x / n for x in v]).tobytes()


def _unpack(b: bytes) -> array:
    a = array("f")
    a.frombytes(b)
    return a


# ---------------------------------------------------------------- index


def _transcript_text(job: dict[str, Any]) -> str:
    p = job.get("transcript_path") or ""
    if p:
        try:
            d = json.loads(open(p, encoding="utf-8").read())
            return " ".join((s.get("text") or "").strip() for s in d.get("segments", []))
        except (OSError, ValueError):
            pass
    try:
        from . import larktext
        lp = larktext.path_of(job["minute_token"])
        return lp.read_text(encoding="utf-8") if lp.exists() else ""
    except Exception:                          # noqa: BLE001
        return ""


def chunks(token: str) -> list[tuple[str, str]]:
    from . import confirm
    job = jobstore.get(token)
    if not job:
        return []
    title = jobstore.meta_from_json(job["meta_json"]).title
    out: list[tuple[str, str]] = []
    recap = confirm.current_recap(token)
    if recap and not summarize.is_placeholder(recap):
        out.append(("recap", f"{title}\n{recap.to_markdown()}"))
    text = _transcript_text(job)
    for i in range(0, len(text), CHUNK - OVERLAP):
        piece = text[i:i + CHUNK].strip()
        if piece:
            out.append(("transcript", f"{title}: {piece}"))
    return out


def _src(job: dict[str, Any], model: str) -> str:
    """Dấu vân tay nguồn — đổi recap/transcript/model là index lại."""
    raw = f"{job.get('recap_json')}|{job.get('transcript_path')}|{job.get('lark_chars')}|{model}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def index(token: str, model: str = "") -> int:
    model = model or config.EMBED_MODEL
    parts = chunks(token)
    if not parts:
        return 0
    vecs: list[list[float]] = []
    for i in range(0, len(parts), 64):
        vecs += embed([t for _, t in parts[i:i + 64]], model)
    src = _src(jobstore.get(token) or {}, model)
    now = int(time.time() * 1000)
    with db.tx() as c:
        c.execute("DELETE FROM embeddings WHERE minute_token=?", (token,))
        c.executemany(
            "INSERT INTO embeddings(minute_token, chunk_id, kind, text, vec, model,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            [(token, i, k, t, _pack(v), f"{model}#{src}", now)
             for i, ((k, t), v) in enumerate(zip(parts, vecs))])
    try:
        from . import links
        links.update(token)                   # V3 YC5: vector mới -> liên kết "cùng chủ đề"
    except Exception as exc:                  # noqa: BLE001
        print(f"[links] {token} tính liên kết hỏng (bỏ qua): {exc}")
    return len(parts)


def stale(limit: int) -> list[str]:
    """Cuộc đã có nội dung (held/delivered) mà vector thiếu hoặc cũ. Mới nhất trước."""
    have = {r["minute_token"]: r["model"] for r in db.conn().execute(
        "SELECT minute_token, MAX(model) AS model FROM embeddings GROUP BY minute_token")}
    out = []
    for job in sorted(jobstore.all_jobs(), key=lambda j: -(j.get("start_ts") or 0)):
        if job.get("status") not in ("held", "delivered"):
            continue
        want = f"{config.EMBED_MODEL}#{_src(job, config.EMBED_MODEL)}"
        if have.get(job["minute_token"]) != want:
            out.append(job["minute_token"])
            if len(out) >= limit:
                break
    return out


def backfill(limit: int = 0) -> int:
    """Gọi mỗi vòng run. Hỏng LiteLLM thì dừng lượt, vòng sau thử lại."""
    if not enabled():
        return 0
    done = 0
    for token in stale(limit or config.EMBED_PER_ROUND):
        try:
            index(token)
            done += 1
        except EmbedUnavailable as exc:
            print(f"[semantic] LiteLLM chưa gọi được, để vòng sau: {exc}")
            break
    return done


# ---------------------------------------------------------------- search


def _day_ms(s: str, end: bool = False) -> int:
    if not s:
        return 0
    try:
        t = time.mktime(time.strptime(s, "%Y-%m-%d"))
    except ValueError:
        return 0
    return int((t + (86400 if end else 0)) * 1000)


def _why(token: str, who: dict[str, Any]) -> str:
    """Vì sao người hỏi thấy cuộc này — minh bạch + audit tại chỗ."""
    ids = qa._ids_of(who)
    try:
        meta = jobstore.meta_from_json((jobstore.get(token) or {})["meta_json"])
    except Exception:                          # noqa: BLE001
        meta = None
    if meta and meta.owner_open_id in ids:
        return "bạn chủ trì"
    if meta and any({a.open_id, a.union_id} & ids for a in meta.attendees):
        return "bạn dự"
    for g in db.conn().execute("SELECT open_id, union_id, source FROM note_grants"
                               " WHERE minute_token=?", (token,)):
        if {g["open_id"], g["union_id"]} & ids:
            src = g["source"] or ""
            if src.startswith("chain:"):
                return f"quyền quản lý, nhánh {src[6:]}"
            if src.startswith("backfill"):
                return "quyền quản lý (cấp lùi)"
    return "được chia sẻ"


def _log(who: dict[str, Any] | None, tool: str, query: str, n: int) -> None:
    try:
        with db.tx() as c:
            c.execute("INSERT INTO query_log(ts, asker, tool, query, n_hits)"
                      " VALUES (?,?,?,?,?)",
                      (int(time.time() * 1000), (who or {}).get("union_id") or "",
                       tool, query[:500], n))
    except Exception as exc:                   # noqa: BLE001 — nhật ký là việc phụ
        print(f"[semantic] ghi query_log hỏng: {exc}")


def _fallback(who: dict[str, Any], query: str, why: str) -> str:
    _log(who, "semantic_search:fallback", query, -1)
    try:
        found = qa.search_meetings(who, query)
    except qa.QAError as exc:
        found = f"Chưa tìm được: {exc}"
    return qa.append_agent_note(found,
                                f"Tìm NGỮ NGHĨA chưa dùng được ({why}) — đây là kết "
                                "quả tìm theo TỪ KHOÁ. Nếu rỗng, thử lại bằng từ "
                                "khoá ngắn hơn (tên dự án, tên người).")


def search(who: dict[str, Any] | None, query: str, *, top_k: int = 8,
           since: str = "", until: str = "", model: str = "") -> str:
    if not who:
        return qa.NO_ASKER
    query = (query or "").strip()
    if not query:
        return "Cần câu hỏi hoặc chủ đề để tìm."
    model = model or config.EMBED_MODEL
    idx = qa.viewers_index()
    lo, hi = _day_ms(since), _day_ms(until, end=True)
    visible: dict[str, dict] = {}
    for job in jobstore.all_jobs():
        t = job["minute_token"]
        if job.get("status") not in ("held", "delivered") or not qa._may_see(t, who, idx):
            continue
        st = job.get("start_ts") or 0
        if (lo and st < lo) or (hi and st > hi):
            continue
        visible[t] = job
    if not visible:
        _log(who, "semantic_search", query, 0)
        return "Không có cuộc họp nào trong phạm vi bạn được xem."
    if not enabled():
        return _fallback(who, query, "chưa cấu hình LiteLLM")
    try:
        qv = _unpack(_pack(embed([query], model)[0]))
    except EmbedUnavailable as exc:
        return _fallback(who, query, f"LiteLLM lỗi: {exc}")
    marks = ",".join("?" * len(visible))
    best: dict[str, tuple[float, str]] = {}
    for r in db.conn().execute(
            f"SELECT minute_token, text, vec FROM embeddings WHERE minute_token IN ({marks})"
            " AND model LIKE ?", (*visible, f"{model}#%")):
        v = _unpack(r["vec"])
        score = sum(a * b for a, b in zip(qv, v))
        if score > best.get(r["minute_token"], (-1.0, ""))[0]:
            best[r["minute_token"]] = (score, r["text"])
    top = sorted(((s, t, x) for t, (s, x) in best.items() if s >= MIN_SCORE),
                 reverse=True)[:max(1, top_k)]
    _log(who, "semantic_search", query, len(top))
    if not top:
        if not best:
            return _fallback(who, query, "các cuộc họp chưa được index xong")
        return f"Không thấy cuộc họp nào liên quan tới «{query}» trong phạm vi bạn được xem."
    lines = [f"{len(top)} cuộc họp liên quan tới «{query}» (xếp theo mức liên quan):"]
    notes = []
    for score, token, text in top:
        job = visible[token]
        meta = jobstore.meta_from_json(job["meta_json"])
        when = (time.strftime("%d/%m/%Y", time.localtime(job["start_ts"] / 1000))
                if job.get("start_ts") else "?")
        lines.append(f"\n• **{qa._title(meta.title)}** — {when} ({_why(token, who)})\n"
                     f"  Đoạn khớp: {text[:400].strip()}")
        notes.append(f"minute_token {token}: điểm {score:.2f}")
    return qa.append_agent_note("\n".join(lines), "\n".join(notes))
