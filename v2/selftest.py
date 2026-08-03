"""
Tự kiểm V2 — chạy được MỌI LÚC, không cần mạng, không đụng `state.db` thật.

    python -m v2 selftest

Vì sao có file này (02/08/2026): repo tới hôm đó **không có phép kiểm tự động
nào**. Mọi lỗi trong lịch sử — `MAX_ATTEMPTS` đếm sai loại lỗi, LLM hỏng thành
"phát xong", ghi Base hỏng không có đường vá, hỏi đáp không lọc theo người —
đều được tìm bằng tay SAU KHI đã hỏng trên dữ liệu thật, và nhiều cái sống qua
vài phiên. Sổ tay ghi lại từng cái rất kỹ, nhưng văn bản không chặn được ai đó
làm lại. Bốn lỗi tìm ra trong ngày viết file này đều do chạy đúng những phép
kiểm dưới đây, không phải do đọc code.

`doctor` KHÔNG thay thế được cái này: nó soi TRẠNG THÁI hệ thống đang chạy
(whisper sống chưa, token còn mấy ngày), còn đây soi HÀNH VI của code trên các
nhánh hiếm — cái mà chỉ khi hỏng thật mới thấy.

Ba luật của file này, giữ nguyên nếu thêm phép kiểm mới:

 1. **Không đụng dữ liệu thật.** DB tạm trong thư mục tạm, xoá lúc xong. Biến
    môi trường đặt TRƯỚC khi import `v2.*` (config đọc `.env` một lần lúc
    import) — đó là lý do file này tự chạy lại mình trong một tiến trình con.
 2. **Không gọi mạng.** Mọi lời gọi Lark/LLM đều bị thay bằng hàm giả. Một phép
    kiểm cần mạng là một phép kiểm sẽ bị bỏ qua khi mạng chập.
 3. **Kiểm nhánh HIẾM, không kiểm đường sướng.** Đường sướng đã chạy hằng ngày
    rồi. Giá trị nằm ở: dry-run có ghi bậy không, lỗi vĩnh viễn có bị xử như lỗi
    tạm thời không, `who=None` có rò dữ liệu không, hai cuộc họp có dùng chung
    một file không.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# =====================================================================
#  Điểm vào từ CLI: chạy lại chính file này trong tiến trình CON
# =====================================================================


def run() -> int:
    """`python -m v2 selftest` gọi vào đây. Trả mã thoát.

    Phải là tiến trình CON: `config.py` đọc `v2/.env` một lần lúc import, nên
    khi CLI đã nạp xong config thật thì không còn cách nào trỏ `V2_DB_PATH` sang
    DB tạm nữa. Chạy lại file này với env đã đặt sẵn là cách duy nhất chắc chắn
    không chạm vào `state.db` thật.
    """
    tmp = Path(tempfile.mkdtemp(prefix="v2-selftest-"))
    env = dict(os.environ)
    env.update({
        "V2_DATA_DIR": str(tmp),
        "V2_DB_PATH": str(tmp / "t.db"),
        "V2_BACKUP_DIR": str(tmp / "bk"),
        "V2_KEY_BACKUP_PATH": str(tmp / "key" / "k.txt"),
        "V2_BACKUP_EVERY_HOURS": "0",
        # Khóa VỨT ĐI, sinh riêng cho file này, KHÔNG phải khóa của `v2/.env`.
        # Bản đầu tôi dán thẳng khóa thật vào đây và suýt commit lên GitHub —
        # đó là khóa giải mã user token của MỌI người đã enroll. `state.db` được
        # gitignore, nhưng khóa lọt vào git là mất luôn ý nghĩa của việc mã hóa
        # (và git giữ nó trong history vĩnh viễn, xem đầu `.gitignore`).
        # Đừng bao giờ đặt giá trị thật từ `.env` vào file được commit.
        "V2_FERNET_KEY": "Osre32wbqm2eXpEmo1u_YCsdKrZfrcPYh8Uck_qhMFA=",
        "BITABLE_APP_TOKEN": "", "BITABLE_TABLE_ID": "",
        "ALERT_UNION_IDS": "on_ADMIN", "QA_ADMIN_UNION_IDS": "on_ADMIN",
        "STATUS_PUSH_URL": "", "OAUTH_PULL_URL": "", "LLM_API_KEY": "",
        "PYTHONIOENCODING": "utf-8",
    })
    try:
        return subprocess.call([sys.executable, str(Path(__file__).resolve())],
                               env=env, cwd=str(Path(__file__).resolve().parent.parent))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# =====================================================================
#  Thân bài — chỉ chạy khi là tiến trình con (env đã đặt sẵn)
# =====================================================================


def _main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import time
    from v2 import (alerts, askers, backup, cards, config, db, gate, jobstore,
                    lark_api, mcp_server, meetings, oauth, oauth_callback,
                    orchestrator, pipeline, qa, summarize, tasks, tokenstore,
                    transcribe)
    from v2.models import Attendee, MeetingMeta, Recap, Segment, Transcript

    config.ensure_dirs()
    db.init()

    # LƯỚI CHẶN MẠNG (02/08/2026). Luật 2 ở đầu file nói "không gọi mạng",
    # nhưng nó chỉ là một câu trong docstring — và tôi đã phá nó ngay hôm thêm
    # nhóm 32: stub `pipeline.deliver` mà quên `pipeline.deliver_file`, nên hàm
    # THẬT chạy và gọi `im_send_file` tới Lark thật giữa lúc selftest. Lần đó vô
    # hại vì union_id là bịa (99992364), nhưng trùng một id thật là GỬI TIN THẬT
    # cho người thật.
    #
    # Mọi lời gọi HTTP của `lark_api` đều đi qua `_http()`, nên chặn ở đây là
    # chặn được tất cả: quên stub một hàm giờ thành FAIL ngay dòng đó, thay vì
    # một lời gọi mạng im lặng mà chỉ đọc log mới thấy.
    def _no_net(*a, **k):
        raise AssertionError(
            "selftest gọi MẠNG THẬT: có hàm lark_api chưa được thay bằng hàm "
            "giả. Xem stack để biết hàm nào, rồi stub nó (luật 2 đầu file).")

    lark_api._http = _no_net

    ok: list[str] = []
    bad: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        (ok if cond else bad).append(name)
        mark = "  OK  " if cond else "  FAIL"
        print(f"{mark} {name}" + (f"   <- {detail}" if detail and not cond else ""))

    def part(title: str) -> None:
        print(f"\n--- {title}")

    def meta(**kw) -> MeetingMeta:
        d = dict(minute_token="obsgTEST00000000000001", title="Hop thu",
                 start=1785000000.0, duration_sec=600,
                 owner_open_id="ou_owner", owner_name="Chu Ban Ghi",
                 app_link="https://x/minutes/abc")
        d.update(kw)
        return MeetingMeta(**d)

    def wipe_jobs() -> None:
        with db.tx() as c:
            c.execute("DELETE FROM jobs")

    def add_user(oid, uid, name, status="active") -> None:
        far = 9_999_999_999_000
        with db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO tokens(open_id,union_id,name,access_enc,"
                "refresh_enc,access_exp,refresh_exp,scopes,status,enrolled_at,"
                "updated_at,last_used) VALUES (?,?,?,'','',?,?,'',?,0,0,0)",
                (oid, uid, name, far, far, status))

    # =================================================================
    part("1. deliver cho NHIỀU người — dùng lại file_key, hỏng một người "
         "không gãy cả job")
    # =================================================================
    # Đây là đường tốn tiền nhất nếu sai và là đường CHƯA TỪNG chạy thật với
    # ≥2 người nhận (V2_HANDOFF §3): trước 02/08 mọi lần phát đều chỉ 1 người.
    seen = {"upload": 0, "card": [], "file": [], "uuid": []}

    def f_upload(path, ftype="stream"):
        seen["upload"] += 1
        return "FILEKEY_1"

    def f_card(rid, card, *, id_type="union_id", uuid_key=None):
        seen["card"].append(rid)
        seen["uuid"].append(uuid_key)
        if rid == "on_BAD":
            raise lark_api.LarkError(230013, "chua phat hanh", "im_send")
        return "om_" + rid

    def f_file(rid, path, *, id_type="union_id", uuid_key=None, file_key=None):
        seen["file"].append((rid, file_key))
        seen["uuid"].append(uuid_key)
        return "om_f"

    class _NullJobstore:
        record_delivery = staticmethod(lambda *a, **k: None)
        set_status = staticmethod(lambda *a, **k: None)

    keep = (lark_api.im_upload_file, lark_api.im_send_card,
            lark_api.im_send_file, pipeline.jobstore)
    lark_api.im_upload_file, lark_api.im_send_card, lark_api.im_send_file = \
        f_upload, f_card, f_file
    pipeline.jobstore = _NullJobstore

    tr = Transcript(minute_token="x", lang="vi", duration=60, engine="t",
                    segments=[Segment(0, 60, "noi dung")])
    sent, failed = pipeline.deliver(meta(), Recap(summary="tom tat"), tr,
                                    ["on_A", "on_BAD", "on_C"])
    check("upload transcript đúng 1 lần cho 3 người", seen["upload"] == 1,
          f"gọi {seen['upload']} lần")
    check("gửi thẻ hỏng cho một người thì KHÔNG gửi file cho người đó",
          [r for r, _ in seen["file"]] == ["on_A", "on_C"])
    check("mọi người dùng CHUNG một file_key",
          {k for _, k in seen["file"]} == {"FILEKEY_1"})
    check("sent/failed đúng", sent == ["on_A", "on_C"] and failed == ["on_BAD"])
    check("uuid_key khác nhau từng người (Lark khử trùng theo uuid)",
          len(set(seen["uuid"])) == len(seen["uuid"]))
    check("uuid_key <= 50 ký tự (Lark cắt ở 50 -> dài quá là đụng nhau)",
          all(len(u) <= 50 for u in seen["uuid"]))

    lark_api.im_upload_file, lark_api.im_send_card, lark_api.im_send_file, \
        pipeline.jobstore = keep

    # =================================================================
    part("2. doc_path — hai cuộc họp KHÔNG được dùng chung một file")
    # =================================================================
    # `bitable._tracking_fields` dựng lại đúng đường dẫn này để đính kèm vào
    # Base. Đụng tên = record của cuộc A nhận transcript của cuộc B.
    a = pipeline.doc_path(meta(title="Hop tuan", start=None, minute_token="obsgAAA"))
    b = pipeline.doc_path(meta(title="Hop tuan", start=None, minute_token="obsgBBB"))
    check("không có giờ họp: hai cuộc trùng tên vẫn ra hai file", a != b,
          f"cả hai đều là {a.name}")
    c1 = pipeline.doc_path(meta(title="H", start=1785000000.0, minute_token="obsgAAA"))
    check("có giờ họp: tên file giữ nguyên định dạng cũ (không phá file đã có)",
          "obsgAAA" not in c1.name)

    # =================================================================
    part("3. download_recording — lỗi VĨNH VIỄN vs lỗi TẠM THỜI")
    # =================================================================
    def scenario(deny=(), other=(), notoken=()):
        def gat(oid):
            if oid in notoken:
                raise tokenstore.TokenError(f"{oid} chưa enroll")
            return "tok_" + oid

        def murl(tok, mt):
            who = tok[4:]
            if who in deny:
                raise lark_api.LarkError(2091005, "permission deny", "minutes_media")
            if who in other:
                raise lark_api.LarkError(500, "server lỗi", "minutes_media")
            return "https://dl/" + mt

        keep2 = (tokenstore.get_access_token, lark_api.minutes_media_url)
        tokenstore.get_access_token, lark_api.minutes_media_url = gat, murl
        try:
            pipeline.download_recording(meta())
            out = "TẢI ĐƯỢC"
        except pipeline.MediaDenied:
            out = "MediaDenied"
        except pipeline.PipelineError:
            out = "PipelineError"
        tokenstore.get_access_token, lark_api.minutes_media_url = keep2
        return out

    keep3 = pipeline._reader_candidates
    pipeline._reader_candidates = lambda m: ["ou_owner", "ou_2", "ou_3"]
    check("tất cả bị từ chối quyền -> MediaDenied (failed ngay, khỏi thử lại)",
          scenario(deny=("ou_owner", "ou_2", "ou_3")) == "MediaDenied")
    check("lẫn MỘT lỗi 5xx -> PipelineError (vẫn thử lại, đừng kết luận vội)",
          scenario(deny=("ou_owner", "ou_2"), other=("ou_3",)) == "PipelineError")
    check("chủ bị từ chối + người khác chưa enroll -> vẫn MediaDenied",
          scenario(deny=("ou_owner",), notoken=("ou_2", "ou_3")) == "MediaDenied")
    check("chỉ toàn người chưa enroll -> PipelineError (chờ họ enroll)",
          scenario(notoken=("ou_owner", "ou_2", "ou_3")) == "PipelineError")
    pipeline._reader_candidates = keep3

    # =================================================================
    part("4. BẤT BIẾN: `process` dry-run KHÔNG được ghi gì vào DB")
    # =================================================================
    # `process` (không --send) là lệnh CHẨN ĐOÁN, người ta chạy nó vài lần.
    # Đã mất một job thật vì nó cộng `attempts` như lần chạy thật (31/07/2026).
    import json as _json

    def snap() -> str:
        return _json.dumps([dict(r) for r in db.conn().execute(
            "SELECT * FROM jobs ORDER BY minute_token")],
            sort_keys=True, default=str)

    def mk_job(tok, attempts=2):
        jobstore.create(meta(minute_token=tok, title="Hop " + tok), status="queued")
        with db.tx() as c:
            c.execute("UPDATE jobs SET attempts=? WHERE minute_token=?",
                      (attempts, tok))

    keep4 = pipeline.run_transcription
    for label, boom in (
            ("MediaDenied", pipeline.MediaDenied("không ai được tải")),
            ("EmptyTranscript", pipeline.EmptyTranscript("0 từ")),
            ("TranscribeUnavailable", transcribe.TranscribeUnavailable("whisper tắt")),
            ("lỗi lạ", RuntimeError("mạng hỏng"))):
        wipe_jobs()
        mk_job("obsgDRY0000000000000001")
        before = snap()
        pipeline.run_transcription = (lambda b: (lambda m: (_ for _ in ()).throw(b)))(boom)
        orchestrator.process_queue(dry_run=True)
        check(f"dry-run + {label}: DB không đổi một byte", snap() == before,
              "DB ĐÃ BỊ GHI trong dry-run")

    part("5. Chạy THẬT: mỗi loại lỗi vào đúng trạng thái")
    for label, boom, st, att in (
            ("MediaDenied -> failed ngay", pipeline.MediaDenied("x"), "failed", 3),
            # Cùng họ "không tự khỏi": chạy lại whisper trên cùng audio cho ra
            # đúng 0 từ đó. Trước 02/08/2026 nó KHÔNG được bắt riêng nên job đi
            # thẳng tới `delivered` và không ai được báo.
            ("EmptyTranscript -> failed ngay", pipeline.EmptyTranscript("x"),
             "failed", 3),
            ("TranscribeUnavailable -> trả lại lần thử",
             transcribe.TranscribeUnavailable("x"), "queued", 2),
            ("lỗi lạ -> tiêu một lần thử", RuntimeError("x"), "queued", 3)):
        wipe_jobs()
        mk_job("obsgREAL000000000000001")
        pipeline.run_transcription = (lambda b: (lambda m: (_ for _ in ()).throw(b)))(boom)
        orchestrator.process_queue(dry_run=False)
        r = jobstore.get("obsgREAL000000000000001")
        check(f"{label} (status={st}, attempts={att})",
              r["status"] == st and r["attempts"] == att,
              f"thực tế status={r['status']} attempts={r['attempts']}")

    wipe_jobs()
    mk_job("obsgERR0000000000000001", attempts=0)
    pipeline.run_transcription = lambda m: (_ for _ in ()).throw(
        pipeline.MediaDenied("chủ bản ghi là Nguyen Ngoc Phuc, chưa cấp quyền"))
    orchestrator.process_queue(dry_run=False)
    check("MediaDenied giữ NGUYÊN NHÂN, không bị ghi đè bằng 'quá N lần thử'",
          "Nguyen Ngoc Phuc" in (jobstore.get("obsgERR0000000000000001")["error"] or ""))
    pipeline.run_transcription = keep4

    # =================================================================
    part("6. _recipients — chỉ người ĐÃ ENROLL, hợp hai nguồn, khử trùng")
    # =================================================================
    add_user("ou_A", "on_A", "An")
    add_user("ou_B", "on_B", "Binh")
    add_user("ou_ADMIN", "on_ADMIN", "Admin")
    add_user("ou_X", "on_X", "Da thu hoi", status="revoked")
    m = meta(minute_token="obsgREC0000000000000001", owner_open_id="ou_A",
             attendees=[Attendee(open_id="ou_A", union_id="on_A"),
                        Attendee(union_id="on_A"),              # trùng
                        Attendee(union_id="on_X"),              # đã thu hồi
                        Attendee(union_id="on_NGOAI"),          # chưa enroll
                        Attendee(open_id="ou_KHONG_CO_UNION")])
    db.note_viewer("obsgREC0000000000000001", "ou_B", "on_B", "Binh")
    db.note_viewer("obsgREC0000000000000001", "ou_A", "on_A", "An")   # trùng nguồn 1
    db.note_viewer("obsgREC0000000000000001", "ou_Z", "on_Z", "Z")    # chưa enroll
    rec, skipped = orchestrator._recipients(m)
    check("chỉ on_A + on_B, mỗi người đúng một lần", rec == ["on_A", "on_B"], str(rec))
    check("đếm đúng số người dự chưa cấp quyền", skipped == 2, f"skipped={skipped}")

    db.note_viewer("obsgV", "ou_A", "on_A", "An")
    first = db.viewers_of("obsgV")[0]["seen_at"]
    time.sleep(0.01)
    db.note_viewer("obsgV", "ou_A", "on_A", "An")
    check("note_viewer idempotent, giữ mốc LẦN ĐẦU",
          len(db.viewers_of("obsgV")) == 1
          and db.viewers_of("obsgV")[0]["seen_at"] == first)

    # =================================================================
    part("7. Phân quyền hỏi đáp — fail-closed")
    # =================================================================
    wipe_jobs()
    for tok, atts, owner in (
            ("mtA", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A"),
            ("mtB", [Attendee(open_id="ou_B", union_id="on_B")], "ou_B"),
            ("mtC", [], "ou_NGOAI")):
        jobstore.create(meta(minute_token=tok, title="Hop " + tok,
                             owner_open_id=owner, attendees=atts),
                        status="delivered")
    db.note_viewer("mtC", "ou_B", "on_B", "Binh")   # chuỗi tra lịch SÓT, Lark thì biết
    idx = qa.viewers_index()
    wA = askers.who("on_A", "ou_A", "An")
    wB = askers.who("on_B", "ou_B", "Binh")
    wAd = askers.who("on_ADMIN", "ou_ADMIN", "Admin")
    check("A xem được cuộc của A", qa._may_see("mtA", wA, idx))
    check("A KHÔNG xem được cuộc của B", not qa._may_see("mtB", wA, idx))
    check("B xem được mtC nhờ minute_viewers (tra lịch sót vẫn không mất quyền)",
          qa._may_see("mtC", wB, idx))
    check("admin xem được hết",
          all(qa._may_see(t, wAd, idx) for t in ("mtA", "mtB", "mtC")))
    check("who=None -> không xem được gì",
          not any(qa._may_see(t, None, idx) for t in ("mtA", "mtB", "mtC")))
    check("record không có job -> không ai xem được", not qa._may_see("mtZZ", wA, idx))
    check("who=None -> ba tool MCP đều từ chối",
          qa.list_meetings(None) == qa.NO_ASKER
          and qa.get_meeting(None, "Hop") == qa.NO_ASKER
          and qa.search_meetings(None, "x") == qa.NO_ASKER)
    check("who=None -> context rỗng (đường `v2 ask` cũng phải kín)",
          qa.context(None) == "")
    check("who=None -> hàng đợi không lộ cả CON SỐ", qa.pending_split(None) == ([], 0))
    pend, hidden = qa.pending_split(wB)
    check("B thấy đúng mtB + mtC trong hàng đợi",
          {p["minute_token"] for p in pend} == {"mtB", "mtC"})
    check("B được báo có 1 cuộc bị ẩn (biết mà đi hỏi, không tưởng là hết)",
          hidden == 1)

    # =================================================================
    part("8. Vé phiên")
    # =================================================================
    t1 = askers.issue("on_A", "ou_A", "An")
    check("nhắn lại -> dùng lại vé cũ", t1 == askers.issue("on_A", "ou_A", "An"))
    check("giải vé ra đúng người",
          (askers.resolve(t1) or {}).get("union_id") == "on_A")
    check("vé bịa -> None (KHÔNG phải 'cho xem hết')", askers.resolve("bia") is None)
    check("vé rỗng -> None", askers.resolve("") is None)
    t3 = askers.issue("on_B", "ou_B", "Binh")
    check("vé của B không giải ra A",
          t1 != t3 and (askers.resolve(t3) or {}).get("union_id") == "on_B")
    with db.tx() as c:
        c.execute("UPDATE qa_sessions SET expires_at=1 WHERE token=?", (t1,))
    check("vé hết hạn -> None", askers.resolve(t1) is None)
    check("union_id rỗng KHÔNG thành admin", askers.who("")["admin"] is False)
    check("admin nhận diện đúng", askers.who("on_ADMIN")["admin"] is True)

    # =================================================================
    part("9. Cảnh báo — chống spam, nhưng KHÔNG im vĩnh viễn")
    # =================================================================
    box: list[str] = []
    # Tắt MỌI phép kiểm khác của check_all: nhóm này đếm số DM, nên một mục
    # khác lỡ kêu là con số sai và phép kiểm nói dối. Hôm nay `_check_llm` và
    # `_check_run_stale` tự im trong môi trường selftest (không LLM_API_KEY,
    # không heartbeat) — nhưng dựa vào điều đó là dựa vào một thứ ở xa.
    keep5 = (alerts._send, alerts._check_whisper, alerts._check_tokens,
             alerts._check_llm, alerts._check_run_stale)
    alerts._send = lambda text: (box.append(text), True)[1]
    alerts._check_whisper = lambda out: None
    alerts._check_tokens = lambda out: None
    alerts._check_llm = lambda out: None
    alerts._check_run_stale = lambda out: None
    with db.tx() as c:
        c.execute("DELETE FROM alert_state")
        c.execute("UPDATE jobs SET status='failed', attempts=3, error='lý do X' "
                  "WHERE minute_token='mtA'")
    check("job failed -> báo lần đầu", len(alerts.check_all()) == 1)
    check("gọi lại -> im (chống spam)", len(alerts.check_all()) == 0)
    check("nội dung DM có lý do thật", "lý do X" in box[0])
    with db.tx() as c:
        c.execute("UPDATE jobs SET attempts=4 WHERE minute_token='mtA'")
    check("attempts đổi = tình trạng đổi -> báo lại", len(alerts.check_all()) == 1)
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='queued' WHERE minute_token='mtA'")
    alerts.check_all()
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='failed' WHERE minute_token='mtA'")
    check("được cứu rồi hỏng LẠI -> báo lại", len(alerts.check_all()) == 1)
    alerts._send = lambda text: False
    with db.tx() as c:
        c.execute("DELETE FROM alert_state")
        c.execute("UPDATE jobs SET attempts=9 WHERE minute_token='mtA'")
    check("gửi hỏng -> KHÔNG ghi mốc (không nuốt mất cảnh báo)",
          len(alerts.check_all()) == 0)
    alerts._send = lambda text: (box.append(text), True)[1]
    check("vòng sau gửi lại được", len(alerts.check_all()) == 1)
    (alerts._send, alerts._check_whisper, alerts._check_tokens,
     alerts._check_llm, alerts._check_run_stale) = keep5

    # =================================================================
    part("10. Lọc người dự / người vào họp")
    # =================================================================
    keep6, dropped = meetings._attendee_ids([
        {"type": "user", "user_id": "u1", "rsvp_status": "accept"},
        {"type": "user", "user_id": "u2", "rsvp_status": "decline"},
        {"type": "user", "user_id": "u3", "rsvp_status": "needs_action"},
        {"type": "user", "user_id": "u4", "rsvp_status": "DECLINE"},
        {"type": "user", "user_id": "u5"},
        {"type": "resource", "user_id": "phong-hop", "rsvp_status": "accept"},
        {"type": "chat", "user_id": "oc_group", "rsvp_status": "accept"},
        {"type": "user", "user_id": "", "rsvp_status": "accept"},
    ])
    check("giữ accept/needs_action/thiếu-trường, bỏ decline (cả HOA lẫn thường)",
          keep6 == ["u1", "u3", "u5"] and dropped == 2,
          f"keep={keep6} dropped={dropped}")
    check("bỏ phòng họp (resource) và group chat (chat)",
          "phong-hop" not in keep6 and "oc_group" not in keep6)

    # Ghép union_id <-> open_id theo `attendee_id`, KHÔNG theo thứ tự trả về.
    # Đo 02/08/2026 trên sự kiện thật 11 người: cùng một người có `attendee_id`
    # giống hệt ở cả hai lời gọi, chỉ `user_id` đổi dạng. Lark không hứa hai
    # lời gọi cùng thứ tự — và ghép lệch là gán danh tính sai, im lặng.
    keep6b = (lark_api.calendar_primary, lark_api.calendar_events,
              lark_api.event_meeting_ids, lark_api.event_attendees,
              meetings._meeting_ids_via_no)
    _t6 = 1_785_600_000
    lark_api.calendar_primary = lambda tok: "cal1"
    lark_api.calendar_events = lambda tok, cal, lo, hi: [
        {"event_id": "ev1", "summary": "Hop tuan",
         "start_time": {"timestamp": str(_t6)}}]
    lark_api.event_meeting_ids = lambda tok, cal, ids: {}
    meetings._meeting_ids_via_no = lambda tok, cal, ev: []
    # THỨ TỰ NGƯỢC NHAU có chủ ý giữa hai lời gọi.
    lark_api.event_attendees = lambda tok, cal, eid, id_type="union_id", **k: (
        [{"type": "user", "attendee_id": "a1", "user_id": "on_A"},
         {"type": "user", "attendee_id": "a2", "user_id": "on_B"}]
        if id_type == "union_id" else
        [{"type": "user", "attendee_id": "a2", "user_id": "ou_B"},
         {"type": "user", "attendee_id": "a1", "user_id": "ou_A"}])
    m6 = meta(minute_token="obsgPAIR0000000000001", title="Hop tuan")
    m6.start = _t6
    m6.attendees = []
    meetings.resolve_participants("tok", m6)
    pairs6 = sorted((a.union_id, a.open_id) for a in m6.attendees)
    check("ghép cặp theo attendee_id dù hai lời gọi trả NGƯỢC thứ tự",
          pairs6 == [("on_A", "ou_A"), ("on_B", "ou_B")],
          f"thực tế: {pairs6} (nguồn={m6.participants_source})")

    # Người chỉ có ở một phía: giữ lại, đứng riêng, KHÔNG ghép bừa.
    lark_api.event_attendees = lambda tok, cal, eid, id_type="union_id", **k: (
        [{"type": "user", "attendee_id": "a1", "user_id": "on_A"}]
        if id_type == "union_id" else
        [{"type": "user", "attendee_id": "a1", "user_id": "ou_A"},
         {"type": "user", "attendee_id": "a9", "user_id": "ou_LE"}])
    m6b = meta(minute_token="obsgPAIR0000000000002", title="Hop tuan")
    m6b.start = _t6
    m6b.attendees = []
    meetings.resolve_participants("tok", m6b)
    pairs6b = sorted((a.union_id, a.open_id) for a in m6b.attendees)
    check("hai lời gọi lệch nhau -> ghép được ai thì ghép, phần dư đứng riêng",
          pairs6b == [("", "ou_LE"), ("on_A", "ou_A")], f"thực tế: {pairs6b}")
    (lark_api.calendar_primary, lark_api.calendar_events,
     lark_api.event_meeting_ids, lark_api.event_attendees,
     meetings._meeting_ids_via_no) = keep6b

    keep7 = lark_api.vc_meeting_participants
    lark_api.vc_meeting_participants = lambda tok, mid, id_type="union_id": [
        {"id": "on_1", "user_type": 1, "is_external": False},
        {"id": "on_1", "user_type": 1, "is_external": False},    # vào 2 lần
        {"id": "on_room", "user_type": 2, "is_external": False},
        {"id": "on_pstn", "user_type": 3, "is_external": False},
        {"id": "on_ngoai", "user_type": 1, "is_external": True},
        {"id": "on_2", "user_type": "1", "is_external": False},  # kiểu chuỗi
        {"id": "", "user_type": 1},
    ]
    check("người vào họp: bỏ Rooms/PSTN/ngoài tenant, khử trùng, nhận user_type chuỗi",
          meetings._vc_joiners("tok", "mid") == ["on_1", "on_2"])
    lark_api.vc_meeting_participants = keep7

    for src, must in (
            ("calendar[verified]:Hop tuan +vc29 -decline2", ("29", "2", "Hop tuan")),
            ("calendar[near6m]:Hop A", ("6m", "Hop A")),
            ("no_match -> fallback:owner", ("chỉ gửi cho chủ minute",)),
            ("", ("không rõ",))):
        out = meetings.explain_source(src)
        check(f"explain_source đọc được: {src[:36] or '(rỗng)'}",
              all(x in out for x in must), out)

    # =================================================================
    part("11. Bóc JSON của LLM (đường Hermes trả kèm văn xuôi)")
    # =================================================================
    for label, raw in (
            ("JSON thuần", '{"summary":"S","decisions":["d1"],"action_items":[]}'),
            ("bọc ```json", 'Kết quả:\n```json\n{"summary":"S","decisions":["d1"],'
                            '"action_items":[]}\n```\nXong.'),
            ("bọc ``` trần", '```\n{"summary":"S","decisions":["d1"],'
                             '"action_items":[]}\n```'),
            ("văn xuôi hai đầu", 'Tôi đã đọc. {"summary":"S","decisions":["d1"],'
                                 '"action_items":[]} Xong nhé.')):
        r = summarize._parse(raw)
        check(f"lấy được decisions từ: {label}",
              r.summary == "S" and r.decisions == ["d1"],
              f"summary={r.summary!r} decisions={r.decisions}")
    check("không phải JSON -> giữ nguyên văn, KHÔNG mất dữ liệu",
          summarize._parse("Khong phai JSON").summary == "Khong phai JSON")
    r = summarize._parse('{"summary":"S","action_items":["việc dạng chuỗi"]}')
    check("action_items dạng chuỗi vẫn nhận", len(r.action_items) == 1)
    check("field null -> không nổ",
          summarize._parse('{"summary":null,"decisions":null}').decisions == [])

    # =================================================================
    part("12. Bóc mốc thời gian của whisper")
    # =================================================================
    s = transcribe._parse_segments(
        "[00:00:00 -> 00:00:05] a\n[00:00:05 -> 00:00:09] b", 9)
    check("có cả start lẫn end", len(s) == 2 and s[0].end == 5.0)
    s = transcribe._parse_segments("[00:00:00] a\n[00:00:07] b", 12)
    check("định dạng CŨ (không end) -> suy từ đoạn kế",
          len(s) == 2 and s[0].end == 7.0 and s[1].end == 12.0)
    s = transcribe._parse_segments("[00:00:00] a\ndòng nối tiếp", 5)
    check("dòng không có mốc -> gộp vào đoạn trước",
          len(s) == 1 and "nối tiếp" in s[0].text)
    check("không có mốc nào -> một đoạn phủ hết",
          transcribe._parse_segments("khong co moc", 30)[0].end == 30)
    check("chuỗi rỗng -> không nổ", len(transcribe._parse_segments("", 0)) == 1)

    # =================================================================
    part("13. Hạn task — bẫy lùi một ngày")
    # =================================================================
    from datetime import datetime, timezone
    ms = tasks._parse_due("2026-08-15")
    check("hạn neo nửa đêm UTC, KHÔNG phải nửa đêm +07",
          datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
          == "2026-08-15 00:00")
    check("định dạng sai -> None", tasks._parse_due("15/08/2026") is None)
    check("ngày không tồn tại -> None", tasks._parse_due("2026-02-30") is None)
    check("rỗng -> None", tasks._parse_due("") is None)

    # =================================================================
    part("14. Thẻ Lark — nội dung dài không làm vỡ giới hạn")
    # =================================================================
    card = cards.recap_card(meta(title="X" * 300), Recap(summary="Y" * 9000))
    check("tiêu đề thẻ <= 100 ký tự",
          len(card["header"]["title"]["content"]) <= 100)
    check("thân thẻ bị cắt, không để tràn",
          all(len(e.get("text", {}).get("content", "")) <= 4000
              for e in card["elements"]))

    # =================================================================
    part("15. Sao lưu — xoay vòng + phát hiện ĐỔI khóa Fernet")
    # =================================================================
    config.BACKUP_KEEP = 3
    for _ in range(5):
        backup.run_backup()
        time.sleep(1.05)                     # tên file theo giây
    check(f"giữ đúng {config.BACKUP_KEEP} bản mới nhất",
          len(backup.snapshots()) == 3, str(len(backup.snapshots())))
    import sqlite3
    con = sqlite3.connect("file:" + str(backup.snapshots()[0]) + "?mode=ro", uri=True)
    check("bản sao đọc được và toàn vẹn",
          con.execute("pragma integrity_check").fetchone()[0] == "ok")
    con.close()
    old_fp = backup.key_fingerprint()
    config.FERNET_KEY = "AAAA3U9PKWv2JOi8JCfWL5TCrXS71QxBxzLbIp6hXGA="
    backup.run_backup()
    prevs = list(config.KEY_BACKUP_PATH.parent.glob("*.prev-*"))
    check("đổi V2_FERNET_KEY -> GIỮ khóa cũ lại, không ghi đè",
          len(prevs) == 1 and old_fp in prevs[0].name,
          str([p.name for p in prevs]))

    # =================================================================
    part("16. Enroll — lưới kiểm scope phải chạy ở CẢ BA đường")
    # =================================================================
    # §22 lấy chính lưới này làm lý do dám cắt OAUTH_SCOPES 124 -> 18. Trước
    # 02/08/2026 nó chỉ chạy ở đường hộp thư Vercel, còn `v2 complete` (đường mà
    # V2_HANDOFF §4.2 bảo người vận hành dùng) thì không.
    checked: list[str] = []
    keep8 = (lark_api.exchange_code, lark_api.user_info,
             oauth._warn_if_missing_scopes, lark_api.im_send_text)
    lark_api.exchange_code = lambda code: {
        "access_token": "at", "refresh_token": "rt", "expires_in": 7200,
        "refresh_token_expires_in": 2592000, "scope": "a b"}
    lark_api.user_info = lambda at: {"open_id": "ou_NEW", "union_id": "on_NEW",
                                     "name": "Nguoi Moi"}
    oauth._warn_if_missing_scopes = lambda info: checked.append(info["open_id"])
    lark_api.im_send_text = lambda *a, **k: "om"

    try:
        oauth.complete("code", "nonce-bia")
        check("nonce bịa bị từ chối", False, "KHÔNG ném lỗi")
    except RuntimeError:
        check("nonce bịa bị từ chối", True)
    _, nx = oauth.start("ou_NEW")
    with db.tx() as c:
        c.execute("UPDATE oauth_nonce SET expires_at=1 WHERE nonce=?", (nx,))
    try:
        oauth.complete("code", nx)
        check("nonce hết hạn bị từ chối", False, "KHÔNG ném lỗi")
    except RuntimeError:
        check("nonce hết hạn bị từ chối", True)

    checked.clear()
    _, n1 = oauth.start("ou_NEW")
    oauth.complete("code", n1)
    check("đường `v2 complete` (dán tay) CÓ kiểm scope", checked == ["ou_NEW"])
    check("nonce tiêu sau khi dùng (dùng-một-lần)",
          db.conn().execute("SELECT 1 FROM oauth_nonce WHERE nonce=?",
                            (n1,)).fetchone() is None)
    checked.clear()
    _, n2 = oauth.start("ou_NEW")
    oauth_callback.oauth.complete("code", n2)
    check("đường `v2 enroll` (callback cục bộ) CÓ kiểm scope", checked == ["ou_NEW"])

    checked.clear()
    _, n3 = oauth.start("ou_NEW")
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            return {"pending": [{"code": "c", "state": n3}]}

    keep9 = httpx.get
    httpx.get = lambda *a, **k: _Resp()
    config.OAUTH_PULL_URL, config.STATUS_PUSH_SECRET = "http://x", "s"
    done = oauth.poll_pending()
    httpx.get = keep9
    check("đường hộp thư Vercel CÓ kiểm scope", checked == ["ou_NEW"])
    check("KHÔNG kiểm hai lần (không còn lời gọi thừa ở poll_pending)",
          len(checked) == 1 and len(done) == 1)

    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO enroll_invites"
                  "(union_id,user_id,name,nonce,sent_at,times) "
                  "VALUES ('on_NEW','u','Nguoi Moi','nc',1,1)")
    _, n4 = oauth.start("ou_NEW")
    oauth.complete("code", n4)
    check("mọi đường enroll đều xoá lời mời cũ",
          db.conn().execute("SELECT 1 FROM enroll_invites WHERE union_id='on_NEW'"
                            ).fetchone() is None)
    lark_api.exchange_code, lark_api.user_info, \
        oauth._warn_if_missing_scopes, lark_api.im_send_text = keep8

    # =================================================================
    part("17. Cửa vào bot hỏi đáp (gate)")
    # =================================================================
    box2: list[tuple[str, str]] = []
    keep10 = (lark_api.im_send_text, oauth.short_link)
    lark_api.im_send_text = lambda uid, text, **k: (box2.append((uid, text)), "om")[1]
    oauth.short_link = lambda u: ""
    check("thiếu union_id -> KHÔNG cho vào",
          gate.check("", name="Vo Danh")["decision"] == "wait")
    d = gate.check("on_A", name="An")
    check("đã enroll -> allow, kèm vé phiên",
          d["decision"] == "allow" and bool(d.get("asker_token")))
    check("vé từ gate giải ra đúng người",
          (askers.resolve(d["asker_token"]) or {}).get("union_id") == "on_A")
    box2.clear()
    d = gate.check("on_LA", name="Nguoi La")
    check("chưa enroll -> invite + gửi link, KHÔNG kèm vé",
          d["decision"] == "invite" and len(box2) == 1 and not d.get("asker_token"))
    d = gate.check("on_LA", name="Nguoi La")
    check("nhắn lại ngay -> wait, không gửi link thứ hai",
          d["decision"] == "wait" and not d.get("asker_token"))
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites")

    def _boom(uid, text, **k):
        raise lark_api.LarkError(230013, "chua phat hanh", "im_send")

    lark_api.im_send_text = _boom
    d = gate.check("on_HONG", name="X")
    check("gửi link HỎNG -> không ghi invite, vòng sau còn thử lại",
          d["decision"] == "wait" and db.conn().execute(
              "SELECT 1 FROM enroll_invites WHERE union_id='on_HONG'").fetchone() is None)
    lark_api.im_send_text, oauth.short_link = keep10

    # =================================================================
    part("18. Dashboard công khai — KHÔNG được lộ đường dẫn máy")
    # =================================================================
    # `…/api/status?json=1` đọc được bằng GET **không cần xác thực** (đo
    # 02/08/2026, HTTP 200). Mà `build_snapshot` nhét nguyên `doctor.collect()`
    # vào — và doctor in đường dẫn đầy đủ vì nó viết cho người ngồi trước máy.
    from v2 import status_push
    for raw, must_go in (
            (f"khóa ở {config.KEY_BACKUP_PATH}", str(config.KEY_BACKUP_PATH)),
            (f"đích: {config.BACKUP_DIR}", str(config.BACKUP_DIR)),
            ("bật E:\\whisper\\run-server.bat + đúng cổng", "E:\\whisper"),
            (f"db {config.DB_PATH}", str(config.DB_PATH))):
        out = status_push.scrub(raw)
        check(f"scrub xoá được: {must_go[:38]}", must_go not in out, out)
    check("scrub xoá cả tên người dùng Windows",
          str(Path.home()) not in status_push.scrub(f"x {Path.home()}\\y"))
    check("scrub xoá vân tay khóa Fernet (vô dụng với người xem từ xa)",
          backup.key_fingerprint() not in
          status_push.scrub(f"vân tay {backup.key_fingerprint()}"))
    check("scrub KHÔNG phá URL bình thường",
          status_push.scrub("http://localhost:8000") == "http://localhost:8000")
    check("scrub giữ nguyên chuỗi không có đường dẫn",
          status_push.scrub("hàng đợi: delivered=3") == "hàng đợi: delivered=3")

    snap = status_push.build_snapshot()
    import re as _re
    check("snapshot thật KHÔNG chứa tên người dùng Windows",
          str(Path.home()) not in _json.dumps(snap, ensure_ascii=False))
    leaks = [c for c in snap["checks"]
             if _re.search(r"[A-Za-z]:\\", f"{c['label']}{c['detail']}")]
    check("snapshot thật không còn đường dẫn ổ đĩa nào", not leaks, str(leaks))

    # =================================================================
    part("19. Mượn token đúng người (chủ bản ghi trước)")
    # =================================================================
    # Từ khi `build_meta` tra ra CHỦ THẬT, `owner_open_id` không còn luôn là
    # người đã enroll — nên thứ tự ứng viên mới là thứ giữ cho hai đường
    # (tải bản ghi, upload file vào Base) không rơi vào "người bất kỳ".
    m2 = meta(minute_token="obsgCAND00000000000001", owner_open_id="ou_CHU",
              attendees=[Attendee(open_id="ou_DU1"), Attendee(union_id="on_x")])
    db.note_viewer("obsgCAND00000000000001", "ou_VIEWER", "on_v", "V")
    cands = pipeline._reader_candidates(m2)
    check("thứ tự: chủ bản ghi -> người dự -> người Lark báo có dự",
          cands == ["ou_CHU", "ou_DU1", "ou_VIEWER"], str(cands))
    check("bỏ qua người dự không có open_id", "on_x" not in cands)
    m3 = meta(minute_token="obsgCAND00000000000001", owner_open_id="ou_CHU",
              attendees=[Attendee(open_id="ou_CHU")])
    check("không lặp lại cùng một người",
          pipeline._reader_candidates(m3).count("ou_CHU") == 1)

    # =================================================================
    part("20. Không có hai lượt xử lý hàng đợi chồng nhau")
    # =================================================================
    # `ws_listener` gọi `process_queue()` từ thread nền. Chồng lượt = phát cùng
    # một biên bản HAI LẦN cho tất cả người dự.
    wipe_jobs()
    mk_job("obsgLOCK000000000000001", attempts=0)
    trace: list[str] = []
    barrier = threading.Event()

    def _slow(m):
        trace.append("vào")
        barrier.wait(3.0)
        trace.append("ra")
        raise transcribe.TranscribeUnavailable("dừng ở đây cho gọn")

    pipeline.run_transcription = _slow
    th = threading.Thread(
        target=lambda: orchestrator.process_queue(dry_run=True), daemon=True)
    th.start()
    while "vào" not in trace:                  # chờ lượt 1 vào hẳn bên trong
        time.sleep(0.01)
    orchestrator.process_queue(dry_run=True)   # lượt 2, phải bị bỏ NGAY
    check("lượt thứ hai bị bỏ, không chạy song song", trace.count("vào") == 1,
          str(trace))
    barrier.set()
    th.join(5)
    orchestrator.process_queue(dry_run=True)
    check("lượt 1 xong thì khoá được nhả, lượt sau chạy bình thường",
          trace.count("vào") == 2, str(trace))
    pipeline.run_transcription = keep4

    # =================================================================
    part("21. Đọc .env chịu được BOM (Windows hay chèn)")
    # =================================================================
    import importlib
    import os as _os
    tmpdir = Path(config.DATA_DIR) / "envtest"
    tmpdir.mkdir(parents=True, exist_ok=True)
    fake_env = tmpdir / ".env"
    for label, prefix in (("có BOM", "﻿"), ("không BOM", "")):
        fake_env.write_text(prefix + "DONG_DAU_LA_CAU_HINH=xin_chao\n"
                                     "PAUSED=1\n", encoding="utf-8")
        got = {}
        for line in fake_env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                got[k.strip()] = v.strip()
        check(f"{label}: cấu hình ở DÒNG ĐẦU vẫn đọc được",
              got.get("DONG_DAU_LA_CAU_HINH") == "xin_chao", str(list(got)))
        check(f"{label}: công tắc PAUSED vẫn đọc được", got.get("PAUSED") == "1")
    # File .env THẬT của máy này: chỉ cần không mất key nào vì BOM.
    real_env = Path(config.__file__).resolve().parent / ".env"
    if real_env.exists():
        keys = [ln.partition("=")[0].strip()
                for ln in real_env.read_text(encoding="utf-8-sig").splitlines()
                if "=" in ln and not ln.strip().startswith("#")]
        check("v2/.env thật: không key nào dính ký tự lạ",
              all(k.isascii() and k.replace("_", "").isalnum() for k in keys),
              str([k for k in keys
                   if not (k.isascii() and k.replace("_", "").isalnum())]))

    # =================================================================
    part("22. Ai đọc được Base — cửa THỨ HAI vào cùng dữ liệu")
    # =================================================================
    # Mọi phân quyền của V2 nằm ở code (`qa._may_see`, `_recipients`), nhưng
    # cùng nội dung đó — kèm attachment NGUYÊN VĂN transcript — nằm trên Base,
    # và ACL của Base là thiết lập Lark thủ công ngoài repo. Bảng ánh xạ dưới
    # đây là thứ quyết định doctor báo xanh hay đỏ; sai một giá trị enum là báo
    # an toàn cho một Base đang mở toang. Đo thật 02/08/2026: Base của hệ thống
    # này đang ở `tenant_readable` + `external_access_entity=open`.
    from v2 import doctor as _doc

    def judge(ent, ext="closed"):
        rows = _doc.judge_base_access(
            [{"member_id": "ou_x", "member_type": "openid", "perm": "full_access"}],
            {"link_share_entity": ent, "external_access_entity": ext})
        return rows[-1]                       # dòng nói về chia sẻ bằng link

    check("link đóng -> OK", judge("closed")[0] == _doc.OK)
    check("tenant_readable -> WARN (cả công ty đọc được)",
          judge("tenant_readable")[0] == _doc.WARN)
    check("anyone_readable -> FAIL (ngoài công ty cũng đọc được)",
          judge("anyone_readable")[0] == _doc.FAIL)
    check("anyone_editable -> FAIL", judge("anyone_editable")[0] == _doc.FAIL)
    # Giá trị lạ KHÔNG được rơi về OK: Lark thêm enum mới thì mặc định phải là
    # "chưa biết, cảnh báo đi", không phải "im lặng coi như an toàn".
    check("giá trị enum LẠ -> WARN, không phải OK",
          judge("mot_gia_tri_moi_cua_lark")[0] == _doc.WARN)
    check("cảnh báo nói rõ transcript nguyên văn bị phơi",
          "NGUYÊN VĂN" in judge("tenant_readable")[2])
    check("có nói link chuyển được ra ngoài công ty",
          "RA NGOÀI" in judge("tenant_readable", ext="open")[2]
          and "RA NGOÀI" not in judge("tenant_readable", ext="closed")[2])
    rows = _doc.judge_base_access([], {"link_share_entity": "closed"})
    check("không ai được thêm vào -> vẫn có dòng liệt kê",
          rows[0][0] == _doc.OK and "0 người" in rows[0][1])

    # =================================================================
    part("23. Vòng `run` chết thì PHẢI có người báo")
    # =================================================================
    # Cái bẫy mà mục này chặn: `alerts.check_all()` được gọi TỪ TRONG vòng
    # `run`, nên `run` chết là mọi cảnh báo chết theo — đúng tính chất mà
    # `alerts` sinh ra để chữa. Phép kiểm này là mục DUY NHẤT chỉ có nghĩa khi
    # chạy từ ngoài (Task Scheduler -> `python -m v2 alerts`).
    box2: list[str] = []
    keep6 = (alerts._send, alerts._check_whisper, alerts._check_tokens,
             alerts._check_failed_jobs, alerts._check_llm)
    alerts._send = lambda text: (box2.append(text), True)[1]
    alerts._check_whisper = lambda out: None
    alerts._check_tokens = lambda out: None
    alerts._check_failed_jobs = lambda out: None
    alerts._check_llm = lambda out: None
    with db.tx() as c:
        c.execute("DELETE FROM alert_state")

    check("CHƯA từng chạy `run` -> im (không có gì để gọi là chết)",
          len(alerts.check_all()) == 0)

    alerts.note_run_alive()
    check("vừa có nhịp sống -> im", len(alerts.check_all()) == 0)

    old_beat = str(alerts._now_ms() - (config.ALERT_RUN_STALE_MIN + 5) * 60_000)
    alerts._mark_set(alerts._RUN_BEAT, old_beat)
    check("im quá ngưỡng -> BÁO", len(alerts.check_all()) == 1)
    check("nội dung nói rõ hậu quả token 7 ngày",
          "7 NGÀY" in box2[-1] or "7 ngày" in box2[-1])
    check("báo rồi -> im (chống spam)", len(alerts.check_all()) == 0)

    # `run` sống lại rồi chết LẦN NỮA thì phải báo lại — không thì mỗi cái DB
    # chỉ báo được đúng một lần trong cả đời nó.
    alerts.note_run_alive()
    alerts.check_all()
    alerts._mark_set(alerts._RUN_BEAT,
                     str(alerts._now_ms() - (config.ALERT_RUN_STALE_MIN + 5) * 60_000))
    check("sống lại rồi chết lần nữa -> báo lại", len(alerts.check_all()) == 1)

    keep_stale = config.ALERT_RUN_STALE_MIN
    config.ALERT_RUN_STALE_MIN = 0
    with db.tx() as c:
        c.execute("DELETE FROM alert_state")
    alerts._mark_set(alerts._RUN_BEAT, old_beat)
    check("ALERT_RUN_STALE_MIN=0 -> tắt hẳn", len(alerts.check_all()) == 0)
    config.ALERT_RUN_STALE_MIN = keep_stale

    (alerts._send, alerts._check_whisper, alerts._check_tokens,
     alerts._check_failed_jobs, alerts._check_llm) = keep6

    # =================================================================
    part("24. LLM recap chết — khác whisper, và phải báo riêng")
    # =================================================================
    # Whisper chết: job nằm chờ, không mất gì. LLM chết: mỗi job chỉ hoãn
    # RECAP_MAX_TRIES vòng rồi PHÁT ĐI với tóm tắt rỗng. Hai hậu quả khác nhau
    # nên hai cảnh báo khác nhau.
    box3: list[str] = []
    keep7 = (alerts._send, alerts._check_whisper, alerts._check_tokens,
             alerts._check_failed_jobs, alerts._check_run_stale, alerts._llm_alive)
    alerts._send = lambda text: (box3.append(text), True)[1]
    for fn in ("_check_whisper", "_check_tokens", "_check_failed_jobs",
               "_check_run_stale"):
        setattr(alerts, fn, lambda out: None)
    with db.tx() as c:
        c.execute("DELETE FROM alert_state")

    alerts._llm_alive = lambda: None
    check("provider ở XA / chưa cấu hình -> KHÔNG kết luận, không báo",
          len(alerts.check_all()) == 0)

    alerts._llm_alive = lambda: False
    alerts.check_all()                        # lần đầu: mới ghi mốc, chưa đủ giờ
    check("mới chết -> chưa báo ngay (chống báo động giả)", len(box3) == 0)
    alerts._mark_set(
        alerts._LLM_SINCE,
        str(alerts._now_ms() - (config.ALERT_LLM_AFTER_MIN + 5) * 60_000))
    check("chết đủ lâu -> BÁO", len(alerts.check_all()) == 1)
    check("nội dung phân biệt rõ với whisper (nói job vẫn bị PHÁT ĐI)",
          "PHÁT ĐI" in box3[-1])
    check("báo rồi -> im", len(alerts.check_all()) == 0)
    alerts._llm_alive = lambda: True
    alerts.check_all()
    check("LLM sống lại -> xoá mốc", alerts._mark_get(alerts._LLM_SINCE) is None)

    (alerts._send, alerts._check_whisper, alerts._check_tokens,
     alerts._check_failed_jobs, alerts._check_run_stale, alerts._llm_alive) = keep7

    # =================================================================
    part("25. Tóm tắt lỡ phát rỗng PHẢI vá lại được")
    # =================================================================
    # Trước 02/08/2026: `_recap_step` chịu phát bản trần sau RECAP_MAX_TRIES
    # lần, rồi job thành `delivered` và KHÔNG vòng nào nhặt lại. Tức LLM chết
    # ~15 phút (3 vòng x 5 phút) là cuộc họp đó vĩnh viễn không có tóm tắt, và
    # bot trả lời "không có tóm tắt" mãi mãi dù transcript vẫn nằm trên đĩa.
    check("nhận ra recap giữ chỗ", summarize.is_placeholder(
        summarize.placeholder("LLM chết")))
    check("recap RỖNG cũng tính là giữ chỗ (job cũ trước 31/07)",
          summarize.is_placeholder(Recap(summary="  ")))
    check("recap thật thì KHÔNG bị coi là giữ chỗ",
          not summarize.is_placeholder(Recap(summary="Chốt ngân sách Q4")))

    import json as _json
    tr2 = Transcript(minute_token="obsgFIX0000000000000001", lang="vi",
                     duration=60, engine="t",
                     segments=[Segment(start=0, end=5, text="noi dung that")])
    tpath = Path(config.TRANSCRIPT_DIR) / "fix.json"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(_json.dumps(tr2.to_json()), encoding="utf-8")

    def mk_delivered(tok, recap_summary):
        jobstore.create(meta(minute_token=tok, title="Hop " + tok),
                        status="queued")
        jobstore.set_status(
            tok, "delivered", transcript_path=str(tpath), recap_fails=3,
            delivered_at=123456,
            recap_json=_json.dumps({"summary": recap_summary, "decisions": [],
                                    "action_items": []}, ensure_ascii=False))

    keep8 = (summarize.summarize, __import__("v2.bitable", fromlist=["x"]).update_recap)
    from v2 import bitable as _bt
    hits: list[str] = []
    _bt.update_recap = lambda tok, rc: (hits.append(tok), True)[1]

    # (a) LLM vẫn chết -> KHÔNG được đổi gì cả
    wipe_jobs()
    mk_delivered("obsgFIX0000000000000001", "Chưa sinh được recap — LLM chết")
    summarize.summarize = lambda t, m: (_ for _ in ()).throw(
        summarize.RecapUnavailable("vẫn chết"))
    orchestrator._backfill_recaps()
    r = jobstore.get("obsgFIX0000000000000001")
    check("LLM còn chết -> job giữ nguyên delivered, Base không bị đụng",
          r["status"] == "delivered" and not hits)

    # (b) LLM sống lại -> vá recap, và TUYỆT ĐỐI không phát lại
    summarize.summarize = lambda t, m: Recap(summary="Tom tat that",
                                             decisions=["chot A"])
    orchestrator._backfill_recaps()
    r = jobstore.get("obsgFIX0000000000000001")
    check("vá xong: recap trong DB là bản THẬT",
          "Tom tat that" in (r["recap_json"] or ""))
    # Đây là phép kiểm QUAN TRỌNG NHẤT của nhóm: `pipeline.run_recap` đặt
    # status='recapping', mà `process_queue` nhặt đúng status đó. Quên trả về
    # `delivered` là vòng sau PHÁT LẠI biên bản cho toàn bộ người dự.
    check("job vẫn `delivered` -> KHÔNG bị phát lại cho người dự",
          r["status"] == "delivered")
    check("giữ nguyên mốc delivered_at cũ", r["delivered_at"] == 123456)
    check("recap_fails được đặt lại", (r["recap_fails"] or 0) == 0)
    check("Base được cập nhật đúng một lần", hits == ["obsgFIX0000000000000001"])

    # (c) recap đã thật thì đừng đụng vào nữa (không đốt lời gọi LLM mỗi vòng)
    hits.clear()
    calls: list[int] = []
    summarize.summarize = lambda t, m: (calls.append(1),
                                        Recap(summary="lai nua"))[1]
    orchestrator._backfill_recaps()
    check("recap đã thật -> không gọi LLM lại, không ghi Base lại",
          not calls and not hits)

    # (d) trần mỗi vòng: không nuốt cả vòng sau một đợt LLM chết dài
    wipe_jobs()
    for i in range(orchestrator.BACKFILL_RECAPS_PER_ROUND + 2):
        mk_delivered(f"obsgFIX000000000000000{i}", "Chưa sinh được recap — x")
    calls.clear()
    summarize.summarize = lambda t, m: (calls.append(1),
                                        Recap(summary="ok that"))[1]
    orchestrator._backfill_recaps()
    check(f"tối đa {orchestrator.BACKFILL_RECAPS_PER_ROUND} recap mỗi vòng",
          len(calls) == orchestrator.BACKFILL_RECAPS_PER_ROUND, str(len(calls)))

    summarize.summarize, _bt.update_recap = keep8

    # =================================================================
    part("26. Bot CHỈ trả lời chat 1-1, không trả lời trong group")
    # =================================================================
    # Lý do KHÔNG phải "agent lẫn vé của hai người" — Hermes để
    # `group_sessions_per_user: true` nên mỗi người trong group đã là một
    # session riêng. Lý do thật: `qa._may_see` cấp quyền cho NGƯỜI HỎI, còn câu
    # trả lời thì CẢ PHÒNG đọc được. Hôm nay còn hai lớp chặn ngoài repo
    # (FEISHU_GROUP_POLICY, plugin); đây là lớp duy nhất có test.
    add_user("ou_g1", "on_g1", "Nguoi Trong Group")
    for ct in ("group", "channel", "thread", "GROUP"):
        r = gate.check("on_g1", name="x", send=False, chat_type=ct)
        check(f"chat_type={ct!r} -> KHÔNG cho vào",
              r["decision"] != "allow", str(r))
    for ct in ("dm", "p2p", "DM"):
        r = gate.check("on_g1", name="x", send=False, chat_type=ct)
        check(f"chat_type={ct!r} -> cho vào bình thường",
              r["decision"] == "allow", str(r))
    # Plugin đời cũ chưa gửi trường này. CHO ĐI TIẾP có chủ ý: `v2-gate.bat`
    # spawn Python mới nên ăn code mới ngay, còn plugin phải restart gateway
    # mới cập nhật — đóng ở đây là bot câm với TẤT CẢ trong khoảng giữa.
    r = gate.check("on_g1", name="x", send=False)
    check("chat_type rỗng (plugin đời cũ) -> vẫn cho vào",
          r["decision"] == "allow", str(r))
    # Chặn phải xảy ra TRƯỚC khi cấp vé: vé cấp ra cho một ngữ cảnh mà câu trả
    # lời sẽ bị người khác đọc là đã hỏng rồi, dù sau đó có chặn.
    r = gate.check("on_g1", name="x", send=False, chat_type="group")
    check("chặn group thì KHÔNG cấp vé phiên", not r.get("asker_token"), str(r))
    # Người CHƯA enroll nhắn trong group: không được gửi link vào phòng chung.
    r = gate.check("on_chua_enroll", name="y", send=False, chat_type="group")
    check("người chưa enroll trong group -> không mời, không nhắn",
          r["decision"] == "wait" and "nonce" not in r, str(r))

    # =================================================================
    part("27. Thử lại khi Lark chập — CHỈ cho lời gọi ĐỌC")
    # =================================================================
    # Trước 02/08/2026 không có một dòng retry nào. Mà tra người dự cho MỘT
    # cuộc họp tốn hàng chục lời gọi, và một cú 429 lẻ ở giữa là danh sách
    # người nhận sai — im lặng.
    import httpx as _httpx

    seen: list[tuple[str, str]] = []

    class _FakeTransport(lark_api._RetryTransport):
        """Đếm số lần gọi thật. `codes` là chuỗi mã trả về lần lượt."""

        def __init__(self, codes):
            super().__init__()
            self.codes = list(codes)

        def _send_single_request(self, request):    # noqa: D401
            raise AssertionError("khong duoc goi toi mang")

        def handle_request(self, request):
            # Chặn ở lớp dưới cùng: gọi lại logic thử lại của lớp cha nhưng
            # thay phần đi mạng bằng response giả.
            return lark_api._RetryTransport.handle_request(self, request)

    def _fake_super(codes):
        """Giả `httpx.HTTPTransport.handle_request` -> tuần tự các mã trong codes."""
        box = {"i": 0}

        def fn(self, request):
            seen.append((request.method, request.headers.get("x-v2-read", "-")))
            i = min(box["i"], len(codes) - 1)
            box["i"] += 1
            return _httpx.Response(codes[i], content=b"{}", request=request)
        return fn

    keep9 = (_httpx.HTTPTransport.handle_request, time.sleep)
    time.sleep = lambda s: None                     # đừng chờ thật trong test

    def run_case(method, codes, headers=None):
        seen.clear()
        _httpx.HTTPTransport.handle_request = _fake_super(codes)
        t = lark_api._RetryTransport()
        req = _httpx.Request(method, "https://x/y", headers=headers or {})
        resp = t.handle_request(req)
        return resp, len(seen)

    _, n = run_case("GET", [429, 429, 200])
    check("GET gặp 429 -> thử lại tới khi được", n == 3, f"{n} lần gọi")
    _, n = run_case("GET", [500, 200])
    check("GET gặp 500 -> thử lại", n == 2, f"{n} lần gọi")
    resp, n = run_case("GET", [503, 503, 503])
    check("GET hỏng mãi -> dừng đúng _RETRY_CALLS, trả response cuối",
          n == lark_api._RETRY_CALLS and resp.status_code == 503, f"{n} lần gọi")
    _, n = run_case("GET", [404])
    check("GET 404 -> KHÔNG thử lại (lỗi của ta, không phải của Lark)", n == 1)
    # QUAN TRỌNG NHẤT: POST thường không được thử lại. `im_send_card` là POST,
    # và phát biên bản hai lần cho cả phòng họp là thứ ai cũng nhìn thấy.
    _, n = run_case("POST", [429, 200])
    check("POST 429 -> KHÔNG thử lại (rủi ro gửi TRÙNG)", n == 1, f"{n} lần gọi")
    _, n = run_case("POST", [429, 200], headers=dict(lark_api.READ_ONLY))
    check("POST tự khai chỉ-đọc -> ĐƯỢC thử lại", n == 2, f"{n} lần gọi")
    check("header nội bộ x-v2-read KHÔNG bị gửi ra ngoài",
          all(h == "-" for _, h in seen), str(seen))

    # `Retry-After` của Lark được tôn trọng, nhưng có TRẦN: một header hỏng
    # (`Retry-After: 3600`) không được treo cả vòng run.
    r429 = _httpx.Response(429, headers={"retry-after": "2"},
                           request=_httpx.Request("GET", "https://x"))
    check("tôn trọng Retry-After", lark_api._retry_after_s(r429, 0) == 2.0)
    rbig = _httpx.Response(429, headers={"retry-after": "3600"},
                           request=_httpx.Request("GET", "https://x"))
    check("Retry-After quá lớn bị chặn trần",
          lark_api._retry_after_s(rbig, 0) == lark_api._RETRY_CAP_S)
    rno = _httpx.Response(429, request=_httpx.Request("GET", "https://x"))
    check("không có Retry-After -> lùi theo cấp số nhân",
          lark_api._retry_after_s(rno, 0) < lark_api._retry_after_s(rno, 2))

    _httpx.HTTPTransport.handle_request, time.sleep = keep9

    # =================================================================
    part("28. Tra LẠI người dự khi lần đầu thất bại")
    # =================================================================
    # `meta` chốt đúng một lần lúc enqueue rồi đông cứng: một cú LarkError
    # thoáng qua ở calendar là job mang danh sách sai VĨNH VIỄN.
    check("nguồn đã tra được -> không tra lại",
          not orchestrator._needs_reresolve("calendar[verified]:Hop tuan +vc3"))
    for src in ("agenda_failed", "no_match", "no_calendar_event",
                "no_event_in_window", "no_start_time",
                "no_calendar_event -> fallback:owner"):
        check(f"nguồn {src!r} -> phải tra lại",
              orchestrator._needs_reresolve(src))

    add_user("ou_rr", "on_rr", "Nguoi Cho Muon Token")
    keep10 = (meetings.resolve_participants, tokenstore.get_access_token)
    tokenstore.get_access_token = lambda oid: "tok"

    m_bad = meta(minute_token="obsgRR00000000000000001",
                 owner_open_id="ou_rr",
                 participants_source="agenda_failed")
    m_bad.attendees = [Attendee(open_id="ou_rr", union_id="on_rr")]
    jobstore.create(m_bad)

    def _ok(tokn, mm):
        mm.attendees = [Attendee(open_id="ou_rr", union_id="on_rr"),
                        Attendee(open_id="ou_x2", union_id="on_x2")]
        mm.participants_source = "calendar[verified]:Hop tuan"
        return mm
    meetings.resolve_participants = _ok
    got = orchestrator._maybe_reresolve(m_bad, dry_run=False)
    check("tra lại thành công -> dùng danh sách mới",
          got.participants_source.startswith("calendar[")
          and len(got.attendees) == 2, got.participants_source)
    saved = jobstore.meta_from_json(
        jobstore.get("obsgRR00000000000000001")["meta_json"])
    check("kết quả tra lại được LƯU vào job",
          len(saved.attendees) == 2, str(len(saved.attendees)))

    # Nhánh nguy hiểm nhất: tra lại HỎNG thì phải giữ nguyên cái đang có.
    # `resolve_participants` sửa TẠI CHỖ và xoá trắng attendees khi thất bại,
    # nên làm thẳng trên meta là tra lại khiến mọi thứ TỆ ĐI.
    m_fb = meta(minute_token="obsgRR00000000000000002", owner_open_id="ou_rr",
                participants_source="no_calendar_event -> fallback:owner")
    m_fb.attendees = [Attendee(open_id="ou_rr", union_id="on_rr")]
    jobstore.create(m_fb)

    def _fail(tokn, mm):
        mm.attendees = []
        mm.participants_source = "agenda_failed"
        return mm
    meetings.resolve_participants = _fail
    got = orchestrator._maybe_reresolve(m_fb, dry_run=False)
    check("tra lại thất bại -> GIỮ NGUYÊN người dự cũ, không xoá trắng",
          len(got.attendees) == 1
          and got.participants_source == "no_calendar_event -> fallback:owner",
          f"{len(got.attendees)} người, {got.participants_source!r}")
    still = jobstore.meta_from_json(
        jobstore.get("obsgRR00000000000000002")["meta_json"])
    check("thất bại thì KHÔNG ghi đè meta trong DB", len(still.attendees) == 1)

    # dry-run: được phép tra (chỉ đọc) nhưng KHÔNG được ghi.
    m_dry = meta(minute_token="obsgRR00000000000000003", owner_open_id="ou_rr",
                 participants_source="agenda_failed")
    m_dry.attendees = [Attendee(open_id="ou_rr", union_id="on_rr")]
    jobstore.create(m_dry)
    meetings.resolve_participants = _ok
    orchestrator._maybe_reresolve(m_dry, dry_run=True)
    dry_saved = jobstore.meta_from_json(
        jobstore.get("obsgRR00000000000000003")["meta_json"])
    check("dry-run KHÔNG ghi meta mới vào DB", len(dry_saved.attendees) == 1,
          str(len(dry_saved.attendees)))

    # Không mượn được token của ai thì im lặng đi tiếp, đừng ném.
    tokenstore.get_access_token = lambda oid: (_ for _ in ()).throw(
        tokenstore.TokenError("chua enroll"))
    got = orchestrator._maybe_reresolve(m_fb, dry_run=False)
    check("không ai cho mượn token -> giữ nguyên, không ném",
          len(got.attendees) == 1)

    meetings.resolve_participants, tokenstore.get_access_token = keep10

    # =================================================================
    part("29. Nhãn 'token sắp hết' không được lúc nào cũng đỏ")
    # =================================================================
    # `auth_report` từng đánh OK khi `days > 7`. Nhưng refresh token của Lark
    # sống ĐÚNG 7 ngày và TRƯỢT (đo 02/08/2026: refresh_exp = updated_at + 7d
    # cho cả ba người, dù enroll ba ngày khác nhau), nên `days > 7` không bao
    # giờ đúng và MỌI người luôn hiện `[SẮP HẾT]`, kể cả token vừa gia hạn.
    # Nhãn lúc nào cũng đỏ là nhãn người ta thôi đọc.
    check("ngưỡng cảnh báo THẤP hơn 7 ngày (cửa sổ trượt của Lark)",
          tokenstore.WARN_DAYS < 7, str(tokenstore.WARN_DAYS))
    check("DM hiếm hơn doctor (alerts.TOKEN_DAYS < WARN_DAYS)",
          alerts.TOKEN_DAYS < tokenstore.WARN_DAYS,
          f"{alerts.TOKEN_DAYS} vs {tokenstore.WARN_DAYS}")

    with db.tx() as c:
        c.execute("DELETE FROM tokens")
    now_ms = int(time.time() * 1000)

    def add_tok(name, days):
        with db.tx() as c:
            c.execute(
                "INSERT INTO tokens(open_id,union_id,name,access_enc,refresh_enc,"
                "access_exp,refresh_exp,scopes,status,enrolled_at,updated_at,"
                "last_used) VALUES (?,?,?,'','',?,?,'','active',0,0,0)",
                (f"ou_{name}", f"on_{name}", name, now_ms,
                 now_ms + int(days * 86_400_000)))

    # Token VỪA gia hạn: với cửa sổ trượt thì đây là trạng thái BÌNH THƯỜNG
    # nhất, và nó phải hiện OK.
    add_tok("VuaGiaHan", 7.0)
    add_tok("SapHet", 2.0)
    add_tok("HetHan", -1.0)
    rep = tokenstore.auth_report()
    line = {l.split()[0]: l for l in
            (x.strip() for x in rep.splitlines()) if l}
    check("token vừa gia hạn (còn 7 ngày) -> OK, KHÔNG phải SẮP HẾT",
          "[OK]" in line["VuaGiaHan"], line["VuaGiaHan"])
    check("token còn 2 ngày -> SẮP HẾT", "[SẮP HẾT]" in line["SapHet"],
          line["SapHet"])
    check("token quá hạn -> HẾT HẠN", "[HẾT HẠN]" in line["HetHan"],
          line["HetHan"])
    with db.tx() as c:
        c.execute("DELETE FROM tokens")

    # =================================================================
    part("30. Transcript RỖNG không được đi tiếp như thành công")
    # =================================================================
    # Đo thật 02/08/2026, job `test` (obsg22ct6md6ogbe3hi1i782): whisper trả 1
    # segment `text: ""` cho 61,7s audio. Không có phép kiểm nào chặn, nên:
    # file .txt 0 byte -> `im_upload` 234010 -> `base_media_upload` 1061002 ->
    # NHƯNG job vẫn `delivered`, `error=NULL`, Base vẫn có record, và
    # `deliveries` chỉ có dòng `recap`. Mọi bảng trạng thái đều nói "xong".
    # `word_count` lúc đó chỉ được dùng để IN ra log, không ai kiểm nó.
    keep30 = (pipeline.download_recording, transcribe.transcribe)

    def _fake_dl(m):
        return Path(str(config.WORK_DIR / f"{m.minute_token}.wav")), "ou_owner"

    empty_tr = Transcript(minute_token="obsgEMPTY00000000000001", lang="vi",
                          duration=61.7, engine="faster-whisper/small",
                          segments=[Segment(0, 61.7, "")])
    check("bản ghi im lặng -> word_count = 0", empty_tr.word_count == 0)

    wipe_jobs()
    mk_job("obsgEMPTY00000000000001", attempts=0)
    config.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    pipeline.download_recording = _fake_dl
    transcribe.transcribe = lambda a, mt, meeting_title="", **kw: empty_tr
    try:
        pipeline.run_transcription(meta(minute_token="obsgEMPTY00000000000001"))
        out30 = "KHÔNG NÉM"
    except pipeline.EmptyTranscript:
        out30 = "EmptyTranscript"
    except Exception as exc:                              # noqa: BLE001
        out30 = f"lỗi khác: {exc}"
    check("phiên âm ra 0 từ -> ném EmptyTranscript", out30 == "EmptyTranscript",
          out30)
    # Không lưu đường dẫn = `_reuse` không nạp lại bản rỗng rồi phát mãi.
    check("transcript RỖNG KHÔNG được lưu transcript_path",
          not (jobstore.get("obsgEMPTY00000000000001") or {}).get("transcript_path"))
    check("vẫn ghi .json ra đĩa làm bằng chứng chẩn lỗi",
          any(p.name.endswith("obsgEMPTY00000000000001.json")
              for p in config.TRANSCRIPT_DIR.glob("*.json")))
    pipeline.download_recording, transcribe.transcribe = keep30

    # File 0 byte: đừng gọi Lark để nhận đúng câu 234010, và PHẢI để lại dấu
    # vết. `deliveries` là thứ duy nhất trả lời được "ai đã nhận gì".
    keep30b = (pipeline.write_doc, lark_api.im_upload_file, lark_api.im_send_card)
    empty_txt = config.TRANSCRIPT_DIR / "rong.txt"
    empty_txt.write_text("", encoding="utf-8")
    called = []
    pipeline.write_doc = lambda t, m: empty_txt
    lark_api.im_upload_file = lambda *a, **k: called.append("upload")
    lark_api.im_send_card = lambda *a, **k: None
    wipe_jobs()
    mk_job("obsgEMPTY00000000000002", attempts=0)
    sent30, failed30 = pipeline.deliver(
        meta(minute_token="obsgEMPTY00000000000002"), Recap(summary="x"),
        empty_tr, ["on_A"], dry_run=False)
    rows30 = list(db.conn().execute(
        "SELECT kind, ok, error FROM deliveries WHERE minute_token=?",
        ("obsgEMPTY00000000000002",)))
    kinds30 = {r["kind"]: r for r in rows30}
    check("file 0 byte -> KHÔNG gọi im_upload_file", not called, str(called))
    check("vẫn gửi được thẻ tóm tắt", sent30 == ["on_A"] and not failed30)
    check("có dòng deliveries 'full' ok=0 — không im lặng bỏ qua",
          "full" in kinds30 and kinds30["full"]["ok"] == 0,
          f"deliveries thực tế: {[dict(r) for r in rows30]}")
    check("dòng đó nói RÕ vì sao",
          "rỗng" in (kinds30.get("full", {})["error"] or "")
          if "full" in kinds30 else False)
    pipeline.write_doc, lark_api.im_upload_file, lark_api.im_send_card = keep30b

    # =================================================================
    part("33. Một người token chập không được làm mù cả vòng quét")
    # =================================================================
    # `get_access_token` gọi `refresh_user_token` (POST, KHÔNG có retry). Một cú
    # 429 khi làm mới token của người thứ nhất từng bay thẳng ra khỏi vòng
    # `for u in users` -> hai người còn lại không được quét gì trong vòng đó.
    with db.tx() as c:
        c.execute("DELETE FROM tokens")
    add_user("ou_CHAP", "on_CHAP", "Token chap")
    add_user("ou_LANH", "on_LANH", "Binh thuong")
    keep33 = (tokenstore.get_access_token, lark_api.minutes_list)
    seen33: list = []

    def _gat33(oid):
        if oid == "ou_CHAP":
            raise lark_api.LarkError(429, "too many requests", "oauth/token")
        return "tok"

    tokenstore.get_access_token = _gat33
    lark_api.minutes_list = lambda tok, s, e, oid: (seen33.append(oid), [])[1]
    orchestrator.scan_once()
    check("người sau VẪN được quét dù người trước lỗi token",
          seen33 == ["ou_LANH"], f"đã quét: {seen33}")
    tokenstore.get_access_token, lark_api.minutes_list = keep33
    with db.tx() as c:
        c.execute("DELETE FROM tokens")

    # Cùng hình dạng, tầng khác: hộp thư enroll nằm trên VERCEL còn quét/xử lý
    # nói chuyện với LARK. Để chung một khối `try` thì Vercel không gọi được là
    # `scan_once` + `process_queue` không chạy vòng đó, dù Lark vẫn sống. Đã
    # thấy thật trong log lúc mạng rớt:
    #   [enroll] không đọc được hộp thư: [Errno 11001] getaddrinfo failed
    #   [loop] lỗi vòng lặp: [Errno 11001] getaddrinfo failed
    # Kiểm bằng cấu trúc nguồn vì `run()` là vòng lặp vô hạn, không gọi thẳng
    # một lượt được.
    import inspect as _inspect
    _src = _inspect.getsource(orchestrator.run).splitlines()
    _i_poll = next(i for i, l in enumerate(_src) if "oauth.poll_pending()" in l)
    _i_scan = next(i for i, l in enumerate(_src) if "scan_once()" in l)
    check("hộp thư enroll và vòng quét KHÔNG chung một khối try",
          "except" in "\n".join(_src[_i_poll:_i_scan]),
          "poll_pending hỏng sẽ kéo theo scan_once + process_queue")

    # =================================================================
    part("32. Gửi hỏng MỘT PHẦN phải được gửi bù, và chỉ cho đúng người")
    # =================================================================
    # `_deliver_now` chỉ giữ `queued` khi hỏng với TẤT CẢ (`failed and not
    # sent`). Hỏng một phần -> job `delivered` -> người đó mất biên bản vĩnh
    # viễn dù dòng ok=0 nằm sẵn trong `deliveries`. Base và recap đều đã có
    # đường vá cho hình dạng lỗi này; việc PHÁT thì chưa, mà nó là bước duy
    # nhất người dùng thật sự nhìn thấy.
    import json as _j32
    _tok32 = "obsgBF00000000000000001"
    wipe_jobs()
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    mk_job(_tok32, attempts=0)
    _tp32 = config.TRANSCRIPT_DIR / "backfill-deliveries.json"
    _tp32.write_text(_j32.dumps(Transcript(
        minute_token=_tok32, lang="vi", duration=10, engine="t",
        segments=[Segment(0, 10, "noi dung that")]).to_json()), encoding="utf-8")
    jobstore.set_status(_tok32, "delivered", transcript_path=str(_tp32),
                        recap_json=_j32.dumps({"summary": "tom tat that",
                                               "decisions": [],
                                               "action_items": []}))
    jobstore.record_delivery(_tok32, "on_OK", "recap", True)
    jobstore.record_delivery(_tok32, "on_OKSAU", "recap", False, "429")
    jobstore.record_delivery(_tok32, "on_OKSAU", "recap", True)      # rồi được
    jobstore.record_delivery(_tok32, "on_HONG", "recap", False, "429")
    for _ in range(3):                                               # quá hạn mức
        jobstore.record_delivery(_tok32, "on_BOCUOC", "recap", False, "230013")

    _pend32 = jobstore.pending_recipients(_tok32, "recap", 3)
    check("người đã nhận được -> KHÔNG gửi lại", "on_OK" not in _pend32)
    check("hỏng rồi sau đó nhận được -> KHÔNG gửi lại", "on_OKSAU" not in _pend32)
    check("hỏng và chưa lần nào nhận được -> có trong danh sách gửi bù",
          "on_HONG" in _pend32, str(_pend32))
    check("quá 3 lần thử -> thôi, không thử mãi", "on_BOCUOC" not in _pend32)

    # Chặn CẢ HAI đường gửi. Bản đầu chỉ chặn `deliver`, nên `deliver_file`
    # THẬT đã chạy và gọi Lark thật (`im_send_file` -> 99992364) ngay giữa
    # selftest — đúng thứ luật 2 của file này cấm. Nó chỉ không gây hại vì
    # `on_OK`/`on_OKSAU` là union_id bịa; trùng một id thật là gửi tin thật.
    keep32 = (pipeline.deliver, pipeline.deliver_file)
    got32: list = []
    file32a: list = []
    pipeline.deliver = lambda m, r, t, recips, **k: (got32.extend(recips),
                                                     (recips, []))[1]
    pipeline.deliver_file = lambda m, t, recips: (file32a.extend(recips),
                                                  (recips, []))[1]
    orchestrator._backfill_deliveries()
    check("gửi bù CHỈ cho người chưa nhận được, không phát lại cả phòng",
          got32 == ["on_HONG"], f"thực tế gửi cho: {got32}")

    # Job mà mọi người hỏng đều đã quá hạn mức: không được gọi gửi nữa.
    got32.clear()
    with db.tx() as c:
        c.execute("DELETE FROM deliveries WHERE recipient <> 'on_BOCUOC'")
    orchestrator._backfill_deliveries()
    check("hết lượt thử -> KHÔNG gọi gửi nữa (khỏi đốt API mỗi vòng)",
          got32 == [], f"vẫn gọi cho: {got32}")
    # --- Nhận được THẺ mà chưa có FILE: ca riêng, đường gửi riêng -----------
    # `deliver` luôn gửi thẻ trước rồi mới tới file, nên gọi lại nó cho người
    # này là họ nhận THẺ TRÙNG. Đây là mảnh còn thiếu của chính bản vá trên:
    # `pending_recipients(kind="recap")` loại họ ra vì thẻ đã `ok=1`.
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    jobstore.record_delivery(_tok32, "on_CHUAFILE", "recap", True)   # chưa có full
    jobstore.record_delivery(_tok32, "on_DU", "recap", True)
    jobstore.record_delivery(_tok32, "on_DU", "full", True)
    jobstore.record_delivery(_tok32, "on_HETLUOT", "recap", True)
    for _ in range(3):
        jobstore.record_delivery(_tok32, "on_HETLUOT", "full", False, "429")
    jobstore.record_delivery(_tok32, "on_CHUATHE", "recap", False, "429")

    _pf32 = jobstore.pending_file_recipients(_tok32, 3)
    check("nhận thẻ rồi mà KHÔNG có dòng file -> phải gửi bù file",
          "on_CHUAFILE" in _pf32, str(_pf32))
    check("đã có file -> thôi", "on_DU" not in _pf32)
    check("file hỏng quá 3 lần -> thôi", "on_HETLUOT" not in _pf32)
    check("chưa nhận được thẻ -> KHÔNG thuộc ca này (đi đường thẻ+file)",
          "on_CHUATHE" not in _pf32)

    file32: list = []
    got32.clear()
    pipeline.deliver_file = lambda m, t, recips: (file32.extend(recips),
                                                  (recips, []))[1]
    orchestrator._backfill_deliveries()
    check("gửi bù file CHỈ cho người thiếu file", file32 == ["on_CHUAFILE"],
          f"thực tế: {file32}")
    check("và KHÔNG gửi lại thẻ cho họ (tránh thẻ trùng)",
          "on_CHUAFILE" not in got32, f"deliver được gọi với: {got32}")
    check("người chưa nhận thẻ vẫn đi đường thẻ+file",
          got32 == ["on_CHUATHE"], f"thực tế: {got32}")

    # Job có transcript RỖNG: không được gọi gửi bù file, VÀ không được lặp
    # mỗi vòng. Thấy trong log production 5 phút sau khi bản vá đầu chạy:
    # `deliver_file` cố ý không ghi lần thử nào cho ca rỗng, nên vòng sau lại
    # trả đúng người đó — dòng log mỗi 5 phút mãi mãi + chiếm một suất gửi bù.
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    jobstore.record_delivery(_tok32, "on_CHUAFILE", "recap", True)
    _tpe32 = config.TRANSCRIPT_DIR / "backfill-rong.json"
    _tpe32.write_text(_j32.dumps(Transcript(
        minute_token=_tok32, lang="vi", duration=61.7, engine="t",
        segments=[Segment(0, 61.7, "")]).to_json()), encoding="utf-8")
    jobstore.set_status(_tok32, "delivered", transcript_path=str(_tpe32))
    file32.clear()
    orchestrator._backfill_deliveries()
    check("job transcript RỖNG -> KHÔNG gọi gửi bù file (khỏi lặp mỗi vòng)",
          file32 == [], f"vẫn gọi cho: {file32}")
    jobstore.set_status(_tok32, "delivered", transcript_path=str(_tp32))

    # Transcript rỗng ở tầng dưới: KHÔNG gọi API, và KHÔNG ghi thêm lần thử —
    # thử lại một thứ không tồn tại chỉ làm `deliveries` nói dối là đã cố.
    # Từ đây chạy hàm THẬT (bỏ stub), nên phải chặn ở tầng lark_api bên dưới.
    pipeline.deliver_file = keep32[1]
    keep32c = (pipeline.write_doc, lark_api.im_upload_file)
    pipeline.write_doc = lambda t, m: empty_txt
    up32: list = []
    lark_api.im_upload_file = lambda *a, **k: up32.append("upload")
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    s32, f32 = pipeline.deliver_file(meta(minute_token=_tok32), empty_tr,
                                     ["on_CHUAFILE"])
    n32 = db.conn().execute(
        "SELECT COUNT(*) c FROM deliveries WHERE minute_token=?",
        (_tok32,)).fetchone()["c"]
    check("transcript rỗng -> không gọi upload", not up32)
    check("transcript rỗng -> KHÔNG ghi thêm lần thử nào", n32 == 0, str(n32))
    check("transcript rỗng -> không báo là đã gửi", s32 == [])
    pipeline.write_doc, lark_api.im_upload_file = keep32c

    pipeline.deliver, pipeline.deliver_file = keep32
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")

    # =================================================================
    part("31. `.bat` chạy nền: `timeout` KHÔNG thay được `ping`")
    # =================================================================
    # `timeout` tự chết ngay khi stdin không phải console — đúng cảnh .bat chạy
    # từ Task Scheduler/Startup. Đo 02/08/2026: `timeout /t 5` mất 0,098s và in
    # "ERROR: Input redirection is not supported"; `ping -n 6` mất 5,14s.
    # Hậu quả đã xảy ra thật: run-v2-auto bật lại orchestrator 17 lần trong
    # 0,93 giây lúc máy logoff, tức cái trễ 120s chưa bao giờ tồn tại.
    _root = Path(__file__).resolve().parent.parent
    for _b in ("run-v2-auto.bat", "hermes-watchdog.bat"):
        _p = _root / _b
        _live = [ln for ln in _p.read_text(encoding="ascii",
                                           errors="replace").splitlines()
                 if "timeout /t" in ln and not ln.strip().upper().startswith("REM")]
        check(f"{_b}: không còn `timeout /t` nào chạy thật",
              not _live, "; ".join(_live))

    # Tên file log phải được tính LẠI mỗi vòng. Trước 03/08/2026 nó tính một
    # lần lúc khởi động, nên wrapper sống qua nửa đêm là log ngày mới chui vào
    # file ngày cũ — rồi lần sau ai đó mở `v2-<hôm đó>.log` thấy file TRỐNG và
    # kết luận hệ thống không chạy. Đọc sai kiểu đó nguy hiểm hơn là thiếu log.
    _auto = (_root / "run-v2-auto.bat").read_text(encoding="ascii",
                                                  errors="replace").splitlines()
    _i_loop = next(i for i, l in enumerate(_auto) if l.strip() == ":loop")
    _i_py = next(i for i, l in enumerate(_auto)
                 if i > _i_loop and "python -m v2 run" in l and
                 not l.strip().upper().startswith("REM"))
    check("run-v2-auto.bat: tính lại tên file log TRONG vòng lặp",
          any("call :setlog" in l for l in _auto[_i_loop:_i_py]),
          "LOG chỉ tính một lần -> log ngày mới ghi vào file ngày cũ")

    # =================================================================
    part("35. Không đốt hạn mức Vercel Blob khi không có việc")
    # =================================================================
    # "Advanced Requests" của gói free = 2.000 thao tác/THÁNG. Vòng `run` gọi
    # hộp thư OAuth mỗi 5 phút bất kể có ai đang enroll hay không (~100 lần/ngày
    # theo log), cộng 3 thao tác mỗi lần đẩy status. Vercel đã gửi thư báo 75%
    # sau ~3,5 ngày. Cạn hạn mức = hộp thư chết = KHÔNG AI ENROLL ĐƯỢC.
    keep35 = oauth.poll_pending
    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce")
    check("không có nonce sống -> KHÔNG gọi hộp thư", not oauth.has_live_nonce())
    with db.tx() as c:
        c.execute("INSERT INTO oauth_nonce(nonce,open_id,expires_at) "
                  "VALUES ('n1','ou_x',?)", (int(time.time() * 1000) + 60_000,))
    check("có người đang giữa chừng cấp quyền -> CÓ gọi", oauth.has_live_nonce())
    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce")
        c.execute("INSERT INTO oauth_nonce(nonce,open_id,expires_at) "
                  "VALUES ('n2','ou_x',?)", (int(time.time() * 1000) - 1,))
    check("nonce hết hạn -> KHÔNG gọi (complete() cũng sẽ từ chối nó)",
          not oauth.has_live_nonce())
    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce")
    # `_http` đã bị lưới chặn mạng thay, nên nếu gate hỏng thì lời gọi thật sẽ
    # nổ chứ không im lặng đi ra Vercel — nhưng poll_pending dùng httpx trực
    # tiếp, nên kiểm bằng giá trị trả về là đủ và không chạm mạng.
    check("gate chặn TRƯỚC khi mở kết nối", oauth.poll_pending() == [])
    oauth.poll_pending = keep35

    check("nhịp đẩy status mặc định KHÔNG còn là 'mỗi vòng'",
          config.STATUS_PUSH_EVERY >= 900, str(config.STATUS_PUSH_EVERY))

    # BẤT BIẾN chống một hiểu nhầm đắt: `STATUS_PUSH_EVERY` KHÔNG phải nhịp báo
    # lỗi. Nhầm nó ra "giảm nhịp đẩy = ít cảnh báo hơn" sẽ dẫn tới quyết định
    # giữ nhịp cao để giữ an toàn — trong khi thứ thật sự bị hy sinh chỉ là độ
    # tươi của một trang web, còn quota Blob thì cạn và kéo sập cửa enroll.
    # Cảnh báo đi bằng DM Lark (`lark_api.im_send_text`), hai đường song song:
    # `alerts.check_all()` mỗi vòng run, và Scheduled Task `V2_Alerts` từ ngoài.
    _asrc = _inspect.getsource(alerts)
    _leak = [w for w in ("status_push.heartbeat(", "STATUS_PUSH_EVERY",
                         "STATUS_PUSH_URL", "OAUTH_PULL_URL")
             if w in _asrc]
    check("alerts KHÔNG phụ thuộc đường Vercel — Vercel chết vẫn báo được",
          not _leak, f"alerts.py có nhắc tới: {_leak}")
    check("alerts gửi bằng DM Lark, không qua HTTP nào của Vercel",
          "im_send_text" in _asrc)

    # Khởi động KHÔNG được đẩy hai lần. Với `last_push = 0.0` thì điều kiện
    # `now - last_push >= STATUS_PUSH_EVERY` đúng ngay vòng đầu, nên cú đẩy lúc
    # khởi động bị lặp lại sau vài giây — đo trong log: 23:17:50 và 23:17:59.
    # Máy này khởi động lại nhiều lần mỗi ngày vì ngủ, nên nó cộng dồn thật.
    _src35 = _inspect.getsource(orchestrator.run)
    check("khởi động đẩy status xong thì ĐẶT LẠI mốc, không để 0.0",
          "last_push = time.monotonic()" in _src35,
          "vòng đầu sẽ đẩy lần thứ hai ngay sau cú lúc khởi động")

    # =================================================================
    part("36. `get_transcript` — nguyên văn whisper, cùng luật phân quyền")
    # =================================================================
    # Tool này phơi thứ THÔ nhất trong hệ thống: lời nói chưa qua recap. Nếu bộ
    # lọc ở đây lỏng hơn `get_meeting` một chút thì cả §20 thành vô nghĩa — ai
    # cũng đọc được nguyên văn cuộc họp người khác, mà còn chi tiết hơn biên bản.
    # Nên phép kiểm chính không phải "có trả nội dung không" mà là "người KHÔNG
    # dự có bị chặn không", và chặn bằng ĐÚNG `_may_see`, không phải luật thứ hai.
    import json as _tjson
    wipe_jobs()
    _tdir = Path(config.TRANSCRIPT_DIR)
    _tdir.mkdir(parents=True, exist_ok=True)

    def _mk_transcript(tok, segs) -> str:
        p = _tdir / f"{tok}.json"
        p.write_text(_tjson.dumps({
            "minute_token": tok, "lang": "vi", "duration": 120.0,
            "engine": "test/fake", "created_at": 0.0,
            "segments": [{"start": st, "end": st + 5, "text": tx}
                         for st, tx in segs]}, ensure_ascii=False),
            encoding="utf-8")
        return str(p)

    for _tok, _att, _owner in (
            ("mtTA", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A"),
            ("mtTB", [Attendee(open_id="ou_B", union_id="on_B")], "ou_B"),
            ("mtTEMPTY", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A"),
            ("mtTNONE", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A")):
        jobstore.create(meta(minute_token=_tok, title="Hop " + _tok,
                             owner_open_id=_owner, attendees=_att),
                        status="delivered")
    jobstore.set_status("mtTA", "delivered", transcript_path=_mk_transcript(
        "mtTA", [(0.0, "cau mot cua A"), (65.0, "cau hai cua A")]))
    jobstore.set_status("mtTB", "delivered", transcript_path=_mk_transcript(
        "mtTB", [(0.0, "BI MAT cua B")]))
    jobstore.set_status("mtTEMPTY", "delivered", transcript_path=_mk_transcript(
        "mtTEMPTY", [(0.0, "   ")]))
    # mtTNONE: cố ý KHÔNG có transcript_path (job mới, chưa phiên âm xong)

    _outA = qa.get_transcript(wA, "mtTA")
    check("người CÓ dự đọc được nguyên văn",
          "cau mot cua A" in _outA and "cau hai cua A" in _outA)
    check("có mốc thời gian mm:ss để lần lại chỗ nói",
          "[00:00]" in _outA and "[01:05]" in _outA, _outA[:200])
    _outAB = qa.get_transcript(wA, "mtTB")
    check("người KHÔNG dự bị chặn, và KHÔNG lộ một chữ nào của nguyên văn",
          "BI MAT" not in _outAB and "không có trong danh sách người dự" in _outAB,
          _outAB[:200])
    check("who=None -> get_transcript từ chối",
          qa.get_transcript(None, "mtTA") == qa.NO_ASKER)
    check("admin đọc được cuộc mình không dự (đúng như các tool khác)",
          "BI MAT" in qa.get_transcript(wAd, "mtTB"))
    check("transcript RỖNG nói rõ là rỗng, không nói 'không có cuộc họp'",
          "KHÔNG có chữ nào" in qa.get_transcript(wA, "mtTEMPTY"))
    check("job chưa phiên âm xong -> nói tình trạng, KHÔNG bịa nội dung",
          "CHƯA có nguyên văn" in qa.get_transcript(wA, "mtTNONE"))
    check("không khớp cuộc nào -> chỉ đường lấy minute_token",
          "Không tìm thấy" in qa.get_transcript(wA, "khongcogi"))

    # Cắt phần: một transcript dài phải ra nhiều phần, và phần CUỐI không được
    # mời gọi đọc tiếp — agent tin lời đó rồi gọi part=n+1 là một vòng vô ích.
    jobstore.create(meta(minute_token="mtTLONG", title="Hop dai",
                         owner_open_id="ou_A",
                         attendees=[Attendee(open_id="ou_A", union_id="on_A")]),
                    status="delivered")
    jobstore.set_status("mtTLONG", "delivered", transcript_path=_mk_transcript(
        "mtTLONG", [(float(i * 10), f"doan {i} " + "x" * 400) for i in range(40)]))
    _p1 = qa.get_transcript(wA, "mtTLONG", part=1)
    check("transcript dài bị cắt thành nhiều phần, mỗi phần vừa ngân sách",
          "Phần 1/" in _p1 and "1/1" not in _p1
          and len(_p1) < qa.TRANSCRIPT_PART_CHARS * 1.5, f"len={len(_p1)}")
    check("phần chưa cuối MỜI đọc tiếp và nói rõ part kế",
          "part=2" in _p1)
    _n = int(_p1.split("Phần 1/")[1].split()[0].split("\n")[0])
    _plast = qa.get_transcript(wA, "mtTLONG", part=_n)
    check("phần CUỐI không mời đọc tiếp",
          "còn phần" not in _plast and "CHƯA hết" not in _plast)
    check("hai phần liền nhau KHÔNG trùng nội dung",
          qa.get_transcript(wA, "mtTLONG", part=2).count("doan 0 ") == 0)
    check("part ngoài tầm -> nói số phần thật, không trả rỗng",
          f"chỉ có {_n} phần" in qa.get_transcript(wA, "mtTLONG", part=_n + 9))

    # Tool mới phải nằm trong danh sách MCP, và phải đòi vé y như các tool khác.
    # Quên `asker_token` ở `required` là mở toang: `_who()` trả None, mà một
    # nhánh `who=None` nào đó lỏng là rò nguyên văn cho bất kỳ ai nhắn bot.
    _tnames = {t["name"] for t in mcp_server.public_tools()}
    check("get_transcript có trong tools/list", "get_transcript" in _tnames)
    check("mọi tool MCP đều BẮT BUỘC asker_token",
          all("asker_token" in (t["inputSchema"]["required"] or [])
              for t in mcp_server.public_tools()),
          str(sorted(_tnames)))
    check("public_tools không để lọt `_fn` ra ngoài dây",
          all(not any(k.startswith("_") for k in t)
              for t in mcp_server.public_tools()))

    # =================================================================
    part("37. Biên bản .docx — file tự viết, không có thư viện nào đỡ")
    # =================================================================
    # `docxfile.py` dựng OOXML bằng tay để không thêm dependency (xem docstring
    # của nó). Cái giá: không có thư viện nào báo lỗi giúp — một ký tự sai là
    # Word/Lark nói "file bị lỗi" và người dự nhận một file không mở được, còn
    # `deliveries` thì vẫn ghi `ok=1`. Nên phải kiểm tận cấu trúc ZIP + XML.
    import zipfile as _zf
    from xml.etree import ElementTree as _ET
    from v2 import docxfile as _dx

    _dmeta = meta(minute_token="obsgDOCX0000000000001",
                  title='Hop <A&B> "quy 3"', start=1785000000.0,
                  app_link="https://x/minutes/obsgDOCX0000000000001")
    _dt = Transcript(
        minute_token="obsgDOCX0000000000001", lang="vi", duration=125.0,
        engine="test/fake",
        segments=[Segment(start=0.0, end=5.0, text="cau mot & <the>"),
                  Segment(start=65.0, end=70.0, text="cau hai\x07co ky tu dieu khien"),
                  Segment(start=90.0, end=95.0, text="   ")])
    _dpath = _dx.write_docx(_dt, _dmeta, Path(config.TRANSCRIPT_DIR) / "t.docx")
    with _zf.ZipFile(_dpath) as _z:
        _names = set(_z.namelist())
        _zbad = _z.testzip()
        _docxml = _z.read("word/document.xml").decode("utf-8")
    check("có đủ 3 phần bắt buộc của một .docx",
          _names == {"[Content_Types].xml", "_rels/.rels", "word/document.xml"},
          str(sorted(_names)))
    check("ZIP không hỏng", _zbad is None, str(_zbad))
    try:
        _ET.fromstring(_docxml)
        _xmlok = True
    except _ET.ParseError as _pe:
        _xmlok = False
        print("     ", _pe)
    check("word/document.xml là XML hợp lệ", _xmlok)
    check("`&` và `<` trong TIÊU ĐỀ được escape, không phá XML",
          "&amp;" in _docxml and "&lt;A&amp;B&gt;" in _docxml)
    check("ký tự điều khiển bị loại (Word từ chối mở nếu lọt vào)",
          "\x07" not in _docxml and "cau haico ky tu dieu khien" in _docxml)
    check("đoạn rỗng bị bỏ, không đẻ ra dòng trắng", "[01:30]" not in _docxml)
    check("mốc thời gian đúng mm:ss", "[00:00]" in _docxml and "[01:05]" in _docxml)
    check("có câu cảnh báo 'bản do máy' NGAY TRONG FILE",
          "Whisper" in _docxml and "nghe nhầm" in _docxml,
          "file rời khỏi chat rất nhanh — cảnh báo chỉ ở tin nhắn là mất")
    check("có link bản ghi gốc để đối chiếu", "minutes/obsgDOCX" in _docxml)
    check("`w:sz` là NỬA point — 32 = 16pt, không phải 32pt",
          'w:sz w:val="32"' in _docxml)
    check("ghi xong không để lại file .part",
          not list(Path(config.TRANSCRIPT_DIR).glob("*.part")))

    # Đuôi file đổi .txt -> .docx. Hai chỗ bám vào tên file: `_upload_doc` chọn
    # `file_type` (sai thì Lark hiện tệp nhị phân thay vì xem trước được), và
    # `bitable` tìm file cũ của các cuộc họp trước 03/08.
    _lp = pipeline.legacy_txt_path(_dmeta)
    check("doc_path ra .docx", pipeline.doc_path(_dmeta).suffix == ".docx")
    check("legacy_txt_path ra .txt, CÙNG tên, chỉ khác đuôi",
          _lp.suffix == ".txt" and _lp.stem == pipeline.doc_path(_dmeta).stem)
    check(".docx -> file_type 'doc' (để Lark xem trước được trong chat)",
          lark_api.FILE_TYPES.get(".docx") == "doc")

    # Guard rỗng: .txt rỗng là 0 byte nên `st_size` bắt được; .docx của một
    # transcript rỗng vẫn ~1 KB. Mất phép kiểm này là §31.3 quay lại bằng cửa
    # khác — file gửi đi mở ra không có chữ nào, `deliveries` vẫn `ok=1`.
    _empty_doc = _dx.write_docx(
        Transcript(minute_token="x", lang="vi", duration=60.0, engine="e",
                   segments=[Segment(start=0.0, end=1.0, text="  ")]),
        _dmeta, Path(config.TRANSCRIPT_DIR) / "rong.docx")
    check(".docx của transcript RỖNG vẫn > 0 byte (nên st_size KHÔNG cứu được)",
          _empty_doc.stat().st_size > 0, str(_empty_doc.stat().st_size))
    _keep37 = lark_api.im_upload_file
    _up37: list = []
    lark_api.im_upload_file = lambda *a, **k: _up37.append("upload")
    _fk37, _why37 = pipeline._upload_doc(
        _empty_doc, Transcript(minute_token="x", lang="vi", duration=60.0,
                               engine="e", segments=[]))
    check("transcript rỗng -> KHÔNG upload dù file to hơn 0 byte",
          not _up37 and _fk37 is None and "rỗng" in _why37, f"{_up37} {_why37}")
    lark_api.im_upload_file = _keep37

    # Base: cuộc họp CŨ chỉ có .txt trên đĩa. Phải tìm ra nó, đừng tụt xuống
    # đính kèm bản .json — người mở ô file trên Base sẽ nhận một cục JSON.
    _oldmeta = meta(minute_token="obsgOLD00000000000001", title="Hop cu",
                    start=1785000000.0)
    pipeline.legacy_txt_path(_oldmeta).write_text("bien ban cu", encoding="utf-8")
    jobstore.create(_oldmeta, status="delivered")
    _keep37b = (lark_api.base_media_upload, lark_api.contact_batch)
    _got37: list = []
    lark_api.base_media_upload = lambda p, *a, **k: (_got37.append(Path(p)), "ft")[1]
    lark_api.contact_batch = lambda ids: {}
    _bt._tracking_fields(_oldmeta)
    check("cuộc họp cũ: Base vẫn đính đúng bản .txt, không tụt xuống .json",
          len(_got37) == 1 and _got37[0].suffix == ".txt", str(_got37))
    lark_api.base_media_upload, lark_api.contact_batch = _keep37b

    # =================================================================
    part("38. `send_transcript_file` — đường GHI thứ hai, gửi tin THẬT")
    # =================================================================
    # Tool này gửi tin nhắn Lark. Một lỗ ở đây không phải "trả lời sai" mà là
    # "file biên bản bay tới người không được xem", và không lùi lại được.
    # Nên phép kiểm nặng nhất là: sai điều kiện thì KHÔNG có lời gọi API nào.
    from v2 import sendfile as _sf
    wipe_jobs()
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    for _tok, _att, _owner in (
            ("mtSA", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A"),
            ("mtSB", [Attendee(open_id="ou_B", union_id="on_B")], "ou_B"),
            ("mtSEMPTY", [Attendee(open_id="ou_A", union_id="on_A")], "ou_A")):
        jobstore.create(meta(minute_token=_tok, title="Hop " + _tok,
                             owner_open_id=_owner, attendees=_att),
                        status="delivered")
    jobstore.set_status("mtSA", "delivered", transcript_path=_mk_transcript(
        "mtSA", [(0.0, "noi dung cua A")]))
    jobstore.set_status("mtSB", "delivered", transcript_path=_mk_transcript(
        "mtSB", [(0.0, "BI MAT cua B")]))
    jobstore.set_status("mtSEMPTY", "delivered", transcript_path=_mk_transcript(
        "mtSEMPTY", [(0.0, "  ")]))

    _keep38 = (lark_api.im_send_file, lark_api.im_upload_file)
    _sent38: list = []
    lark_api.im_upload_file = lambda *a, **k: "FK"
    lark_api.im_send_file = lambda rid, p, **k: (
        _sent38.append((rid, Path(p).suffix, k.get("id_type"))), "om_x")[1]

    _r = _sf.send_transcript(wA, "mtSA")
    check("người CÓ dự: gửi được, và gửi .docx",
          len(_sent38) == 1 and _sent38[0][1] == ".docx", str(_sent38))
    check("gửi tới CHÍNH người hỏi, bằng union_id của họ",
          _sent38 and _sent38[0][0] == "on_A" and _sent38[0][2] == "union_id")
    check("báo cho agent là ĐÃ gửi, và dặn đừng chép nội dung ra chat",
          "ĐÃ gửi" in _r and "KHÔNG chép" in _r)
    check("ghi deliveries kind='ondemand', KHÔNG đội lốt 'full'",
          [dict(r) for r in db.conn().execute(
              "SELECT kind, ok FROM deliveries WHERE minute_token='mtSA'")]
          == [{"kind": "ondemand", "ok": 1}],
          "dùng lại 'full' là _backfill_deliveries tưởng đã phát rồi")

    _n_before = len(_sent38)
    _rb = _sf.send_transcript(wA, "mtSB")
    check("người KHÔNG dự: KHÔNG gọi API lần nào",
          len(_sent38) == _n_before, str(_sent38))
    check("và câu từ chối không lộ nội dung",
          "BI MAT" not in _rb and "không có trong danh sách người dự" in _rb)
    check("who=None -> từ chối, không gửi",
          _sf.send_transcript(None, "mtSA") == qa.NO_ASKER
          and len(_sent38) == _n_before)
    check("minute_token rỗng -> từ chối",
          _sf.send_transcript(wA, "") == _sf.NO_MEETING)
    check("cuộc họp không có thật -> từ chối, không gửi",
          _sf.send_transcript(wA, "mtKHONGCO") == _sf.NO_MEETING
          and len(_sent38) == _n_before)
    check("transcript rỗng -> KHÔNG gửi file mở ra trắng",
          "KHÔNG có chữ nào" in _sf.send_transcript(wA, "mtSEMPTY")
          and len(_sent38) == _n_before)
    # `admin_view()` là cửa sau cho người ngồi trước máy (`v2 ask` không có
    # --as): nó KHÔNG có union_id/open_id nào. Đừng nhầm với `who("on_ADMIN",…)`
    # — người đó là admin THẬT trong Lark và phải nhận được file bình thường.
    check("admin đường terminal (không có id Lark) -> KHÔNG gửi, nói rõ vì sao",
          "không có định danh Lark" in _sf.send_transcript(
              askers.admin_view(), "mtSA")
          and len(_sent38) == _n_before)

    # Chống lặp: agent hiểu nhầm hoặc gặp lỗi là nó gọi lại. Không có cửa này
    # thì một vòng lặp của agent = hai chục file rơi vào chat người dùng.
    _r2 = _sf.send_transcript(wA, "mtSA")
    check("gọi lại ngay -> KHÔNG gửi lần hai",
          len(_sent38) == 1 and "KHÔNG gọi lại tool này" in _r2, str(_sent38))
    with db.tx() as c:
        c.execute("UPDATE deliveries SET sent_at=sent_at-? WHERE kind='ondemand'",
                  ((_sf.RESEND_COOLDOWN_MIN + 1) * 60_000,))
    _sf.send_transcript(wA, "mtSA")
    check("qua cửa sổ chống lặp thì gửi lại được (người dùng thật đổi ý)",
          len(_sent38) == 2, str(_sent38))

    # Lỗi Lark phải nói THẲNG là chưa gửi. Báo "đã gửi" rồi người dùng ngồi chờ
    # một file không bao giờ tới là kiểu hỏng tệ nhất ở đây.
    def _boom38(*a, **k):
        raise lark_api.LarkError(230013, "chua phat hanh", "im_send")

    lark_api.im_send_file = _boom38
    with db.tx() as c:
        c.execute("DELETE FROM deliveries")
    _re = _sf.send_transcript(wA, "mtSA")
    check("Lark từ chối -> nói CHƯA gửi, không nói đã gửi",
          "chưa gửi" in _re and "ĐÃ gửi" not in _re, _re[:120])
    check("và vẫn để lại dấu vết ok=0",
          db.conn().execute("SELECT ok FROM deliveries WHERE kind='ondemand'"
                            ).fetchone()["ok"] == 0)
    lark_api.im_send_file, lark_api.im_upload_file = _keep38

    check("send_transcript_file có trong tools/list",
          "send_transcript_file" in {t["name"] for t in mcp_server.public_tools()})
    check("`.docx` -> file_type 'doc' ở MỘT chỗ duy nhất (lark_api.FILE_TYPES)",
          lark_api.FILE_TYPES.get(".docx") == "doc"
          and "FILE_TYPES" in _inspect.getsource(lark_api.im_send_file),
          "bảng này từng nằm hai nơi — thêm đuôi ở một bên là lệch hành vi")

    # =================================================================
    part("39. Mô hình KÉO — priority, held, hỏi -> cực cao -> tự gửi")
    # =================================================================
    # Transcript/Segment/Attendee đã import ở đầu run() (from v2.models ...).
    from v2 import sendfile

    # (a) active_by_priority: cực cao(2) -> thường(1) -> backlog(0)
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgPRIO0000000000low"), priority=0)
    jobstore.create(meta(minute_token="obsgPRIO000000000norm"), priority=1)
    jobstore.create(meta(minute_token="obsgPRIO0000000000urg"), priority=2)
    order = [r["minute_token"] for r in jobstore.active_by_priority()]
    check("active_by_priority: 2 -> 1 -> 0",
          order == ["obsgPRIO0000000000urg", "obsgPRIO000000000norm",
                    "obsgPRIO0000000000low"], str(order))

    # (b) set_priority chỉ NÂNG, không hạ
    jobstore.set_priority("obsgPRIO000000000norm", 0)
    check("set_priority KHÔNG hạ mức đang cao hơn",
          jobstore.get("obsgPRIO000000000norm")["priority"] == 1)
    jobstore.set_priority("obsgPRIO000000000norm", 2)
    check("set_priority nâng lên được",
          jobstore.get("obsgPRIO000000000norm")["priority"] == 2)

    # (c) transcript_requests: khử trùng (cuộc,người), list, clear
    db.add_transcript_request("obsgTREQ000000000001", "on_A", "An")
    db.add_transcript_request("obsgTREQ000000000001", "on_A", "An")   # trùng
    db.add_transcript_request("obsgTREQ000000000001", "on_B", "Binh")
    reqs = db.transcript_requesters("obsgTREQ000000000001")
    check("transcript_requests khử trùng, giữ 2 người",
          len(reqs) == 2 and {r["requester"] for r in reqs} == {"on_A", "on_B"})
    db.clear_transcript_requests("obsgTREQ000000000001")
    check("clear_transcript_requests xoá hết",
          db.transcript_requesters("obsgTREQ000000000001") == [])

    # Mock run_transcription THÀNH CÔNG (như thật: set recapping + transcript_path)
    keepP = pipeline.run_transcription

    def _fake_ok(m):
        p = config.TRANSCRIPT_DIR / f"fake-{m.minute_token}.json"
        jobstore.set_status(m.minute_token, "recapping",
                            transcript_path=str(p),
                            audio_seconds=60.0, whisper_seconds=1.0)
        return (Transcript(minute_token=m.minute_token, lang="vi",
                           duration=60.0, engine="fake",
                           segments=[Segment(0.0, 1.0, "xin chao")]), p)
    pipeline.run_transcription = _fake_ok
    # Base là nguồn bot Q&A đọc mặc định — held/cực cao PHẢI ghi record, không
    # thì "gửi transcript [tên]" tra không ra cuộc. Mock để bắt lời gọi.
    import v2.bitable as _bit
    base_calls: list[str] = []
    keepWD = _bit.write_draft
    _bit.write_draft = lambda m, r, n: (base_calls.append(m.minute_token),
                                        "rec")[1]

    # (d) cuộc THƯỜNG (priority 1) dịch xong -> held, KHÔNG broadcast, có Base
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgHELD00000000001"), priority=1)
    orchestrator.process_queue(dry_run=False)
    check("cuộc thường dịch xong -> held (không gửi cho ai)",
          jobstore.get("obsgHELD00000000001")["status"] == "held")
    check("cuộc held -> CÓ ghi record Base (bot Q&A tra được)",
          "obsgHELD00000000001" in base_calls)

    # (e) cuộc CỰC CAO (priority 2) + có người hỏi -> gửi transcript cho họ
    sentbox: list[str] = []
    keepDF = pipeline.deliver_file
    pipeline.deliver_file = (lambda m, t, recips:
                             (sentbox.extend(recips), (list(recips), []))[1])
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgURG000000000001"), priority=2)
    db.add_transcript_request("obsgURG000000000001", "on_ASK", "Nguoi Hoi")
    orchestrator.process_queue(dry_run=False)
    r = jobstore.get("obsgURG000000000001")
    check("cực cao xong -> gửi transcript cho người đã hỏi",
          "on_ASK" in sentbox)
    check("cực cao xong -> status delivered", r["status"] == "delivered")
    check("cực cao xong -> xoá danh sách hỏi",
          db.transcript_requesters("obsgURG000000000001") == [])
    check("cực cao xong -> cũng ghi record Base",
          "obsgURG000000000001" in base_calls)
    pipeline.deliver_file = keepDF
    pipeline.run_transcription = keepP
    _bit.write_draft = keepWD

    # (f) sendfile: hỏi transcript chưa có -> nâng cực cao + ghi người hỏi
    add_user("ou_ASK", "on_ASK2", "Nguoi Hoi 2")
    wipe_jobs()
    mm_ask = meta(minute_token="obsgASK000000000001", owner_open_id="ou_ASK",
                  attendees=[Attendee(open_id="ou_ASK", union_id="on_ASK2")])
    jobstore.create(mm_ask, status="queued", priority=1)
    db.note_viewer("obsgASK000000000001", "ou_ASK", "on_ASK2", "Nguoi Hoi 2")
    msg_ask = sendfile.send_transcript(
        {"union_id": "on_ASK2", "open_id": "ou_ASK", "name": "Nguoi Hoi 2"},
        "obsgASK000000000001")
    check("hỏi transcript chưa có -> nâng priority=2",
          jobstore.get("obsgASK000000000001")["priority"] == 2)
    check("hỏi transcript chưa có -> ghi người hỏi để tự gửi",
          any(r["requester"] == "on_ASK2"
              for r in db.transcript_requesters("obsgASK000000000001")))
    check("hỏi transcript chưa có -> trả lời 'ưu tiên dịch/tự gửi'",
          "ưu tiên" in msg_ask.lower() or "tự gửi" in msg_ask.lower())

    # (g) held mà ghi Base HỎNG lần đầu -> `retry_missing_records` PHẢI vá
    #     (sửa 03/08/2026): trước đó retry chỉ quét 'delivered', bỏ sót held
    #     vĩnh viễn -> cuộc mất trong list/search Base dù transcript nằm sẵn.
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgHELDRETRY000001"),
                    status="held", priority=1)   # KHÔNG có bitable_record_id
    retry_calls: list[str] = []
    keepEN, keepWD3 = _bit.enabled, _bit.write_draft
    _bit.enabled = lambda: True                  # selftest tắt Base (env rỗng)
    _bit.write_draft = lambda m, r, n: (retry_calls.append(m.minute_token),
                                        "rec")[1]
    _bit.retry_missing_records()
    _bit.enabled, _bit.write_draft = keepEN, keepWD3
    check("held chưa lên Base -> retry_missing_records vá (không bỏ sót held)",
          "obsgHELDRETRY000001" in retry_calls)

    # (h) held lọt vào pending (ghi record hỏng) -> nói theo góc người dùng
    #     ('chưa tạo xong biên bản, tạo xong tự gửi'), KHÔNG lộ chữ 'Base' hay
    #     'held' thô, và KHÔNG nói 'CHƯA CÓ BIÊN BẢN' (sai ngược).
    check("_TINH_TRANG có 'held' (không rơi về chữ thô cho người dùng)",
          "held" in qa._TINH_TRANG)
    _held_row = {"when": "2026-08-03 10:00:00", "title": "Hop dang giu",
                 "status": "held", "tinh_trang": qa._TINH_TRANG["held"],
                 "minute_token": "obsgHELDRETRY000001", "link": "", "error": ""}
    _line = qa.fmt_pending(_held_row)
    check("held trong pending -> KHÔNG lộ 'Base'/'held' cho người dùng",
          "Base" not in _line and "held" not in _line, _line)
    check("held trong pending -> 'chưa tạo xong', không 'CHƯA CÓ BIÊN BẢN'",
          "CHƯA TẠO XONG BIÊN BẢN" in _line and "CHƯA CÓ BIÊN BẢN" not in _line,
          _line)

    # (i) delivery_status(held) -> nhãn RIÊNG, KHÔNG nhầm 'chưa ai cấp quyền'
    #     (sửa 03/08/2026): held có 0 dòng recap-delivery nên nhánh đếm cũ gán
    #     ST_NO_CONSENT — sai, người dự vẫn được báo Minute+tóm tắt, chỉ giữ bản
    #     nguyên văn chờ hỏi. Phải xét jobs.status TRƯỚC.
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgHELDSTAT00001"),
                    status="held", priority=1)
    check("delivery_status(held) -> ST_HELD, không ST_NO_CONSENT",
          _bit.delivery_status("obsgHELDSTAT00001") == _bit.ST_HELD)
    check("ST_HELD nằm trong STATUS_OPTIONS (Base mới nhận được giá trị)",
          _bit.ST_HELD in _bit.STATUS_OPTIONS)
    check("ST_HELD KHÔNG lộ 'Base'/'held' thô cho người dùng",
          "Base" not in _bit.ST_HELD and "held" not in _bit.ST_HELD)

    # =================================================================
    part("34. Lưới chặn mạng còn nguyên sau cả lượt chạy")
    # =================================================================
    # Nhiều nhóm ở trên lưu-rồi-trả-lại thuộc tính của `lark_api`. Một cái trả
    # nhầm `_http` là lưới biến mất mà không ai biết, và lần sau quên stub thì
    # lại gọi mạng thật trong im lặng. Kiểm ở CUỐI vì đó là lúc duy nhất câu
    # trả lời có nghĩa.
    check("lark_api._http vẫn là hàm chặn, không bị trả về bản thật",
          lark_api._http is _no_net)
    try:
        lark_api._http()
        _bit = False
    except AssertionError:
        _bit = True
    check("gọi thẳng vào nó thì NỔ, không im lặng đi ra mạng", _bit)

    # =================================================================
    print("\n" + "=" * 66)
    print(f"selftest: PASS {len(ok)}   FAIL {len(bad)}")
    for f in bad:
        print("   FAIL:", f)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
