"""
Gửi file biên bản `.docx` cho CHÍNH người đang hỏi bot — đường GHI thứ hai.

Vì sao KHÔNG đặt trong `qa.py`: file đó có bất biến "chỉ đọc, đừng thêm hàm
ghi/xoá" (V2_HANDOFF §5.3). Cùng lý do `tasks.py` tồn tại riêng — xem docstring
ở đó. Mọi thứ có tác dụng ra ngoài (tạo task, gửi tin) nằm ngoài `qa.py`, để
ranh giới "dữ liệu họp chảy vào prompt agent" không bao giờ chạm vào cửa ghi.

Vì sao cần (user chốt 03/08/2026): `get_transcript` trả nguyên văn dạng TEXT vào
chat, cắt thành phần. Đọc trên điện thoại thì dài và không lưu lại được. Đường
tự động (`pipeline.deliver`) có gửi file `.docx`, nhưng chỉ MỘT lần lúc phát —
hỏi lại một cuộc họp cũ thì không có cửa nào lấy file.

NĂM RÀNG BUỘC, cưỡng chế bằng CODE chứ không bằng prompt:

 1. Phải là một cuộc họp CÓ THẬT trong `jobs`, tra bằng `minute_token`.
 2. Người hỏi phải ĐƯỢC XEM cuộc họp đó — dùng đúng `qa._may_see`, KHÔNG dựng
    luật phân quyền thứ hai (hai luật song song sẽ lệch, cái lỏng hơn thắng).
 3. Gửi cho CHÍNH người hỏi, không nhận người nhận tuỳ ý. Đây là ràng buộc quan
    trọng nhất của file này: một tool "gửi file cho <ai đó>" mà agent gọi được
    là một máy phát tán biên bản, và nội dung họp — thứ lái được agent — chảy
    thẳng vào prompt của nó.
 4. Transcript phải CÓ CHỮ. Gửi file rỗng thì người nhận mở ra không thấy gì mà
    mọi chỗ đọc trạng thái đều báo thành công (§31.3).
 5. Chống gửi lặp: cùng người + cùng cuộc họp trong `RESEND_COOLDOWN_MIN` phút
    thì KHÔNG gửi lại. Agent gặp lỗi hay hiểu nhầm là nó thử lại, và không có
    cửa này thì một vòng lặp của agent = hai chục file rơi vào chat người dùng.
"""

from __future__ import annotations

import re
from typing import Any

from . import db, jobstore, lark_api, pipeline, qa

# Đủ dài để chặn một vòng lặp của agent, đủ ngắn để người dùng thật đổi ý và
# xin lại file vẫn được phục vụ trong cùng buổi làm việc.
RESEND_COOLDOWN_MIN = 10

# `kind` riêng trong bảng `deliveries`. KHÔNG dùng lại `full`: bảng đó trả lời
# câu "ai đã nhận biên bản khi hệ thống PHÁT", và `_backfill_deliveries` đọc nó
# để biết còn ai chưa nhận. Nhét lượt gửi theo yêu cầu vào `full` là làm
# backfill tưởng người đó đã được phát rồi, rồi thôi không gửi nữa.
KIND = "ondemand"

NO_MEETING = (
    "KHÔNG TRA RA cuộc họp nào tên như vậy trong hệ thống. Nói đúng như thế với "
    "người dùng — ĐỪNG nói 'không xuất được file', 'bản ghi chưa hợp lệ' hay bất "
    "cứ câu nào nghe như hệ thống hỏng: sự thật chỉ là không tìm thấy cuộc họp. "
    "Gọi `list_meetings` hoặc `search_meetings` để lấy đúng tên/`minute_token` "
    "rồi gọi lại."
)


def _no_meeting_with_hints(who: dict[str, Any] | None,
                           qwords: set[str], limit: int = 5) -> str:
    """`NO_MEETING` kèm danh sách cuộc GẦN ĐÚNG mà chính người hỏi được xem.

    Vì sao phải kèm dữ liệu chứ không chỉ viết lại câu chữ (bài học 04/08/2026):
    bản trước đã dặn thẳng trong câu trả lời "ĐỪNG nói 'không xuất được file'"
    — agent vẫn nói y hệt. Chỉ thị trong output của tool KHÔNG điều khiển được
    agent một cách đáng tin. Thứ điều khiển được nó là DỮ LIỆU HÀNH ĐỘNG ĐƯỢC:
    đưa sẵn vài cái tên kèm `minute_token` thì nó gọi lại đúng, thay vì bịa ra
    một lời giải thích kỹ thuật rồi bỏ cuộc.

    Xếp theo số chữ trùng, và CHỈ nêu cuộc người đó được xem — không lộ cuộc của
    người khác.
    """
    index = qa.viewers_index()
    scored: list[tuple[int, str, str]] = []
    for row in jobstore.all_jobs():
        if not qa._may_see(row["minute_token"], who, index):
            continue
        try:
            title = jobstore.meta_from_json(row["meta_json"]).title
        except (ValueError, KeyError, TypeError):
            continue
        hit = len(qwords & set(_norm(title).split()))
        if hit:
            scored.append((hit, title, row["minute_token"]))
    if not scored:
        return NO_MEETING
    scored.sort(key=lambda x: -x[0])
    lines = [f"- {t} — minute_token: {tok}" for _, t, tok in scored[:limit]]
    return (NO_MEETING + "\n\nGẦN ĐÚNG NHẤT (gọi lại tool này với một trong các "
            "`minute_token` dưới đây, ĐỪNG bỏ cuộc):\n" + "\n".join(lines))


def _norm(s: str) -> str:
    """Chuẩn hoá tên cuộc họp để so khớp: thường hoá, bỏ DẤU CÂU, gộp khoảng trắng.

    GIỮ dấu tiếng Việt (`\\w` của Python là unicode) — 'CĐS' và 'CDS' là hai từ
    khác nhau, gộp chúng lại là mời gọi khớp nhầm. Chỉ bỏ dấu câu, vì đó mới là
    thứ người ta lược đi khi gõ: tên thật 'CĐS: Flow Backlog' còn người dùng
    nhắn 'cđs flow backlog' — thiếu đúng một dấu hai chấm là trượt sạch.
    """
    return " ".join(re.sub(r"[^\w\s]", " ", (s or ""), flags=re.UNICODE)
                    .lower().split())


def _resolve_token(who: dict[str, Any] | None, query: str) -> tuple[str, str]:
    """(minute_token, câu báo lỗi) — đúng một trong hai có giá trị.

    Nhận `minute_token` HOẶC tên cuộc họp. Vì sao phải nhận cả tên (sửa
    04/08/2026, sau một ca thật): agent gọi tool này với TÊN cuộc họp thay vì
    token là chuyện thường xuyên — nó vừa đọc tên từ câu người dùng. Mô tả tool
    đã dặn rõ vẫn trượt, nên chặn ở CODE. Trước đó tool trả "cần minute_token",
    agent diễn giải chệch thành "bản ghi chưa được nhận diện là biên bản có thể
    xuất file", và người dùng — có toàn quyền với cuộc họp đó — tưởng hệ thống
    hỏng rồi thôi không hỏi lại. Hay gặp nhất ở tin nhắn ĐẦU TIÊN của hội thoại,
    khi agent chưa gọi list_meetings nên chưa có token trong tay.

    KHÔNG nới quyền: đây chỉ là bước tên -> token. Người hỏi vẫn phải qua
    `qa._may_see` ở `send_transcript` như cũ. Danh sách gợi ý khi trùng tên chỉ
    nêu cuộc mà chính người đó được xem.
    """
    query = (query or "").strip()
    if not query:
        return "", NO_MEETING
    if jobstore.get(query):
        return query, ""                  # đã là token
    q = _norm(query)
    if not q:
        return "", NO_MEETING
    qwords = set(q.split())
    exact: list[dict[str, Any]] = []
    part: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for row in jobstore.all_jobs():
        try:
            title = jobstore.meta_from_json(row["meta_json"]).title
        except (ValueError, KeyError, TypeError):
            continue
        t = _norm(title)
        if t == q:
            exact.append(row)
        elif q in t:
            part.append(row)
        elif qwords and qwords <= set(t.split()):
            # Nấc thứ ba: ĐỦ CHỮ, không cần đúng thứ tự (thêm 04/08/2026 sau ca
            # thật thứ hai). Người dùng gõ 'workforce AI 23-07' trong khi tên là
            # '07-23 | Workforce AI Weekly Meeting Buổi 3' — đảo ngày/tháng và
            # thiếu đuôi, khớp-chuỗi-liền trượt sạch. Sau chuẩn hoá thì '07-23'
            # tách thành hai chữ '07' '23' nên phép so theo TẬP CHỮ bắt được cả
            # kiểu viết ngày ngược, thứ mà không luật thứ tự nào bắt nổi.
            words.append(row)
    cands = exact or part or words
    if not cands:
        return "", _no_meeting_with_hints(who, qwords)
    if len(cands) == 1:
        return cands[0]["minute_token"], ""
    # Trùng nhiều cuộc (hệ thống có hai cuộc cùng tên 'test luồng tự động' là
    # chuyện thật). Thu hẹp theo quyền TRƯỚC khi hỏi lại: người ta thường chỉ
    # được xem một trong số đó, và như vậy khỏi bắt hỏi lại vô ích.
    index = qa.viewers_index()
    vis = [r for r in cands if qa._may_see(r["minute_token"], who, index)]
    if len(vis) == 1:
        return vis[0]["minute_token"], ""
    if not vis:
        return "", NO_MEETING
    import time as _t
    lines = []
    for r in sorted(vis, key=lambda x: x["start_ts"] or 0, reverse=True):
        when = (_t.strftime("%d/%m/%Y %H:%M", _t.localtime(r["start_ts"] / 1000))
                if r["start_ts"] else "?")
        lines.append(f"- {jobstore.meta_from_json(r['meta_json']).title} "
                     f"({when}) — minute_token: {r['minute_token']}")
    return "", (f"Có {len(vis)} cuộc họp trùng tên '{query}'. Hỏi người dùng muốn "
                f"cuộc nào rồi gọi lại với `minute_token`:\n" + "\n".join(lines))


def _recent_send(minute_token: str, recipient: str) -> bool:
    """Đã gửi thành công file này cho người này trong cửa sổ chống lặp chưa."""
    row = db.conn().execute(
        "SELECT sent_at FROM deliveries WHERE minute_token=? AND recipient=? "
        "AND kind=? AND ok=1 ORDER BY sent_at DESC LIMIT 1",
        (minute_token, recipient, KIND),
    ).fetchone()
    if not row:
        return False
    import time
    return (time.time() * 1000 - int(row["sent_at"] or 0)) < RESEND_COOLDOWN_MIN * 60_000


def send_transcript(who: dict[str, Any] | None, minute_token: str) -> str:
    """Gửi file .docx nguyên văn cho chính `who`. Trả câu báo cho agent."""
    if not who:
        return qa.NO_ASKER
    # (1) cuộc họp có thật — nhận cả TÊN, không chỉ `minute_token` (xem
    # `_resolve_token`). Cửa quyền ở (2) không đổi.
    minute_token, err = _resolve_token(who, minute_token)
    if err:
        return err
    row = jobstore.get(minute_token)
    if not row:
        return NO_MEETING

    # (2) được xem — ĐÚNG bộ lọc của qa, không phải luật thứ hai
    if not qa._may_see(minute_token, who, qa.viewers_index()):
        return ("Cuộc họp này CÓ trong hệ thống nhưng bạn không có trong danh "
                "sách người dự, nên mình không gửi biên bản được. Nếu bạn có dự "
                "thì nhắn quản trị hệ thống — có thể việc tra người dự bị sót.")

    # (3) gửi cho CHÍNH người hỏi. `admin_view` (đường `v2 ask`) không có id nào
    # -> không gửi đi đâu cả, và nói rõ vì sao thay vì im lặng "đã gửi".
    rid = who.get("union_id") or ""
    id_type = "union_id"
    if not rid:
        rid, id_type = who.get("open_id") or "", "open_id"
    if not rid:
        return ("Không gửi được: phiên này không có định danh Lark để gửi tới "
                "(đường terminal/admin). Thử trong Lark.")

    # (5) chống lặp — kiểm TRƯỚC khi sinh file và trước khi upload
    if _recent_send(minute_token, rid):
        return (f"Đã gửi file biên bản cuộc họp này cho bạn trong "
                f"{RESEND_COOLDOWN_MIN} phút vừa rồi — kiểm tra lại trong khung "
                f"chat. KHÔNG gọi lại tool này; nếu người dùng vẫn không thấy "
                f"thì bảo họ nhắn quản trị hệ thống.")

    # (4) phải có chữ. Nạp transcript từ đĩa: `jobs.transcript_path` là nguồn
    # sự thật, file .docx chỉ là bản xuất và có thể chưa từng được tạo (cuộc họp
    # cũ chỉ có .txt, hoặc phát trước khi đổi định dạng — V2_MAINTENANCE §35).
    tpath = row.get("transcript_path")
    if not tpath:
        # CHƯA có transcript whisper. Mô hình KÉO (03/08/2026): thay vì chỉ báo
        # tình trạng, NÂNG cuộc này lên CỰC CAO (priority=2) và ghi người hỏi —
        # vòng `run` dịch nó trước tiên rồi `_deliver_requested` tự gửi cho họ.
        db.add_transcript_request(minute_token, rid, who.get("name", ""))
        st = row.get("status") or ""
        # Job ở trạng thái KHÔNG tự chạy lại (discarded/failed/owner_only/...):
        # kéo về `queued` để được nhặt. Job đang chạy (queued/transcribing/
        # recapping) thì để nguyên, chỉ nâng priority.
        if st not in ("queued", "transcribing", "recapping"):
            jobstore.set_status(minute_token, "queued", error=None)
        jobstore.set_priority(minute_token, 2)
        return ("Cuộc họp này CHƯA có bản transcript chuẩn (whisper). Mình đã "
                "ƯU TIÊN DỊCH NGAY và sẽ TỰ GỬI cho bạn khi xong — bạn không cần "
                "hỏi lại. Cuộc ngắn vài phút, cuộc dài thì lâu hơn. Trong lúc "
                "chờ, bản Minute của Lark xem tạm được. Báo đúng vậy cho người "
                "dùng, đừng bịa là đã gửi.")
    import json
    from .models import Transcript
    try:
        with open(tpath, encoding="utf-8") as f:
            t = Transcript.from_json(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return (f"Có ghi đường dẫn nguyên văn nhưng đọc KHÔNG được ({exc}). "
                f"Đây là lỗi hệ thống, không phải cuộc họp thiếu dữ liệu — báo "
                f"người dùng nhắn quản trị hệ thống.")
    if not (t.text or "").strip():
        return ("Cuộc họp này có file nguyên văn nhưng KHÔNG có chữ nào — "
                "bản ghi im lặng, hoặc whisper hỏng lúc chạy. Không có gì để "
                "gửi; báo người dùng nhắn quản trị hệ thống.")

    meta = jobstore.meta_from_json(row["meta_json"])
    doc = pipeline.write_doc(t, meta)
    try:
        lark_api.im_send_file(rid, doc, id_type=id_type,
                              uuid_key=f"ond-{minute_token}-{rid}"[:50])
    except lark_api.LarkError as exc:
        jobstore.record_delivery(minute_token, rid, KIND, False, str(exc))
        return (f"Gửi file hỏng: {exc}. Nói thẳng với người dùng là chưa gửi "
                f"được, đừng nói đã gửi.")
    jobstore.record_delivery(minute_token, rid, KIND, True)
    return (f"ĐÃ gửi file '{doc.name}' vào khung chat này. Báo ngắn gọn cho "
            f"người dùng là file đã ở trong chat, và nhắc một câu rằng đó là "
            f"bản máy phiên âm nên có lỗi nghe nhầm. KHÔNG chép lại nội dung "
            f"biên bản ra tin nhắn — người dùng xin FILE.")
