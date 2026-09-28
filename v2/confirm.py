"""Xác nhận biên bản của CHỦ cuộc họp trước khi báo người dự (V3 YC1).

    họp xong -> gate(): gửi thẻ duyệt cho chủ, KHÔNG báo người dự   (pending)
             -> chủ nhắn "duyệt X"  -> approve(): phát cho người dự  (confirmed)
             -> chủ nhắn "sửa X: …" -> edit(): LLM áp sửa, chờ duyệt lại
             -> quá CONFIRM_TIMEOUT_HOURS -> tick(): tự phát kèm nhãn
                "chưa được chủ trì review"                            (auto_published)
             -> chủ duyệt/sửa MUỘN vẫn được; nội dung đổi thì người dự nhận
                một thẻ "Đã hiệu chỉnh" (đúng một lần mỗi bản).

Quyết định của chủ hệ thống (16/09/2026, docs/V3_SPECS.md §C.1). Trạng thái nằm
ở bảng `confirmations`, TÁCH khỏi `jobs.status`: whisper vẫn chạy nền theo máy
trạng thái cũ, file này chỉ lo "người dự được báo chưa, chủ duyệt chưa".

Vì sao lần này không chết như cửa duyệt cũ (bỏ 30/07/2026 vì record kẹt
`draft` vĩnh viễn): có nhắc, có HẠN tự phát, và chủ duyệt muộn vẫn được.

Chủ CHƯA enroll thì không ai duyệt được (bot chỉ nhắn người đã cấp quyền) ->
phát ngay kèm nhãn "chưa review"; chủ enroll về sau vẫn duyệt/sửa được.

RÀNG BUỘC (cưỡng chế bằng code, như `tasks.py`): chỉ CHỦ của đúng cuộc họp —
`owner_open_id` trong `jobs.meta_json` — mới duyệt/sửa được. Chỉ sửa RECAP;
transcript nguyên văn là bằng chứng, không bao giờ bị sửa.
"""

from __future__ import annotations

import difflib
import json
import time
from typing import Any

from . import cards, config, db, jobstore, lark_api, summarize, tokenstore
from .models import ActionItem, MeetingMeta, Recap


def _now_ms() -> int:
    return int(time.time() * 1000)


def recap_to_json(recap: Recap) -> str:
    return json.dumps({"summary": recap.summary, "decisions": recap.decisions,
                       "action_items": [a.__dict__ for a in recap.action_items]},
                      ensure_ascii=False)


def recap_from_json(s: str | None) -> Recap | None:
    if not s:
        return None
    try:
        d = json.loads(s)
        return Recap(summary=d.get("summary", ""), decisions=d.get("decisions", []),
                     action_items=[ActionItem(**a) for a in d.get("action_items", [])])
    except (ValueError, TypeError, KeyError):
        return None


def get(token: str) -> dict[str, Any] | None:
    r = db.conn().execute("SELECT * FROM confirmations WHERE minute_token=?",
                          (token,)).fetchone()
    return dict(r) if r else None


def _update(token: str, **cols: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        c.execute(f"UPDATE confirmations SET {sets} WHERE minute_token=?",
                  (*cols.values(), token))


def owner_union_id(meta: MeetingMeta) -> str:
    """union_id của chủ cuộc họp NẾU chủ đã enroll (active), không thì ""."""
    if not meta.owner_open_id:
        return ""
    for u in tokenstore.list_users(active_only=True):
        if u["open_id"] == meta.owner_open_id:
            return u.get("union_id") or ""
    return ""


def _is_owner(who: dict[str, Any] | None, meta: MeetingMeta) -> bool:
    if not who or not meta.owner_open_id:
        return False
    if who.get("open_id") and who["open_id"] == meta.owner_open_id:
        return True
    uid = owner_union_id(meta)
    return bool(uid and who.get("union_id") == uid)


def locked(token: str) -> bool:
    """Recap đã thuộc về CHỦ (đã sửa hoặc đã duyệt) — bản whisper về sau không
    được ghi đè lên. `pipeline.save_recap` hỏi hàm này trước khi ghi."""
    r = get(token)
    return bool(r and (r["edits"] or r["state"] == "confirmed"))


def current_recap(token: str) -> Recap | None:
    return recap_from_json((jobstore.get(token) or {}).get("recap_json"))


def _diff_chars(a: Recap | None, b: Recap | None) -> int:
    x = a.to_markdown() if a else ""
    y = b.to_markdown() if b else ""
    return round((1 - difflib.SequenceMatcher(None, x, y).ratio()) * max(len(x), len(y)))


def _send_owner(meta: MeetingMeta, uid: str, recap: Recap, version: int, *,
                updated: bool = False, reminder: bool = False) -> bool:
    card = cards.confirm_card(meta, recap, updated=updated, reminder=reminder)
    tag = "r" if reminder else "v"
    try:
        lark_api.im_send_card(uid, card, id_type="union_id",
                              uuid_key=f"confirm{tag}{version}-{meta.minute_token}"[:50])
        return True
    except lark_api.LarkError as exc:
        print(f"[confirm] gửi thẻ duyệt cho chủ {meta.minute_token} hỏng: {exc}")
        return False


def _broadcast(meta: MeetingMeta, recap: Recap, *, unreviewed: bool = False,
               revised: bool = False, version: int = 1) -> int:
    from . import orchestrator                 # import vòng: orchestrator import file này
    exclude = {owner_union_id(meta)} - {""}
    return orchestrator._broadcast_notice(meta, recap, unreviewed=unreviewed,
                                          revised=revised, exclude=exclude,
                                          tag=f"v{version}" if version > 1 else "")


def _after_final(token: str, meta: MeetingMeta, recap: Recap) -> None:
    """Nội dung chốt đổi -> sửa mọi chỗ đọc lại được (Base). Việc phụ, không ném."""
    try:
        from . import bitable
        if (jobstore.get(token) or {}).get("bitable_record_id"):
            bitable.update_recap(token, recap)
    except Exception as exc:                  # noqa: BLE001
        print(f"[confirm] {token} cập nhật Base hỏng (bỏ qua): {exc}")
    try:
        from . import notes
        notes.publish(token)
    except Exception as exc:                  # noqa: BLE001
        print(f"[confirm] {token} cập nhật file biên bản hỏng (bỏ qua): {exc}")


# ------------------------------------------------------------- lúc họp xong


def gate(meta: MeetingMeta, recap: Recap) -> str:
    """Gọi từ `orchestrator._notify_minute`. Trả:
      "held"       — đã gửi chủ duyệt, caller KHÔNG báo người dự;
      "unreviewed" — chủ chưa enroll, caller báo ngay kèm nhãn chưa review;
      "publish"    — tắt tính năng, caller báo như cũ.
    """
    if not config.CONFIRM_ENABLED:
        return "publish"
    token = meta.minute_token
    uid = owner_union_id(meta)
    if not config.SEND_MODE:
        print(f"[confirm] (dry-run) {token} -> "
              + ("sẽ gửi chủ duyệt" if uid else "chủ chưa enroll, sẽ phát kèm nhãn"))
        return "held" if uid else "unreviewed"
    now = _now_ms()
    with db.tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO confirmations(minute_token, owner_union_id, state,"
            " original_recap_json, sent_at, released_version, released_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (token, uid, "pending" if uid else "auto_published", recap_to_json(recap),
             now, 0 if uid else 1, None if uid else now))
    if not cur.rowcount:                      # đã xử lý từ lần trước — đừng gửi lại
        return "held"
    if not uid:
        return "unreviewed"
    _send_owner(meta, uid, recap, 1)
    print(f"[confirm] {token} -> gửi chủ duyệt, chưa báo người dự")
    return "held"


def on_new_recap(meta: MeetingMeta, recap: Recap) -> None:
    """Tóm tắt từ nguyên văn whisper về khi chủ CHƯA đụng vào -> gửi chủ bản
    mới (chủ phải duyệt đúng cái sẽ được phát). Gọi từ `pipeline.save_recap`."""
    r = get(meta.minute_token)
    if (not r or r["state"] != "pending" or r["edits"]
            or summarize.is_placeholder(recap) or not config.SEND_MODE):
        return
    v = r["version"] + 1
    _update(meta.minute_token, version=v, original_recap_json=recap_to_json(recap))
    if r["owner_union_id"]:
        _send_owner(meta, r["owner_union_id"], recap, v, updated=True)


# -------------------------------------------------------- hành động của chủ


def _load(who: dict[str, Any] | None, query: str
          ) -> tuple[str, MeetingMeta | None, dict | None, str]:
    """(token, meta, row, câu lỗi). Có câu lỗi thì các giá trị khác vô nghĩa."""
    from . import qa, sendfile
    if not who:
        return "", None, None, qa.NO_ASKER
    token, err = sendfile._resolve_token(who, query)
    if err:
        return "", None, None, err
    job = jobstore.get(token)
    try:
        meta = jobstore.meta_from_json(job["meta_json"])
    except (TypeError, KeyError, ValueError):
        return "", None, None, "Không đọc được dữ liệu cuộc họp này."
    if not _is_owner(who, meta):
        return "", None, None, (
            f"Chỉ chủ trì cuộc họp ({meta.owner_name or 'người tạo bản ghi'}) mới "
            "duyệt/sửa được biên bản này.")
    row = get(token)
    if not row:
        return "", None, None, (
            f"Cuộc họp «{meta.title}» không đi qua bước duyệt (đã phát từ trước "
            "khi có tính năng này).")
    return token, meta, row, ""


def approve(who: dict[str, Any] | None, query: str) -> str:
    token, meta, row, err = _load(who, query)
    if err:
        return err
    recap = current_recap(token) or summarize.placeholder("chưa có tóm tắt")
    orig = recap_from_json(row["original_recap_json"])
    now = _now_ms()
    cols: dict[str, Any] = dict(state="confirmed", responded_at=now,
                                diff_chars=_diff_chars(orig, recap))
    if row["state"] == "pending":
        n = _broadcast(meta, recap, version=row["version"])
        cols.update(released_version=row["version"], released_at=now)
        msg = f"Đã duyệt và phát biên bản «{meta.title}» cho {n} người dự."
    elif row["released_version"] != row["version"]:
        n = _broadcast(meta, recap, revised=True, version=row["version"])
        cols.update(released_version=row["version"], released_at=now)
        msg = (f"Đã duyệt bản hiệu chỉnh «{meta.title}» và báo {n} người dự. "
               "Nhãn 'chưa review' đã được gỡ.")
    else:
        msg = (f"Đã ghi nhận bạn duyệt «{meta.title}». Nội dung không đổi nên "
               "không gửi thêm gì cho người dự.")
    _update(token, **cols)
    _after_final(token, meta, recap)
    return msg


def edit(who: dict[str, Any] | None, query: str, instruction: str) -> str:
    token, meta, row, err = _load(who, query)
    if err:
        return err
    instruction = (instruction or "").strip()
    if not instruction:
        return "Bạn muốn sửa gì? Nhắn: sửa <tên cuộc họp>: <nội dung cần sửa>."
    recap = current_recap(token) or summarize.placeholder("chưa có tóm tắt")
    try:
        new = summarize.apply_edit(recap, instruction, meta.title)
    except summarize.RecapUnavailable as exc:
        print(f"[confirm] {token} áp sửa hỏng: {exc}")
        return "Chưa áp được sửa đổi (bộ tóm tắt đang lỗi tạm thời). Bạn nhắn lại sau ít phút nhé."
    job = jobstore.get(token) or {}
    jobstore.set_status(token, job.get("status") or "queued",
                        recap_json=recap_to_json(new))
    _update(token, version=row["version"] + 1, edits=row["edits"] + 1,
            responded_at=_now_ms())
    after = ("để phát cho người dự" if row["state"] == "pending"
             else "để báo người dự bản đã hiệu chỉnh")
    return (f"Đã sửa biên bản «{meta.title}». Bản mới:\n\n{new.to_markdown()}\n\n"
            f"Nhắn **duyệt {meta.title}** {after}, hoặc sửa tiếp.")


# ------------------------------------------------------------ mỗi vòng run


def tick() -> None:
    """Nhắc chủ sau CONFIRM_REMIND_HOURS; tự phát kèm nhãn sau CONFIRM_TIMEOUT_HOURS."""
    if not config.CONFIRM_ENABLED or not config.SEND_MODE:
        return
    now = _now_ms()
    rows = db.conn().execute(
        "SELECT * FROM confirmations WHERE state='pending'").fetchall()
    for r in map(dict, rows):
        token = r["minute_token"]
        job = jobstore.get(token)
        if not job:
            continue
        meta = jobstore.meta_from_json(job["meta_json"])
        age_h = (now - (r["sent_at"] or now)) / 3_600_000
        recap = recap_from_json(job.get("recap_json")) or summarize.placeholder(
            "chưa có tóm tắt")
        if age_h >= config.CONFIRM_TIMEOUT_HOURS:
            n = _broadcast(meta, recap, unreviewed=True, version=r["version"])
            _update(token, state="auto_published", released_version=r["version"],
                    released_at=now)
            print(f"[confirm] {token} quá {config.CONFIRM_TIMEOUT_HOURS}h chủ chưa "
                  f"duyệt -> tự phát kèm nhãn cho {n} người")
            try:
                from . import notes
                notes.publish(token)
            except Exception as exc:          # noqa: BLE001
                print(f"[confirm] {token} ghi file biên bản hỏng (bỏ qua): {exc}")
        elif (age_h >= config.CONFIRM_REMIND_HOURS and not r["reminded_at"]
              and r["owner_union_id"]):
            _send_owner(meta, r["owner_union_id"], recap, r["version"], reminder=True)
            _update(token, reminded_at=now)
