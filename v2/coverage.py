"""Phủ sóng: mỗi người đã enroll đang THẤY được bao nhiêu cuộc họp của họ.

    python -m v2 coverage [--days 90]

Vì sao cần một lệnh riêng (06/08/2026, sau khi chị Ngọc Anh vào bot và báo
"không thấy cuộc họp cũ"): trước đó không có cách nào trả lời câu
"vì sao người này không thấy cuộc họp X" ngoài việc mở SQLite gõ tay. Mà câu
đó có tới BỐN nguyên nhân khác hẳn nhau, và ba trong bốn cái KHÔNG sửa được
bằng code:

  1. Lark chưa từng cho ta thấy cuộc đó -> `backfill` nạp bù được.
  2. Có job rồi, đã phiên âm xong -> đang chạy đúng.
  3. Có job nhưng KHÔNG AI TẢI ĐƯỢC bản ghi -> chờ CHỦ bản ghi tự kết nối.
  4. Người này không phải owner/attendee -> đúng luật, không phải lỗi.

Đo thật 06/08/2026, và đây là thứ định hình cả bảng dưới — quyền của Lark
Minutes có HAI TẦNG, không phải một:

    minutes_get (tiêu đề, giờ, chủ, link)   -> NGƯỜI DỰ đọc được
    minutes_transcript + media (nội dung)   -> CHỈ CHỦ BẢN GHI

Đo trên `07-30 | Workforce AI Weekly Meeting: Buổi 4`: chủ bản ghi đọc được
44.340 ký tự nguyên văn của Lark; một người DỰ CHÍNH cuộc đó bị từ chối
`2091005 permission deny`. Và trên 18 job `waiting_auth`: 18/18 đọc được
metadata, 0/18 đọc được nguyên văn hay tải được bản ghi.

Hệ quả phải nhớ trước khi hứa gì với người dùng: **không có đường vòng kỹ
thuật nào** cho cuộc họp mà chủ bản ghi chưa kết nối. Không phải whisper yếu,
cũng không phải thiếu tính năng đọc Lark Minute — Lark không đưa nội dung ra
cho bất kỳ token nào khác. Cách duy nhất là chính chủ bản ghi vào bot.

Lệnh này CHỈ ĐỌC: không tạo job, không đổi status, không nhắn ai. Cột "Lark có"
gọi `minutes_list` theo từng người nên có tốn ít lời gọi API — chạy khi cần,
đừng đưa vào vòng lặp.
"""

from __future__ import annotations

import time
from typing import Any

from . import db, jobstore, lark_api, tokenstore


def _now_ms() -> int:
    return int(time.time() * 1000)


def _has_content(row: dict[str, Any]) -> bool:
    """Job này ĐÃ có nội dung để trả lời chưa.

    Tiêu chí là `transcript_path`, không phải status: `held` nghĩa là đã phiên
    âm xong và đang chờ người hỏi, còn `delivered` là đã phát — cả hai đều trả
    lời được. Ngược lại một job `queued` thì dù có record trên Base cũng chưa
    có chữ nào để đọc.
    """
    return bool(row.get("transcript_path"))


def collect(days: int = 90) -> dict[str, Any]:
    """Gom số liệu phủ sóng. Không in gì — để `report()` lo phần chữ."""
    users = tokenstore.list_users(active_only=True)
    jobs = {r["minute_token"]: r for r in jobstore.all_jobs()}

    # minute_token -> tập id được xem (dùng lại ĐÚNG chỉ mục của bot, không
    # dựng luật thứ hai: hai luật phân quyền song song thì cái lỏng hơn thắng).
    from . import qa
    index = qa.viewers_index()

    end = _now_ms()
    start = end - max(1, int(days)) * 86_400_000

    rows: list[dict[str, Any]] = []
    lark_seen: dict[str, set[str]] = {}
    for u in users:
        oid, uid = u["open_id"], u.get("union_id") or ""
        try:
            token = tokenstore.get_access_token(oid)
            found = {it.get("token") or it.get("minute_token")
                     for it in lark_api.minutes_list(token, start, end, oid)}
            found.discard(None)
            err = ""
        except Exception as exc:               # noqa: BLE001 — một token chập không làm mù cả bảng
            found, err = set(), str(exc)[:60]
        lark_seen[oid] = found

        mine = [t for t, ids in index.items() if {oid, uid} & ids]
        rows.append({
            "name": u.get("name") or oid,
            "open_id": oid,
            "lark": len(found),
            "in_db": sum(1 for t in found if t in jobs),
            "visible": len(mine),
            "with_content": sum(1 for t in mine if _has_content(jobs.get(t, {}))),
            "blocked": sum(1 for t in mine
                           if (jobs.get(t, {}).get("status") == "waiting_auth")),
            "missing": sorted(t for t in found if t not in jobs),
            "error": err,
        })

    # Ai đang CHẶN: gom job waiting_auth theo chủ bản ghi. Đây là danh sách
    # hành động thật sự — mỗi dòng là một người cần được mời vào bot.
    enrolled = {u["open_id"] for u in users}
    blockers: dict[str, dict[str, Any]] = {}
    for row in jobstore.by_status("waiting_auth"):
        try:
            meta = jobstore.meta_from_json(row["meta_json"])
        except Exception:                      # noqa: BLE001 — job cũ méo dữ liệu
            continue
        owner = meta.owner_open_id or "(không rõ chủ bản ghi)"
        if owner in enrolled:
            # Chủ đã enroll mà job vẫn kẹt = chuyện KHÁC (token hỏng, Lark từ
            # chối chính chủ). Tách riêng để không lẫn vào danh sách đi mời.
            owner = f"!! {owner}"
        b = blockers.setdefault(owner, {"name": meta.owner_name or "", "jobs": []})
        b["jobs"].append(meta.title or row["minute_token"])
        if meta.owner_name and not b["name"]:
            b["name"] = meta.owner_name

    return {"days": days, "rows": rows, "blockers": blockers,
            "total_jobs": len(jobs)}


def report(days: int = 90) -> int:
    """In bảng cho người vận hành. Trả mã thoát (0 = không có gì phải làm)."""
    data = collect(days)
    rows, blockers = data["rows"], data["blockers"]

    print(f"=== PHỦ SÓNG {days} NGÀY — {len(rows)} người đã kết nối ===\n")
    print(f"{'Người dùng':40} {'Lark có':>8} {'đã nạp':>7} "
          f"{'thấy được':>10} {'đọc được':>9} {'đang kẹt':>9}")
    print("-" * 88)
    for r in rows:
        print(f"{r['name'][:40]:40} {r['lark']:>8} {r['in_db']:>7} "
              f"{r['visible']:>10} {r['with_content']:>9} {r['blocked']:>9}"
              + (f"   <- {r['error']}" if r["error"] else ""))

    missing = sorted({t for r in rows for t in r["missing"]})
    print(f"\nLark biết {sum(r['lark'] for r in rows)} lượt cuộc họp; "
          f"{len(missing)} cuộc CHƯA có trong hệ thống.")
    if missing:
        print("  Nạp bù (thử khô trước, KHÔNG gửi tin cho ai):")
        print(f"    python -m v2 backfill --days {days}")
        print(f"    python -m v2 backfill --days {days} --yes")

    if blockers:
        total = sum(len(b["jobs"]) for b in blockers.values())
        print(f"\n=== ĐANG CHẶN: {total} cuộc chờ CHỦ BẢN GHI kết nối ===")
        print("Lark chỉ đưa nội dung (nguyên văn + bản ghi) cho CHỦ bản ghi.")
        print("Người dự đọc được tiêu đề/giờ nhưng KHÔNG đọc được nội dung —")
        print("nên không có cách kỹ thuật nào lấy được, kể cả qua Lark Minute.\n")
        for oid, b in sorted(blockers.items(),
                             key=lambda kv: -len(kv[1]["jobs"])):
            who = b["name"] or "(chưa rõ tên)"
            print(f"  {who[:34]:34} {len(b['jobs']):>3} cuộc   {oid[:24]}")
            for t in b["jobs"][:3]:
                print(f"       • {t[:64]}")
            if len(b["jobs"]) > 3:
                print(f"       … và {len(b['jobs']) - 3} cuộc nữa")
        print("\nHọ kết nối xong thì các cuộc này TỰ chạy tiếp — không phải")
        print("làm gì thêm. KHÔNG tự gửi link cho họ: luật hiện tại là người ta")
        print("phải nhắn bot trước (xem docs/CURRENT_CONTEXT.md §1 mục 2).")

    return 0 if not (missing or blockers) else 1
