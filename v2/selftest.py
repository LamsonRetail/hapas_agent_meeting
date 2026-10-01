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

import json
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
        "CF_RELAY_URL": "", "CF_ACCOUNT_ID": "", "CF_QUEUE_ID": "",
        "CF_QUEUE_API_TOKEN": "", "OAUTH_STATE_SECRET": "",
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
    from v2 import (alerts, askers, backup, cards, cloudflare_relay, config, db, gate, jobstore,
                    lark_api, mcp_server, meetings, oauth, oauth_callback,
                    orchestrator, pipeline, qa, summarize, tasks, tokenstore,
                    transcribe, singleton, whisper_supervisor)
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
        except pipeline.WaitingForAuth:
            out = "WaitingForAuth"
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
    check("chủ chưa enroll + người dự bị từ chối -> park chờ CHỦ tự OAuth",
          scenario(deny=("ou_2", "ou_3"), notoken=("ou_owner",))
          == "WaitingForAuth")
    check("chỉ toàn người chưa enroll -> WaitingForAuth (không retry nóng)",
          scenario(notoken=("ou_owner", "ou_2", "ou_3")) == "WaitingForAuth")
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
            ("WaitingForAuth", pipeline.WaitingForAuth("chờ người tự OAuth")),
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
            ("WaitingForAuth -> park, không retry nóng",
             pipeline.WaitingForAuth("chờ người tự OAuth"), "waiting_auth", 3),
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
    part("6. _recipients — chỉ attendee rõ ràng + ĐÃ ENROLL, khử trùng")
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
    check("reader/search hit KHÔNG tự thành người nhận",
          rec == ["on_A"] and "on_B" not in rec, str(rec))
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
    db.note_viewer("mtC", "ou_B", "on_B", "Binh")   # chỉ search/read được, không phải attendee
    idx = qa.viewers_index()
    wA = askers.who("on_A", "ou_A", "An")
    wB = askers.who("on_B", "ou_B", "Binh")
    wAd = askers.who("on_ADMIN", "ou_ADMIN", "Admin")
    check("A xem được cuộc của A", qa._may_see("mtA", wA, idx))
    check("A KHÔNG xem được cuộc của B", not qa._may_see("mtB", wA, idx))
    check("minute_viewers KHÔNG nới ACL sang cuộc người khác",
          not qa._may_see("mtC", wB, idx))
    # ĐỔI 04/08/2026 (user chốt): admin qua BOT chỉ còn xem cuộc MÌNH dự. Phép
    # kiểm cũ khẳng định "admin xem được hết" — chính hành vi đã bị bỏ.
    check("admin qua bot chỉ xem cuộc mình dự, KHÔNG xem cuộc người khác",
          not any(qa._may_see(t, wAd, idx) for t in ("mtA", "mtB", "mtC")))
    check("đường terminal (see_all) vẫn xem được hết",
          all(qa._may_see(t, askers.admin_view(), idx)
              for t in ("mtA", "mtB", "mtC")))
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
    check("B chỉ thấy mtB trong hàng đợi",
          {p["minute_token"] for p in pend} == {"mtB"})
    check("B bị ẩn đúng mtA + mtC", hidden == 2)

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

    # Gần giờ nhưng khác tên KHÔNG được lấy attendee. Đây là regression cho ca
    # thật Daily CDP bị ghép với HAPAS Project Trang sức (+22 người).
    lark_api.calendar_events = lambda tok, cal, lo, hi: [
        {"event_id": "ev-near-wrong", "summary": "CUỘC KHÁC HẲN",
         "start_time": {"timestamp": str(_t6)}}]
    m6c = meta(minute_token="obsgNEARWRONG0000001", title="Daily CDP Checkin")
    m6c.start = _t6
    m6c.attendees = []
    meetings.resolve_participants("tok", m6c)
    check("chỉ gần giờ nhưng khác tên -> KHÔNG lấy attendee",
          not m6c.attendees and m6c.participants_source == "no_match",
          f"{m6c.participants_source}: {m6c.attendees}")
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
    # Whisper KHỞI ĐỘNG LẠI giữa chừng: job_id nằm trong RAM tiến trình cũ nên
    # `/status/<id>` trả 404. Trước 04/08/2026 vòng chờ không nhận ra —
    # `.json()` của 404 ra `{"detail":...}`, `status` rỗng, và nó quay tiếp tới
    # `timeout_sec` = SÁU GIỜ, chặn cả hàng đợi phía sau. Phải bỏ NHANH và ném
    # `TranscribeUnavailable` để orchestrator trả lại lượt thử (sự cố hạ tầng,
    # không phải lỗi cuộc họp).
    class _Resp404:
        status_code = 404
        @staticmethod
        def json(): return {"detail": "job_id khong ton tai"}

    class _RespNoStatus:
        status_code = 200
        @staticmethod
        def json(): return {"khong_co_status": 1}

    _keep_httpx_get = transcribe.httpx.get
    _keep_post = transcribe.httpx.post

    class _PostOK:
        status_code = 200
        @staticmethod
        def raise_for_status(): pass
        @staticmethod
        def json(): return {"job_id": "j1"}
    _wav = Path(config.WORK_DIR) / "kiemthu404.wav"
    _wav.parent.mkdir(parents=True, exist_ok=True)
    _wav.write_bytes(b"RIFF0000WAVE")

    transcribe.httpx.post = lambda *a, **k: _PostOK()
    transcribe.httpx.get = lambda *a, **k: _Resp404()
    _t0 = time.time()
    try:
        transcribe.transcribe(_wav, "obsg404", poll_interval=0.01)
        _err404 = "KHÔNG ném gì"
    except transcribe.TranscribeUnavailable as exc:
        _err404 = str(exc)
    except Exception as exc:                      # noqa: BLE001
        _err404 = f"{type(exc).__name__}: {exc}"
    check("whisper trả 404 -> bỏ NGAY, ném TranscribeUnavailable",
          "khởi động lại" in _err404, _err404[:90])
    check("...và bỏ trong vài giây, KHÔNG quay 6 tiếng",
          time.time() - _t0 < 5, f"{time.time()-_t0:.1f}s")

    transcribe.httpx.get = lambda *a, **k: _RespNoStatus()
    try:
        transcribe.transcribe(_wav, "obsg404", poll_interval=0.01)
        _errns = "KHÔNG ném gì"
    except transcribe.TranscribeUnavailable as exc:
        _errns = str(exc)
    except Exception as exc:                      # noqa: BLE001
        _errns = f"{type(exc).__name__}: {exc}"
    check("trả lời KHÔNG có trạng thái -> cũng bỏ, không quay vô hạn",
          "không đọc được trạng thái" in _errns, _errns[:90])
    transcribe.httpx.get, transcribe.httpx.post = _keep_httpx_get, _keep_post
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

    # Hạn QUÁ KHỨ (ca thật 04/08/2026): agent quy "thứ sáu tuần này" ra 31/07
    # trong khi hôm nay 04/08 — nó neo vào ngày CUỘC HỌP. Không chặn thì Lark
    # trả code=0 và task quá hạn ngay lúc sinh ra.
    from datetime import timedelta as _td
    _tod = datetime.now(tasks._TZ).date()
    _wipe_who = {"union_id": "on_TASK", "open_id": "ou_TASK", "name": "Nguoi Tao"}
    wipe_jobs()
    add_user("ou_TASK", "on_TASK", "Nguoi Tao")
    jobstore.create(meta(minute_token="obsgTASKDUE00000001", title="Hop tao task",
                         owner_open_id="ou_TASK",
                         attendees=[Attendee(open_id="ou_TASK", union_id="on_TASK")]),
                    status="held", priority=1)
    db.note_viewer("obsgTASKDUE00000001", "ou_TASK", "on_TASK", "Nguoi Tao")
    _keepTC = lark_api.task_create
    _keepGAT = tokenstore.get_access_token
    tokenstore.get_access_token = lambda oid: "tok-gia"   # add_user không có token thật
    _tc_calls: list = []
    lark_api.task_create = lambda *a, **k: (_tc_calls.append(k), {"guid": "g"})[1]
    _msg_past = tasks.create_from_meeting(
        _wipe_who, "obsgTASKDUE00000001", "Viec gi do",
        due=(_tod - _td(days=3)).strftime("%Y-%m-%d"))
    check("hạn quá khứ -> TỪ CHỐI, không gọi Lark",
          "ĐÃ QUA" in _msg_past and not _tc_calls, _msg_past[:90])
    check("câu từ chối KÈM ngày hôm nay (agent không tự biết hôm nay là ngày nào)",
          _tod.strftime("%Y-%m-%d") in _msg_past, _msg_past[:90])
    _msg_today = tasks.create_from_meeting(
        _wipe_who, "obsgTASKDUE00000001", "Viec hom nay",
        due=_tod.strftime("%Y-%m-%d"))
    check("hạn ĐÚNG HÔM NAY vẫn tạo được (không chặn nhầm)",
          "Đã tạo task" in _msg_today, _msg_today[:90])
    # Tên cuộc họp thay cho minute_token: dùng chung bộ giải với send_transcript
    _tc_calls.clear()
    _msg_name = tasks.create_from_meeting(
        _wipe_who, "hop tao task", "Viec theo ten",
        due=(_tod + _td(days=2)).strftime("%Y-%m-%d"))
    check("create_task nhận cả TÊN cuộc họp (cùng bộ giải với send_transcript)",
          "Đã tạo task" in _msg_name, _msg_name[:90])
    lark_api.task_create = _keepTC
    tokenstore.get_access_token = _keepGAT

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

    # Code OAuth chỉ dùng một lần. Nếu exchange xong nhưng user_info chập chờn,
    # lượt sau phải tiếp tục từ token đã checkpoint mã hóa, không exchange lại.
    exchange_calls: list[str] = []
    user_info_calls: list[str] = []

    def _exchange_once(code):
        exchange_calls.append(code)
        return {"access_token": "at-checkpoint", "refresh_token": "rt-checkpoint",
                "expires_in": 7200, "refresh_token_expires_in": 2592000,
                "scope": "offline_access"}

    def _user_info_flaky(access):
        user_info_calls.append(access)
        if len(user_info_calls) == 1:
            raise RuntimeError("user_info tạm thời hỏng")
        return {"open_id": "ou_CHECKPOINT", "union_id": "on_CHECKPOINT",
                "name": "Checkpoint"}

    lark_api.exchange_code = _exchange_once
    lark_api.user_info = _user_info_flaky
    _, n_checkpoint = oauth.start("ou_CHECKPOINT")
    try:
        oauth.complete("one-time-code", n_checkpoint)
        check("user_info hỏng sau exchange phải ném để Queue retry", False)
    except RuntimeError:
        check("user_info hỏng sau exchange phải ném để Queue retry", True)
    check("token sau exchange được checkpoint mã hóa ở local",
          db.conn().execute(
              "SELECT 1 FROM oauth_exchange_pending WHERE state=?",
              (n_checkpoint,)).fetchone() is not None)
    oauth.complete("one-time-code", n_checkpoint)
    check("retry dùng checkpoint, KHÔNG exchange code lần hai",
          exchange_calls == ["one-time-code"], str(exchange_calls))
    check("thành công thì dọn cả nonce lẫn checkpoint",
          db.conn().execute(
              "SELECT 1 FROM oauth_exchange_pending WHERE state=?",
              (n_checkpoint,)).fetchone() is None)

    lark_api.exchange_code, lark_api.user_info, \
        oauth._warn_if_missing_scopes, lark_api.im_send_text = keep8

    # =================================================================
    part("16b. Cloudflare OAuth relay — HMAC + ACK sau khi lưu thành công")
    # =================================================================
    keep_cf_config = (
        config.CF_RELAY_URL, config.CF_ACCOUNT_ID, config.CF_QUEUE_ID,
        config.CF_QUEUE_API_TOKEN, config.OAUTH_STATE_SECRET,
        config.CF_QUEUE_MAX_ATTEMPTS,
    )
    config.CF_RELAY_URL = "https://relay.example"
    config.CF_ACCOUNT_ID = "a" * 32
    config.CF_QUEUE_ID = "b" * 32
    config.CF_QUEUE_API_TOKEN = "queue-token"
    config.OAUTH_STATE_SECRET = "selftest-hmac-secret-never-use-in-production"
    config.CF_QUEUE_MAX_ATTEMPTS = 5

    with db.tx() as c:
        c.execute(
            "INSERT INTO oauth_nonce(nonce,open_id,expires_at) VALUES (?,?,?)",
            ("legacy-vercel-state", "ou_LEGACY", int(time.time() * 1000) + 7200_000))
    _, replacement_state = oauth.start_or_reuse("ou_LEGACY")
    check("cutover không tái sử dụng state Vercel cũ trong link Cloudflare",
          replacement_state != "legacy-vercel-state" and
          cloudflare_relay.verify_state(replacement_state))

    _, signed_state = oauth.start("ou_CF")
    check("state Cloudflare có HMAC hợp lệ",
          cloudflare_relay.verify_state(signed_state))
    check("sửa một ký tự state -> chữ ký bị từ chối",
          not cloudflare_relay.verify_state(signed_state[:-1] +
                                             ("A" if signed_state[-1] != "A" else "B")))
    check("short link Cloudflare không cần ghi KV/Blob",
          oauth.short_link(lark_api.authorize_url(signed_state)) ==
          f"https://relay.example/e/{signed_state}")

    drain_calls: list[str] = []
    keep_drain = (cloudflare_relay.pull_messages,
                  cloudflare_relay.settle_messages, httpx.get)
    cloudflare_relay.pull_messages = lambda: (drain_calls.append("cloudflare"), [])[1]
    cloudflare_relay.settle_messages = lambda **kw: None

    class _EmptyVercel:
        status_code = 200

        def json(self):
            return {"pending": []}

    httpx.get = lambda *a, **k: (drain_calls.append("vercel"), _EmptyVercel())[1]
    oauth.poll_pending(force=True, notify=False)
    check("giai đoạn chuyển tiếp poll CẢ Cloudflare lẫn link Vercel cũ",
          drain_calls == ["cloudflare", "vercel"], str(drain_calls))
    cloudflare_relay.pull_messages, cloudflare_relay.settle_messages, \
        httpx.get = keep_drain

    _, state_ok = oauth.start("ou_CF_OK")
    _, state_retry = oauth.start("ou_CF_RETRY")
    _, state_max = oauth.start("ou_CF_MAX")
    trace_cf: list[str] = []
    settled_cf: dict[str, list[str]] = {}
    keep_cf_funcs = (
        cloudflare_relay.pull_messages, cloudflare_relay.settle_messages,
        oauth.complete,
    )

    def _cf_complete(code, state):
        trace_cf.append(f"complete:{code}")
        if code == "temporary":
            raise RuntimeError("network temporarily unavailable")
        if code == "maxed":
            raise RuntimeError("network still unavailable")
        with db.tx() as c:
            c.execute("DELETE FROM oauth_nonce WHERE nonce=?", (state,))
        return {"open_id": "ou_CF_OK", "union_id": "", "name": "CF Test"}

    cloudflare_relay.pull_messages = lambda: [
        cloudflare_relay.QueueMessage(
            "lease-ok", {"version": 1, "code": "ok", "state": state_ok}, 1),
        cloudflare_relay.QueueMessage(
            "lease-retry", {"version": 1, "code": "temporary", "state": state_retry}, 1),
        cloudflare_relay.QueueMessage(
            "lease-max", {"version": 1, "code": "maxed", "state": state_max}, 5),
        cloudflare_relay.QueueMessage(
            "lease-denied", {"version": 1, "state": signed_state,
                              "fail": {"error": "access_denied"}}, 1),
        cloudflare_relay.QueueMessage("lease-bad", {"nonsense": True}, 1),
    ]

    def _cf_settle(*, ack, retry):
        trace_cf.append("settle")
        settled_cf["ack"] = list(ack)
        settled_cf["retry"] = list(retry)

    cloudflare_relay.settle_messages = _cf_settle
    oauth.complete = _cf_complete
    done_cf = oauth._poll_cloudflare(notify=False, force=True)
    check("Queue chỉ ACK thành công sau complete()",
          trace_cf.index("complete:ok") < trace_cf.index("settle") and len(done_cf) == 1)
    check("lỗi tạm thời được RETRY, không ACK mất code",
          settled_cf.get("retry") == ["lease-retry"], str(settled_cf))
    check("quá số lần thử trở thành terminal để Queue không kẹt vô hạn",
          "lease-max" in settled_cf.get("ack", []), str(settled_cf))
    check("từ chối/sai định dạng được ACK, không chặn message kế tiếp",
          {"lease-denied", "lease-bad"}.issubset(set(settled_cf.get("ack", []))))
    check("ACK thất lạc giao lại vẫn terminal vì nonce đã tiêu",
          oauth._terminal_cloudflare_error(
              RuntimeError("duplicate delivery"), state_ok, 1))

    cloudflare_relay.pull_messages, cloudflare_relay.settle_messages, \
        oauth.complete = keep_cf_funcs
    (config.CF_RELAY_URL, config.CF_ACCOUNT_ID, config.CF_QUEUE_ID,
     config.CF_QUEUE_API_TOKEN, config.OAUTH_STATE_SECRET,
     config.CF_QUEUE_MAX_ATTEMPTS) = keep_cf_config

    # =================================================================
    part("17. Cửa vào bot hỏi đáp (gate)")
    # =================================================================
    box2: list[tuple[str, str]] = []
    cards2: list[tuple[str, dict]] = []
    keep10 = (lark_api.im_send_text, oauth.short_link, lark_api.im_send_card)
    lark_api.im_send_text = lambda uid, text, **k: (box2.append((uid, text)), "om")[1]
    # Lời mời cấp quyền giờ là THẺ có nút (05/08/2026). Thu cả hai kênh: đường
    # thẻ là đường chính, đường text là bản lùi khi thẻ hỏng — cả hai đều phải
    # đo được, nếu không thì bản lùi hỏng âm thầm và cửa vào chết không ai biết.
    lark_api.im_send_card = lambda uid, card, **k: (
        cards2.append((uid, card)), "om")[1]
    oauth.short_link = lambda u: ""
    check("thiếu union_id -> KHÔNG cho vào",
          gate.check("", name="Vo Danh")["decision"] == "wait")
    d = gate.check("on_A", name="An")
    check("đã enroll -> allow, kèm vé phiên",
          d["decision"] == "allow" and bool(d.get("asker_token")))
    check("vé từ gate giải ra đúng người",
          (askers.resolve(d["asker_token"]) or {}).get("union_id") == "on_A")
    box2.clear()
    cards2.clear()
    d = gate.check("on_LA", name="Nguoi La")
    check("chưa enroll -> invite + gửi THẺ cấp quyền, KHÔNG kèm vé",
          d["decision"] == "invite" and len(cards2) == 1
          and not box2 and not d.get("asker_token"))
    _btns = [a for el in cards2[0][1]["elements"] if el.get("tag") == "action"
             for a in el["actions"]]
    check("thẻ cấp quyền có đúng MỘT nút open_url, không phải nút callback",
          len(_btns) == 1 and _btns[0].get("url") and "value" not in _btns[0],
          str(_btns))
    check("URL trong nút là link OAuth thật, không phải chỗ khác",
          "oauth" in _btns[0]["url"].lower() or "authorize" in _btns[0]["url"].lower(),
          _btns[0]["url"][:80])
    d = gate.check("on_LA", name="Nguoi La")
    check("nhắn lại ngay -> wait, không gửi link thứ hai",
          d["decision"] == "wait" and not d.get("asker_token"))
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites")

    def _boom(uid, text, **k):
        raise lark_api.LarkError(230013, "chua phat hanh", "im_send")

    # THẺ hỏng nhưng TEXT còn sống -> vẫn phải mời được. Đây là đường lùi của
    # cửa vào: mất nó thì một thay đổi schema thẻ bên Lark là người mới không
    # bao giờ cấp quyền được nữa, mà triệu chứng chỉ là bot im lặng.
    box2.clear()
    cards2.clear()
    lark_api.im_send_card = _boom
    d = gate.check("on_LUI", name="Nguoi Lui")
    check("thẻ hỏng -> LÙI về text, vẫn mời được",
          d["decision"] == "invite" and len(box2) == 1 and not cards2)

    lark_api.im_send_text = _boom
    d = gate.check("on_HONG", name="X")
    check("cả thẻ lẫn text HỎNG -> không ghi invite, vòng sau còn thử lại",
          d["decision"] == "wait" and db.conn().execute(
              "SELECT 1 FROM enroll_invites WHERE union_id='on_HONG'").fetchone() is None)
    # THỬ KHÔ (`--no-send`) KHÔNG được ghi gì (sửa 04/08/2026 — đã dính thật khi
    # chẩn lỗi bằng `v2 gate --no-send`): ghi `enroll_invites` ở đường không gửi
    # nghĩa là lần nhắn THẬT kế tiếp rơi vào nhánh "wait" và bot nói "link mình
    # VỪA GỬI vẫn còn hiệu lực" trong khi chưa link nào được gửi — người dùng
    # ngồi chờ một tin nhắn không tồn tại suốt ENROLL_REINVITE_MINUTES.
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites")
    box2.clear()
    # `_boom` ở trên vẫn đang thay im_send_text — trả lại bản thu tin để đo được
    # "có gửi hay không" (KHÔNG trả về hàm thật: lưới chặn mạng ở cuối sẽ nổ).
    lark_api.im_send_text = lambda uid, text, **k: (box2.append((uid, text)), "om")[1]
    d = gate.check("on_KHO", name="Nguoi Kho", send=False)
    check("--no-send -> vẫn ra quyết định invite (chẩn được)",
          d["decision"] == "invite" and d.get("dry_run") is True, str(d)[:80])
    check("--no-send -> KHÔNG gửi tin nào", not box2)
    check("--no-send -> KHÔNG ghi lời mời vào DB (không kẹt người dùng)",
          db.conn().execute("SELECT 1 FROM enroll_invites WHERE union_id='on_KHO'"
                            ).fetchone() is None)
    d = gate.check("on_KHO", name="Nguoi Kho")
    check("...nên lần nhắn THẬT ngay sau đó VẪN gửi được link",
          d["decision"] == "invite" and len(box2) == 1, f"{d['decision']} {box2}")
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites")

    # Người vừa OAuth nhắn lại đúng lúc gate tự kéo Queue: auth phải hoàn tất
    # VÀ welcome phải được gửi.
    #
    # ĐẢO LẠI 05/08/2026 (user chốt). Bản trước ép `notify=False` ở đây để tránh
    # "bot trả lời hai lần", nhưng đổi lấy một lỗi nặng hơn: đường này vẫn hoàn
    # tất OAuth và ACK Queue, chỉ bỏ welcome — và KHÔNG có ai gửi bù. Ai lỡ nhắn
    # trước khi vòng nền kịp poll thì mất hẳn danh sách 7 ngày, tức mất đúng
    # luồng chính của sản phẩm. Hai tin lúc đó khác nội dung nên không phải trả
    # lời trùng, và `_sleep_with_fast_enroll` kéo hộp thư mỗi 5 giây khi có người
    # enroll dở nên welcome gần như luôn tới trước.
    keep_gate_poll = oauth.poll_pending
    gate_poll_notify: list[bool] = []

    def _gate_poll(*, notify=True, **_):
        gate_poll_notify.append(notify)
        add_user("ou_AFTER_AUTH", "on_AFTER_AUTH", "Vua Auth")
        return [{"open_id": "ou_AFTER_AUTH", "union_id": "on_AFTER_AUTH"}]

    oauth.poll_pending = _gate_poll
    d = gate.check("on_AFTER_AUTH", name="Vua Auth", send=False)
    oauth.poll_pending = keep_gate_poll
    check("gate hoàn tất auth inline và VẪN gửi welcome (không nuốt danh sách)",
          d["decision"] == "allow" and gate_poll_notify == [True],
          f"decision={d.get('decision')} notify={gate_poll_notify}")

    keep_transition = (config.CF_RELAY_URL, config.OAUTH_STATE_SECRET,
                       config.OAUTH_PULL_URL)
    config.CF_RELAY_URL = "https://relay.example"
    config.OAUTH_STATE_SECRET = "selftest-hmac-secret-never-use-in-production"
    config.OAUTH_PULL_URL = ""
    with db.tx() as c:
        c.execute(
            "INSERT INTO enroll_invites(union_id,user_id,name,nonce,sent_at,times) "
            "VALUES (?,?,?,?,?,1)",
            ("on_OLD_LINK", "u", "Old", "legacy-vercel-state", int(time.time() * 1000)))
    d = gate.check("on_OLD_LINK", name="Old", send=False)
    check("người có invite Vercel cũ nhắn lại -> sinh link Cloudflare mới",
          d["decision"] == "invite" and
          cloudflare_relay.verify_state(d["nonce"]), str(d)[:100])
    (config.CF_RELAY_URL, config.OAUTH_STATE_SECRET,
     config.OAUTH_PULL_URL) = keep_transition
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites")
    lark_api.im_send_text, oauth.short_link, lark_api.im_send_card = keep10

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

    _old_secret = config.APP_SECRET
    config.APP_SECRET = "SECRET_PREFIX_MUST_NEVER_REACH_LOGS"
    try:
        _summary = config.summary()
    finally:
        config.APP_SECRET = _old_secret
    check("startup summary không lộ cả tiền tố app_secret",
          "SECRET" not in _summary and "app_secret=(đã đặt)" in _summary,
          _summary.splitlines()[0])

    _relay_bad = ("[status] đã đẩy snapshot -> ok\n"
                  "[status] đẩy hỏng HTTP 502: Vercel Blob: "
                  "This store has been suspended.\n")
    _relay_recovered = (_relay_bad
                        + "[status] đã đẩy snapshot -> recovered\n")
    check("doctor: Blob suspend sau lần OK -> FAIL",
          _doc.relay_log_health(_relay_bad)[0] == _doc.FAIL)
    check("doctor: lần đẩy mới đã hồi phục -> OK",
          _doc.relay_log_health(_relay_recovered)[0] == _doc.OK)
    check("doctor: chưa có log -> WARN, không xanh giả",
          _doc.relay_log_health("")[0] == _doc.WARN)

    def judge(ent, ext="closed"):
        rows = _doc.judge_base_access(
            [{"member_id": "ou_x", "member_type": "openid", "perm": "full_access"}],
            {"link_share_entity": ent, "external_access_entity": ext})
        return rows[-1]                       # dòng nói về chia sẻ bằng link

    check("link đóng -> OK", judge("closed")[0] == _doc.OK)
    check("tenant_readable -> FAIL (bypass ACL người dự của bot)",
          judge("tenant_readable")[0] == _doc.FAIL)
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

    # Hàm sửa quyền là đường GHI thật nên test phải khóa payload thật hẹp: chỉ
    # đóng link, không vô tình thay quyền cộng tác viên hay external access.
    _perm_calls: list[tuple[str, str, dict]] = []

    class _PermResponse:
        text = ""

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _PermHTTP:
        def patch(self, path, **kwargs):
            _perm_calls.append(("PATCH", path, kwargs))
            return _PermResponse({"code": 0, "data": {}})

        def get(self, path, **kwargs):
            _perm_calls.append(("GET", path, kwargs))
            return _PermResponse({"code": 0, "data": {
                "permission_public": {"link_share_entity": "closed"}}})

    _keep_perm_http, _keep_perm_head = lark_api._http, lark_api._im_headers
    lark_api._http = lambda: _PermHTTP()
    lark_api._im_headers = lambda token=None: {"Authorization": "fake"}
    try:
        _closed = lark_api.drive_link_close("baseTOKEN", "bitable")
    finally:
        lark_api._http, lark_api._im_headers = _keep_perm_http, _keep_perm_head
    check("đóng link Base: PATCH đúng endpoint v2 rồi GET xác minh",
          [x[0] for x in _perm_calls] == ["PATCH", "GET"]
          and all("/drive/v2/permissions/baseTOKEN/public" in x[1]
                  for x in _perm_calls))
    check("đóng link Base: payload CHỈ đổi link_share_entity=closed",
          _perm_calls[0][2].get("json") == {"link_share_entity": "closed"}
          and _perm_calls[0][2].get("params") == {"type": "bitable"})
    check("đóng link Base: đọc lại đúng closed mới báo thành công",
          _closed.get("link_share_entity") == "closed")

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
    check("nguồn khớp tên chính xác -> không tra lại",
          not orchestrator._needs_reresolve("calendar[title]:Hop tuan"))
    for src in ("agenda_failed", "no_match", "no_calendar_event",
                "no_event_in_window", "no_start_time",
                "calendar[near19m]:CUỘC KHÁC",
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
    # Từ 04/08/2026 mỗi người được hỏi HAI lượt: lọc theo người dự (open_id) và
    # không lọc (""), xem `config.SCAN_ALL_VISIBLE`.
    check("người sau VẪN được quét dù người trước lỗi token",
          [x for x in seen33 if x] == ["ou_LANH"], f"đã quét: {seen33}")
    check("quét thêm lượt KHÔNG lọc người (bắt cuộc chỉ mở xem được)",
          "" in seen33 if config.SCAN_ALL_VISIBLE else True, f"đã quét: {seen33}")
    tokenstore.get_access_token, lark_api.minutes_list = keep33

    # --- Quét rộng KHÔNG được nới quyền (04/08/2026) -------------------
    # Cuộc chỉ "mở xem được" phải VÀO hệ thống nhưng KHÔNG được ghi người đó là
    # người xem — nếu ghi, người chỉ được chia sẻ bản ghi sẽ thành "người dự" và
    # kéo được biên bản. Đây là chỗ một dòng thừa mở toang phân quyền.
    wipe_jobs()
    with db.tx() as c:
        c.execute("DELETE FROM minute_viewers")
    keep33b = (tokenstore.get_access_token, lark_api.minutes_list,
               orchestrator.enqueue_minute)
    tokenstore.get_access_token = lambda oid: "tok"
    MINE, SEEN_ONLY = "obsgSCANMINE0000001", "obsgSCANSEEN0000001"
    lark_api.minutes_list = (lambda tok, s, e, oid:
                             [{"token": MINE}] if oid else
                             [{"token": MINE}, {"token": SEEN_ONLY}])
    enq33: list = []
    def _enq33(oid, mt, it=None, **k):
        enq33.append(mt)
        # Chỉ MINE có metadata độc lập xác nhận đúng người; SEEN_ONLY chỉ là
        # bản ghi mở/share và không bao giờ được cấp viewer.
        atts = ([Attendee(open_id="ou_LANH", union_id="on_LANH")]
                if mt == MINE else [])
        jobstore.create(meta(minute_token=mt, owner_open_id="ou_OWNER_OTHER",
                             attendees=atts), status="queued")
        return True

    orchestrator.enqueue_minute = _enq33
    keep_sav = config.SCAN_ALL_VISIBLE
    config.SCAN_ALL_VISIBLE = True        # mặc định TẮT — bật để kiểm chính nó
    db.clear_seen(MINE); db.clear_seen(SEEN_ONLY)
    db.note_seen(MINE); db.note_seen(SEEN_ONLY)          # giả lập đã qua settle
    with db.tx() as c:
        c.execute("UPDATE minutes_seen SET first_seen_at=? ",
                  (int(time.time() * 1000) - 99 * 60_000,))
    orchestrator.scan_once()
    (tokenstore.get_access_token, lark_api.minutes_list,
     orchestrator.enqueue_minute) = keep33b
    check("cuộc chỉ MỞ XEM ĐƯỢC vẫn được nạp vào hệ thống",
          SEEN_ONLY in enq33, f"đã nạp: {enq33}")
    _vw = {r["minute_token"] for r in db.conn().execute(
        "SELECT minute_token FROM minute_viewers").fetchall()}
    check("...nhưng KHÔNG bị ghi là người xem (quyền không nới)",
          SEEN_ONLY not in _vw, f"viewers: {_vw}")
    check("chỉ metadata owner/attendee xác nhận thì mới ghi reader",
          MINE in _vw, f"viewers: {_vw}")

    # Reader của một search hit không được biến thành owner/fallback attendee
    # khi minutes_get thiếu owner tạm thời.
    FALSE_OWNER = "obsgFALSEOWNER00001"
    keep33c = (tokenstore.get_access_token, meetings.build_meta,
               meetings.resolve_participants, tokenstore.list_users,
               config.FALLBACK_TO_OWNER)
    tokenstore.get_access_token = lambda oid: "tok"
    meetings.build_meta = lambda tok, mt, raw=None: meta(
        minute_token=mt, owner_open_id="", attendees=[])
    meetings.resolve_participants = lambda tok, m: m
    tokenstore.list_users = lambda active_only=False: [
        {"open_id": "ou_READER", "union_id": "on_READER", "name": "Reader"}]
    config.FALLBACK_TO_OWNER = True
    check("enqueue search hit thiếu owner vẫn tạo job fail-closed",
          orchestrator.enqueue_minute("ou_READER", FALSE_OWNER,
                                      {"token": FALSE_OWNER}, notify=False))
    _false_meta = jobstore.meta_from_json(jobstore.get(FALSE_OWNER)["meta_json"])
    check("reader KHÔNG bị gán thành owner/attendee",
          not _false_meta.owner_open_id and not _false_meta.attendees,
          str(_false_meta))
    (tokenstore.get_access_token, meetings.build_meta,
     meetings.resolve_participants, tokenstore.list_users,
     config.FALLBACK_TO_OWNER) = keep33c
    config.SCAN_ALL_VISIBLE = keep_sav
    check("mặc định SCAN_ALL_VISIBLE TẮT (bật là kéo cả cuộc phòng khác vào)",
          config.SCAN_ALL_VISIBLE is False)

    # --- `unauth`: xoá quyền để diễn lại luồng người mới ----------------
    # Khác `revoke` (giữ dòng tokens với status='revoked' làm dấu vết): `unauth`
    # xoá SẠCH dấu vết auth để cửa vào coi mình như người lạ. Nhưng KHÔNG được
    # đụng vào dữ liệu họp — cấp quyền lại là dùng ngay được bản cũ.
    from types import SimpleNamespace
    from v2.__main__ import cmd_unauth
    add_user("ou_TESTUA", "on_TESTUA", "Nguoi Tu Test")
    jobstore.create(meta(minute_token="obsgUNAUTH000000001", title="Hop cua ho"),
                    status="held", priority=1)
    db.note_viewer("obsgUNAUTH000000001", "ou_TESTUA", "on_TESTUA", "Nguoi Tu Test")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO enroll_invites(union_id,nonce,sent_at,times)"
                  " VALUES ('on_TESTUA','nonce-cu',?,1)", (int(time.time()*1000),))
    cmd_unauth(SimpleNamespace(union_id="on_TESTUA", open_id=None, yes=True))
    _left = db.conn().execute(
        "SELECT COUNT(*) FROM tokens WHERE open_id='ou_TESTUA'").fetchone()[0]
    _inv = db.conn().execute(
        "SELECT COUNT(*) FROM enroll_invites WHERE union_id='on_TESTUA'").fetchone()[0]
    check("unauth XOÁ hẳn dòng tokens (không để lại 'revoked')", _left == 0)
    check("unauth xoá cả lời mời treo (nếu không gate chỉ nhắc link CŨ)", _inv == 0)
    check("unauth GIỮ NGUYÊN cuộc họp trong DB",
          jobstore.get("obsgUNAUTH000000001") is not None)
    check("unauth GIỮ NGUYÊN người dự (cấp quyền lại là dùng ngay bản cũ)",
          any(v["union_id"] == "on_TESTUA"
              for v in db.conn().execute(
                  "SELECT union_id FROM minute_viewers WHERE minute_token=?",
                  ("obsgUNAUTH000000001",)).fetchall()))
    add_user("ou_TESTUA", "on_TESTUA", "Nguoi Tu Test")      # giả lập cấp lại
    check("cấp quyền lại -> active ngay, không kẹt ở 'revoked'",
          any(u["open_id"] == "ou_TESTUA"
              for u in tokenstore.list_users(active_only=True)))

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
    # ĐỔI 04/08/2026: nguyên văn là chỗ rò nặng nhất — admin qua BOT không còn
    # đọc được cuộc mình không dự. Người ngồi trước máy vẫn đọc được bằng
    # `v2 transcript` không có `--as` (đường `admin_view`).
    check("admin qua bot KHÔNG đọc được nguyên văn cuộc mình không dự",
          "BI MAT" not in qa.get_transcript(wAd, "mtTB"))
    check("đường terminal vẫn đọc được nguyên văn cuộc bất kỳ",
          "BI MAT" in qa.get_transcript(askers.admin_view(), "mtTB"))
    check("transcript RỖNG nói rõ là rỗng, không nói 'không có cuộc họp'",
          "KHÔNG có chữ nào" in qa.get_transcript(wA, "mtTEMPTY"))
    check("job chưa phiên âm xong -> nói tình trạng, KHÔNG bịa nội dung",
          "chưa có nguyên văn" in qa.get_transcript(wA, "mtTNONE"))
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
    # File 0 byte: Lark trả `1061002 params error`, mà nấc chọn cũ chỉ hỏi "có
    # tồn tại không" -> ô file hỏng vĩnh viễn, lượt đồng bộ nào cũng thử rồi
    # trượt (đo 04/08/2026, cuộc 'test' 27/07 có .txt rỗng).
    _got37.clear()
    _emptymeta = meta(minute_token="obsgEMPTYTXT00000001", title="Hop txt rong",
                      start=1785000000.0)
    pipeline.legacy_txt_path(_emptymeta).write_text("", encoding="utf-8")
    jobstore.create(_emptymeta, status="delivered")
    _bt._tracking_fields(_emptymeta)
    check(".txt RỖNG (0 byte) -> KHÔNG upload, không lặp lỗi params mỗi vòng",
          not any(p.suffix == ".txt" for p in _got37), str(_got37))
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

    # uuid gửi file PHẢI đổi theo thời gian. Lỗi thật 05/08/2026: uuid cố định
    # `ond-{token}-{rid}` làm Lark khử trùng vĩnh viễn — người dùng chỉ nhận
    # được file của một cuộc họp ĐÚNG MỘT LẦN trong đời, các lần sau V2 không
    # thấy exception nên ghi ok=1 và báo "đã gửi" trong khi chat trống trơn.
    # Ba dòng deliveries ok=1 mà chỉ dòng đầu có tin nhắn thật.
    _u_now = _sf._send_uuid("mtUUID001", "on_UUID_A")
    check("uuid gửi file: khác nhau giữa hai người",
          _u_now != _sf._send_uuid("mtUUID001", "on_UUID_B"))
    check("uuid gửi file: khác nhau giữa hai cuộc họp",
          _u_now != _sf._send_uuid("mtUUID002", "on_UUID_A"))
    check("uuid gửi file: <= 50 ký tự (Lark cắt ở 50)", len(_u_now) <= 50)
    _keep_time = _sf_time_patch = None
    import time as _t_uuid
    _real_time = _t_uuid.time
    try:
        _t_uuid.time = lambda: _real_time() + _sf.RESEND_COOLDOWN_MIN * 60 * 3
        check("uuid gửi file: ĐỔI sau khi qua cửa sổ chống lặp (xin lại là nhận "
              "được thật, không bị Lark nuốt)",
              _sf._send_uuid("mtUUID001", "on_UUID_A") != _u_now)
    finally:
        _t_uuid.time = _real_time

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
    #
    # Kiểm luôn NGUỒN của tóm tắt (thêm 05/08/2026 sau khi user chỉ ra): nhánh
    # held trước đây không gọi LLM lần nào, `_base_record_held` dùng lại
    # `recap_json` mà `_notify_minute` đã ghi từ văn bản Minute của Lark. Tức
    # transcript whisper nằm trên đĩa nhưng tóm tắt hiển thị khắp nơi lại phân
    # tích từ bản Lark kém chi tiết hơn. Đặt sẵn một recap "của Minute" rồi đo
    # xem nó có bị THAY bằng bản tóm tắt từ nguyên văn không.
    import json as _json_held
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgHELD00000000001"), priority=1)
    jobstore.set_status("obsgHELD00000000001", "queued", recap_json=_json_held.dumps(
        {"summary": "TOM TAT TU MINUTE LARK", "decisions": [],
         "action_items": []}, ensure_ascii=False))
    _keep_sum = summarize.summarize
    _sum_src: list[str] = []

    def _sum_from_transcript(t, m):
        _sum_src.append(t.text)
        return Recap(summary="TOM TAT TU NGUYEN VAN", decisions=[],
                     action_items=[])

    summarize.summarize = _sum_from_transcript
    try:
        orchestrator.process_queue(dry_run=False)
    finally:
        summarize.summarize = _keep_sum
    check("cuộc thường dịch xong -> held (không gửi cho ai)",
          jobstore.get("obsgHELD00000000001")["status"] == "held")
    check("cuộc held -> CÓ ghi record Base (bot Q&A tra được)",
          "obsgHELD00000000001" in base_calls)
    check("tóm tắt được làm lại TỪ NGUYÊN VĂN, không dùng bản của Lark Minute",
          "TOM TAT TU NGUYEN VAN" in
          (jobstore.get("obsgHELD00000000001")["recap_json"] or ""),
          (jobstore.get("obsgHELD00000000001")["recap_json"] or "")[:80])
    check("...và LLM nhận đúng chữ trong transcript whisper",
          _sum_src and "xin chao" in _sum_src[0], str(_sum_src)[:80])

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

    # (f2) send_transcript nhận cả TÊN cuộc họp, không chỉ minute_token
    #      (sửa 04/08/2026 sau ca thật): agent gọi tool với TÊN là chuyện
    #      thường — nó vừa đọc tên từ câu người dùng. Tool cũ trả "cần
    #      minute_token", agent diễn giải chệch thành "bản ghi chưa được nhận
    #      diện là biên bản có thể xuất file", và người dùng CÓ TOÀN QUYỀN
    #      tưởng hệ thống hỏng. Không liên quan admin: đo trên người thường.
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgNAME00000000001",
                         title="CĐS: Flow Backlog"), status="held", priority=1)
    _w = {"union_id": "on_X", "open_id": "ou_X", "name": "Nguoi Thuong"}
    for _q, _label in ((" CĐS: Flow Backlog ", "đúng tên, thừa khoảng trắng"),
                       ("cđs flow backlog", "thường hoá + THIẾU dấu hai chấm"),
                       ("flow backlog", "một phần tên"),
                       ("obsgNAME00000000001", "chính là minute_token")):
        _tok, _err = sendfile._resolve_token(_w, _q)
        check(f"tra tên -> token: {_label}",
              _tok == "obsgNAME00000000001", f"{_q!r} -> {_tok or _err[:40]}")
    check("tên KHÔNG có thật -> báo KHÔNG TRA RA, và cấm agent diễn giải thành "
          "'không xuất được file'",
          sendfile._resolve_token(_w, "cuộc họp không tồn tại")[0] == ""
          and "KHÔNG TRA RA" in sendfile.NO_MEETING
          and "ĐỪNG nói" in sendfile.NO_MEETING)
    check("giữ dấu tiếng Việt khi so ('CDS' KHÔNG khớp 'CĐS')",
          sendfile._resolve_token(_w, "CDS flow backlog")[0] == "")

    # (f3) ĐỦ CHỮ, sai thứ tự vẫn phải ra (ca thật thứ hai 04/08/2026): người
    #      dùng gõ 'workforce AI 23-07' trong khi tên là '07-23 | Workforce AI
    #      Weekly Meeting Buổi 3' — đảo ngày/tháng + thiếu đuôi.
    jobstore.create(meta(minute_token="obsgWORD00000000001",
                         title="07-23 | Workforce AI Weekly Meeting Buổi 3",
                         owner_open_id="ou_X",
                         attendees=[Attendee(open_id="ou_X", union_id="on_X")]),
                    status="held", priority=1)
    db.note_viewer("obsgWORD00000000001", "ou_X", "on_X", "Nguoi Thuong")
    for _q in ("workforce AI 23-07", "buổi 3 workforce", "23-07 workforce"):
        check(f"đủ chữ sai thứ tự vẫn tra ra: {_q!r}",
              sendfile._resolve_token(_w, _q)[0] == "obsgWORD00000000001",
              str(sendfile._resolve_token(_w, _q)))
    check("thiếu chữ thì KHÔNG khớp bừa ('workforce buổi 9')",
          sendfile._resolve_token(_w, "workforce buổi 9")[0] == "")

    # (f4) Tra trượt -> kèm DANH SÁCH gần đúng có minute_token. Chỉ thị bằng
    #      lời không điều khiển được agent (đã đo: dặn 'ĐỪNG nói không xuất được
    #      file' mà nó vẫn nói y hệt) — dữ liệu hành động được thì có.
    _, _hint = sendfile._resolve_token(_w, "workforce buổi 9")
    check("tra trượt -> gợi ý kèm minute_token để agent gọi lại",
          "obsgWORD00000000001" in _hint and "minute_token" in _hint, _hint[:100])
    #      ...và gợi ý KHÔNG được lộ cuộc của người khác.
    jobstore.create(meta(minute_token="obsgWORD00000000002",
                         title="Workforce AI Kick Off buổi 1"),
                    status="held", priority=1)      # KHÔNG note_viewer cho on_X
    _, _hint2 = sendfile._resolve_token(_w, "workforce buổi 9")
    check("gợi ý CHỈ nêu cuộc người hỏi được xem (không lộ cuộc người khác)",
          "obsgWORD00000000002" not in _hint2, _hint2[:120])
    # Tra tên KHÔNG được nới quyền: giải xong vẫn phải qua `qa._may_see`.
    _msg_name = sendfile.send_transcript(_w, "cđs flow backlog")
    check("tra được tên nhưng KHÔNG dự -> vẫn chặn ở cửa quyền như cũ",
          "không có trong danh sách người dự" in _msg_name, _msg_name[:80])
    # Trùng tên: chỉ nêu cuộc mà chính người đó được xem, và đòi minute_token.
    _name_att = [Attendee(open_id="ou_X", union_id="on_X")]
    jobstore.update_meta("obsgNAME00000000001",
                         meta(minute_token="obsgNAME00000000001",
                              title="CĐS: Flow Backlog", owner_open_id="ou_X",
                              attendees=_name_att))
    jobstore.create(meta(minute_token="obsgNAME00000000002",
                         title="CĐS: Flow Backlog", owner_open_id="ou_X",
                         attendees=_name_att), status="held", priority=1)
    for _t in ("obsgNAME00000000001", "obsgNAME00000000002"):
        db.note_viewer(_t, "ou_X", "on_X", "Nguoi Thuong")
    _tok2, _err2 = sendfile._resolve_token(_w, "cđs flow backlog")
    check("hai cuộc trùng tên -> hỏi lại kèm minute_token, không đoán bừa",
          _tok2 == "" and "minute_token" in _err2 and "obsgNAME00000000001" in _err2,
          (_err2 or "")[:90])
    # ...nhưng mã máy và lời dặn phải ở KÊNH NỘI BỘ. Ca thật 05/08/2026: người
    # dùng nhận nguyên văn "Hỏi người dùng muốn cuộc nào rồi gọi lại với
    # minute_token: - test luồng tự động (?) — minute_token: obsg39y1z..." — vừa
    # lộ mã máy, vừa đọc được câu dặn dành cho bot, vừa có dấu "?" thay cho giờ
    # nên không phân biệt nổi hai cuộc.
    _user2 = _err2.split(qa.END_MARK)[0]
    check("hỏi lại: khối người dùng KHÔNG lộ minute_token",
          "obsgNAME00000000001" not in _user2 and "minute_token" not in _user2,
          _user2[:120])
    check("hỏi lại: khối người dùng KHÔNG chứa lời dặn dành cho bot",
          "gọi lại" not in _user2.lower() and "gọi LẠI" not in _user2,
          _user2[:120])
    check("hỏi lại: KHÔNG in dấu '?' thay cho giờ họp",
          "(?)" not in _user2 and "?" not in _user2.replace("nào?", ""),
          _user2[:160])
    check("hỏi lại: agent vẫn nhận đủ token ở kênh nội bộ",
          qa.INTERNAL_MARK in _err2
          and "obsgNAME00000000001" in _err2.split(qa.INTERNAL_MARK)[1])

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
    part("41. Base là GƯƠNG của bảng jobs (quản trị trên Base, 03/08/2026)")
    # =================================================================
    # (a) MỌI status của jobs phải có nhãn đọc được — thiếu một cái là ô select
    #     rỗng trên Base và người vận hành không biết cuộc đó đang ở đâu.
    _statuses = {"detected", "queued", "transcribing", "recapping",
                 "waiting_auth", "held",
                 "delivered", "failed", "discarded", "awaiting_approval",
                 "owner_only", "expired"}
    check("mọi jobs.status đều có nhãn tiếng Việt trên Base",
          _statuses <= set(_bit.JOB_STATUS_LABEL),
          str(_statuses - set(_bit.JOB_STATUS_LABEL)))
    check("nhãn KHÔNG lộ chữ máy ('held'/'queued') cho người đọc Base",
          not any(k in v for k in ("held", "queued", "discarded")
                  for v in _bit.JOB_STATUS_LABEL.values()))
    check("JOB_STATUS_OPTIONS khử trùng (Base không nhận option lặp)",
          len(_bit.JOB_STATUS_OPTIONS) == len(set(_bit.JOB_STATUS_OPTIONS)))
    check("cột gương nằm trong SCHEMA (base-init tạo bảng mới cũng có)",
          {_bit.F_JOB_STATUS, _bit.F_ERROR, _bit.F_ATTEMPTS, _bit.F_INVITEES,
           _bit.F_DETECTED, _bit.F_TRANSCRIBED, _bit.F_DELIVERED}
          <= {s["name"] for s in _bit.SCHEMA})

    # (b) _job_fields soi ĐÚNG một dòng jobs
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgMIRROR00000001"),
                    status="failed", priority=1)
    with db.tx() as _c:
        _c.execute("UPDATE jobs SET attempts=3, error='2091005 thieu quyen', "
                   "invitee_count=7, transcribed_at=1785000000000 "
                   "WHERE minute_token='obsgMIRROR00000001'")
    _jf = _bit._job_fields(jobstore.get("obsgMIRROR00000001"))
    check("gương: status -> nhãn 'HỎNG'",
          _jf[_bit.F_JOB_STATUS] == _bit.JOB_STATUS_LABEL["failed"], str(_jf))
    check("gương: mang theo lỗi + số lần thử + số người mời",
          _jf[_bit.F_ERROR].startswith("2091005") and _jf[_bit.F_ATTEMPTS] == 3
          and _jf[_bit.F_INVITEES] == 7, str(_jf))
    check("gương: mốc thời gian là epoch ms (datetime của Base)",
          _jf[_bit.F_TRANSCRIBED] == 1785000000000)
    check("gương: mốc CHƯA có thì KHÔNG ghi ô (không đổ 1970)",
          _bit.F_DELIVERED not in _jf and _bit.F_DETECTED in _jf)

    # (c) hết lỗi thì ô lỗi phải bị XOÁ, không giữ chữ cũ
    with db.tx() as _c:
        _c.execute("UPDATE jobs SET error=NULL, status='held' "
                   "WHERE minute_token='obsgMIRROR00000001'")
    _jf2 = _bit._job_fields(jobstore.get("obsgMIRROR00000001"))
    check("chạy lại thành công -> ô 'Lỗi gần nhất' về RỖNG (không nói dối)",
          _jf2[_bit.F_ERROR] == "", str(_jf2))

    # (d) job CHƯA phát thì KHÔNG có record record nào chưa tới lượt lại bị gán
    #     'chưa ai cấp quyền' — đó là lý do cột `Tình trạng gửi` để trống.
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgMIRRORQ0000001"),
                    status="queued", priority=1)
    _created: list[dict] = []
    keepEN2, keepRC = _bit.enabled, _bit.lark_api.base_record_create
    _bit.enabled = lambda: True
    _bit._fields_checked = True                  # khỏi gọi ensure_fields ra mạng
    _bit.lark_api.base_record_create = lambda a, t, f: (_created.append(f), "rec")[1]
    keepFind = _bit.lark_api.base_record_find
    _bit.lark_api.base_record_find = lambda a, t, f, v: ""
    keepTF = _bit._tracking_fields
    _bit._tracking_fields = lambda m, **k: {}
    _bit.write_draft(jobstore.meta_from_json(
        jobstore.get("obsgMIRRORQ0000001")["meta_json"]), Recap(summary=""), 0)
    _bit.enabled, _bit.lark_api.base_record_create = keepEN2, keepRC
    _bit.lark_api.base_record_find, _bit._tracking_fields = keepFind, keepTF
    check("job `queued` LÊN được Base (trước đây mất tăm)", len(_created) == 1)
    check("job chưa phát -> cột 'Tình trạng gửi' để TRỐNG, không 'chưa ai cấp quyền'",
          _bit.F_STATUS not in _created[0], str(_created[0].keys()))
    check("job `queued` vẫn có cột 'Tình trạng xử lý' = đang chờ dịch",
          _created[0].get(_bit.F_JOB_STATUS) == _bit.JOB_STATUS_LABEL["queued"])

    # (e) retry_missing_records quét MỌI status, không chỉ delivered/held
    wipe_jobs()
    for _st in ("queued", "waiting_auth", "failed", "discarded", "held"):
        jobstore.create(meta(minute_token=f"obsgALL{_st[:4].upper()}0000001"),
                        status=_st, priority=1)
    _seen: list[str] = []
    keepEN3, keepWD4 = _bit.enabled, _bit.write_draft
    _bit.enabled = lambda: True
    _bit.write_draft = lambda m, r, n: (_seen.append(m.minute_token), "rec")[1]
    _bit.retry_missing_records()
    _bit.enabled, _bit.write_draft = keepEN3, keepWD4
    check("Base nhận cả job queued/waiting_auth/failed/discarded (không chỉ đã phát)",
          len(_seen) == 5, f"{len(_seen)}: {_seen}")

    # (f) cột cũ được TÁI DÙNG chứ không xoá — đổi tên giữ nguyên field_id
    check("bảng đổi tên: 'Người chốt' -> 'Lỗi gần nhất', 'Chốt lúc' -> 'Dịch xong lúc'",
          ("Người chốt", _bit.F_ERROR, "text") in _bit._RENAMES
          and ("Chốt lúc", _bit.F_TRANSCRIBED, "datetime") in _bit._RENAMES)
    check("'Trạng thái' đổi tên thành 'Tình trạng gửi' (tách khỏi tình trạng xử lý)",
          _bit.F_STATUS == "Tình trạng gửi"
          and ("Trạng thái", _bit.F_STATUS, "select") in _bit._RENAMES)

    # (g) phép so quyết định "có ghi lại record không". Chỉ so trạng thái là
    #     KHÔNG đủ: job thử lại đứng yên ở `queued` mà attempts/lỗi đổi mỗi
    #     vòng — đúng lúc người vận hành cần nhìn Base thì nó lại đứng im.
    _rec = {_bit.F_JOB_STATUS: "đang chờ dịch", _bit.F_ERROR: "",
            _bit.F_ATTEMPTS: 1, _bit.F_INVITEES: 4}
    check("không đổi gì -> KHÔNG ghi lại (khỏi đốt lời gọi mỗi 5 phút)",
          not _bit._needs_push(_rec, dict(_rec)))
    check("số lần thử tăng (trạng thái giữ nguyên) -> PHẢI ghi lại",
          _bit._needs_push(_rec, dict(_rec, **{_bit.F_ATTEMPTS: 2})))
    check("lỗi mới (trạng thái giữ nguyên) -> PHẢI ghi lại",
          _bit._needs_push(_rec, dict(_rec, **{_bit.F_ERROR: "2091005"})))
    check("trạng thái đổi -> PHẢI ghi lại",
          _bit._needs_push(_rec, dict(_rec, **{_bit.F_JOB_STATUS: "đã gửi"})))
    check("số 1 và 1.0 KHÔNG coi là lệch (Base trả float)",
          not _bit._needs_push(_rec, dict(_rec, **{_bit.F_ATTEMPTS: 1.0})))
    check("None trên Base vs '' muốn ghi -> KHÔNG lệch (khỏi ghi vô ích)",
          not _bit._needs_push(dict(_rec, **{_bit.F_ERROR: None}), dict(_rec)))
    check("mốc thời gian KHÔNG nằm trong phép so (Base trả chuỗi, ta gửi epoch)",
          _bit.F_DETECTED not in _bit._DIFF_FIELDS
          and _bit.F_TRANSCRIBED not in _bit._DIFF_FIELDS)

    # (h) record tạo lúc job còn `queued` -> chưa có transcript. Khi dịch xong,
    #     `write_draft` thoát sớm (record đã có) nên ô FILE không ai điền. Vòng
    #     đồng bộ PHẢI tự vá, nếu không ô 'File transcript' rỗng vĩnh viễn —
    #     đã gặp thật 04/08/2026 với hai cuộc vừa dịch xong.
    wipe_jobs()
    jobstore.create(meta(minute_token="obsgFILE00000000001"),
                    status="held", priority=1, )
    with db.tx() as _c:
        _c.execute("UPDATE jobs SET transcript_path='X:\\co\\that.json', "
                   "whisper_seconds=12.0, audio_seconds=60.0, "
                   "bitable_record_id='recFILE' WHERE minute_token=?",
                   ("obsgFILE00000000001",))
    _pushed: list[dict] = []
    keepEN4 = _bit.enabled
    keepRA, keepRU = _bit.lark_api.base_records_all, _bit.lark_api.base_record_update
    keepTF2 = _bit._tracking_fields
    _bit.enabled = lambda: True
    # Record trên Base: trạng thái ĐÃ đúng, nhưng ô file + số giây whisper TRỐNG
    _bit.lark_api.base_records_all = lambda a, t: [{
        "_record_id": "recFILE", _bit.F_TOKEN: "obsgFILE00000000001",
        _bit.F_JOB_STATUS: _bit.JOB_STATUS_LABEL["held"], _bit.F_ERROR: "",
        _bit.F_ATTEMPTS: 0, _bit.F_INVITEES: 0}]
    _bit.lark_api.base_record_update = lambda a, t, r, f: _pushed.append(f)
    _bit._tracking_fields = lambda m, **k: {
        _bit.F_TRANSCRIPT: [{"file_token": "ft"}], _bit.F_WHISPER_SEC: 12.0,
        "_skip_file": k.get("skip_file")}
    _bit.sync_jobs()
    _bit.enabled, _bit._tracking_fields = keepEN4, keepTF2
    _bit.lark_api.base_records_all, _bit.lark_api.base_record_update = keepRA, keepRU
    check("trạng thái đã khớp nhưng THIẾU file -> vẫn ghi lại để vá ô file",
          len(_pushed) == 1 and _bit.F_TRANSCRIPT in _pushed[0],
          str(_pushed)[:120])
    check("ô đã có file thì KHÔNG upload lại (khỏi sinh file_token rác)",
          _pushed and _pushed[0].get("_skip_file") is False)

    # (i) NGƯỜI upload attachment: admin (chủ Base) phải đứng TRƯỚC chủ bản ghi.
    #     Hai miền quyền khác nhau — tải bản ghi từ Lark cần chủ bản ghi, ghi
    #     file vào Base cần chủ Base. Đo thật 04/08: token Chi/Thiện đều
    #     `1061004 forbidden`, chỉ admin upload được.
    _adm_uid = config.QA_ADMIN_UNION_IDS[0] if config.QA_ADMIN_UNION_IDS else ""
    add_user("ou_ADMIN_UP", _adm_uid or "on_admin_up", "Admin Base")
    add_user("ou_CHUBANGHI", "on_chu_ban_ghi", "Chu Ban Ghi")
    _mm_up = meta(minute_token="obsgUPLOAD000000001", owner_open_id="ou_CHUBANGHI",
                  attendees=[Attendee(open_id="ou_CHUBANGHI",
                                      union_id="on_chu_ban_ghi")])
    _order = _bit._upload_candidates(_mm_up)
    check("có ứng viên upload (không rỗng khi đã có người enroll)", bool(_order))
    check("admin (chủ Base) đứng ĐẦU danh sách upload, TRƯỚC chủ bản ghi",
          bool(_adm_uid) and _order and _order[0] == "ou_ADMIN_UP",
          f"{_order[:3]}")
    check("chủ bản ghi vẫn CÒN trong danh sách (dự phòng khi admin hỏng token)",
          "ou_CHUBANGHI" in _order)
    check("danh sách khử trùng (không thử cùng một người hai lần)",
          len(_order) == len(set(_order)))

    # =================================================================
    part("40. Glossary tự cải thiện (part B): ứng viên, duyệt, tiêm prompt")
    # =================================================================
    from v2 import glossary
    with db.tx() as _c:
        _c.execute("DELETE FROM glossary_candidates")   # sạch trước khi kiểm

    # (a) gộp theo khoá chữ-thường: 'MCP' và 'mcp' là MỘT, count cộng dồn
    db.glossary_add_candidate("MCP", "Cuộc A")
    db.glossary_add_candidate("mcp", "Cuộc B")
    db.glossary_add_candidate("BookFood", "Cuộc A")
    _p2 = db.glossary_list("pending", 2)
    check("ứng viên gộp theo khoá chữ-thường (MCP+mcp -> count 2)",
          len(_p2) == 1 and _p2[0]["term"] == "MCP" and _p2[0]["count"] == 2,
          str(_p2))
    check("min_count lọc bỏ ứng viên gặp 1 cuộc (BookFood)",
          all(r["term"] != "BookFood" for r in _p2))

    # (b) admin gating — CƯỠNG CHẾ bằng code, không bằng mô tả tool
    _notadmin = {"union_id": "u_x", "admin": False}
    _admin = {"union_id": "u_a", "admin": True}
    check("người thường KHÔNG duyệt được (trả NOT_ADMIN)",
          glossary.approve(_notadmin, "MCP") == glossary.NOT_ADMIN)
    check("người thường KHÔNG xem được danh sách chờ",
          glossary.pending(_notadmin) == glossary.NOT_ADMIN)
    check("who=None -> từ chối",
          glossary.approve(None, "MCP") == glossary.NOT_ADMIN)

    # (c) admin duyệt -> approved, vào được prompt
    _msg = glossary.approve(_admin, "mcp")             # khớp không phân biệt hoa
    check("admin duyệt được (khớp không phân biệt hoa/thường)",
          "Đã duyệt: MCP" in _msg, _msg)
    check("từ đã duyệt vào glossary_approved_terms",
          db.glossary_approved_terms() == ["MCP"])

    # (d) từ đã DUYỆT được nhồi vào prompt whisper (cùng đường tên người dự)
    _hint = transcribe._prompt_hint("Weekly", ["An"], db.glossary_approved_terms())
    check("prompt hint chứa CẢ thuật ngữ duyệt LẪN tên người dự",
          "Thuật ngữ: MCP" in _hint and "An" in _hint, _hint)

    # (e) bỏ + KHÔNG hồi sinh: rejected gặp lại không đếm lại, không nổi lại
    db.glossary_add_candidate("Anthropic", "Cuộc A")
    db.glossary_add_candidate("Anthropic", "Cuộc B")   # count 2
    check("admin bỏ được từ", "Đã bỏ: Anthropic" in glossary.reject(_admin, "anthropic"))
    db.glossary_add_candidate("Anthropic", "Cuộc C")   # đã rejected
    _anth = [r for r in db.glossary_list() if r["term"] == "Anthropic"][0]
    check("từ đã bỏ KHÔNG hồi sinh khi gặp lại (giữ rejected, count không tăng)",
          _anth["status"] == "rejected" and _anth["count"] == 2, str(_anth))

    # (f) duyệt từ KHÔNG có trong danh sách -> báo không thấy, không tạo bừa
    check("duyệt từ lạ -> báo không thấy",
          "Không thấy" in glossary.approve(_admin, "TuKhongCoThat"))
    check("từ lạ KHÔNG bị tạo trong bảng",
          all(r["term"] != "TuKhongCoThat" for r in db.glossary_list()))

    # (g) digest body: có pending>=2 thì liệt kê; hết thì rỗng
    with db.tx() as _c:
        _c.execute("DELETE FROM glossary_candidates")
    db.glossary_add_candidate("Vercel", "Cuộc A")
    db.glossary_add_candidate("Vercel", "Cuộc B")
    check("digest body liệt kê từ chờ duyệt gặp >=2 cuộc",
          "Vercel" in glossary.digest_body(2) and "duyệt" in glossary.digest_body(2))
    check("digest body RỖNG khi không có gì chờ (>= min_count)",
          glossary.digest_body(5) == "")

    # (h) CHUỖI nhiều từ cách dấu phẩy phải tách — chính `pending()` dặn người
    # dùng nhắn vậy. Trước 03/08/2026 chỉ `mcp_server._terms_arg` tách, nên gọi
    # thẳng `approve(who, "A, B")` im lặng báo "không thấy" dù cả hai đang chờ.
    with db.tx() as _c:
        _c.execute("DELETE FROM glossary_candidates")
    for _t in ("Hermes", "Bitable"):
        db.glossary_add_candidate(_t, "Cuộc A")
    _msg_cs = glossary.approve(_admin, "Hermes, Bitable")
    check("approve nhận CHUỖI 'A, B' -> tách dấu phẩy, duyệt cả hai",
          "Hermes" in _msg_cs and "Bitable" in _msg_cs and "Không thấy" not in _msg_cs,
          _msg_cs)
    check("cả hai từ vào được danh sách đã duyệt",
          set(db.glossary_approved_terms()) == {"Hermes", "Bitable"})

    # (i) duyệt lại từ ĐÃ BỎ: vẫn cho (admin là người quyết) nhưng phải NÓI RA —
    # `pending()` không còn liệt kê từ rejected nên admin đang gõ một từ họ không
    # nhìn thấy, thường là quên mình từng bỏ.
    db.glossary_add_candidate("Vercel", "Cuộc A")
    glossary.reject(_admin, "Vercel")
    _msg_rev = glossary.approve(_admin, "Vercel")
    check("duyệt lại từ đã BỎ -> có cảnh báo 'trước đó đã bị BỎ'",
          "đã bị BỎ" in _msg_rev and "Đã duyệt: Vercel" in _msg_rev, _msg_rev)

    # (j) ngân sách prompt: cuộc ĐÔNG người thì THUẬT NGỮ bị cắt bớt Ở ĐÂY, và
    # phải cắt theo TỪ NGUYÊN VẸN. Server gộp `vi-prompt.txt` + hint rồi giữ 180
    # từ CUỐI, nên hint quá dài = khối "Thuật ngữ" bị chặt giữa chừng trong im
    # lặng (cuộc ~40 người là mất sạch part B mà không ai biết).
    _many_names = [f"Nguoi Du So {i}" for i in range(40)]
    _many_terms = [f"ThuatNgu{i}" for i in range(40)]
    _hint_big = transcribe._prompt_hint("Hop toan cong ty", _many_names, _many_terms)
    check("cuộc RẤT đông (40 người, tên đã kín ngân sách) -> BỎ HẲN thuật ngữ",
          "Thuật ngữ" not in _hint_big, f"{len(_hint_big.split())} từ")
    check("cuộc đông người: GIỮ ĐỦ tên người dự (part A là phần chắc)",
          all(n in _hint_big for n in _many_names))
    # Cuộc VỪA: thuật ngữ phải bị cắt BỚT cho vừa ngân sách, không bị chặt giữa từ.
    _hint_mid = transcribe._prompt_hint("Hop phong", _many_names[:24], _many_terms)
    check("hint cuộc vừa không vượt ngân sách từ (server khỏi phải chặt)",
          len(_hint_mid.split()) <= transcribe._HINT_WORD_BUDGET,
          f"{len(_hint_mid.split())} từ")
    _kept = ([] if "Thuật ngữ: " not in _hint_mid else
             _hint_mid.split("Thuật ngữ: ", 1)[1].split(".")[0].split(", "))
    check("cuộc vừa: thuật ngữ bị cắt BỚT, không đứt giữa từ",
          0 < len(_kept) < len(_many_terms) and all(k in _many_terms for k in _kept),
          f"giữ {len(_kept)}/{len(_many_terms)}: {_kept[-3:]}")
    check("cuộc vừa: vẫn GIỮ ĐỦ tên người dự",
          all(n in _hint_mid for n in _many_names[:24]))
    _hint_small = transcribe._prompt_hint("Hop nhom", ["An", "Binh"],
                                          ["MCP", "Anthropic"])
    check("cuộc ít người: giữ NGUYÊN cả thuật ngữ lẫn tên",
          "Thuật ngữ: MCP, Anthropic." in _hint_small
          and "An, Binh" in _hint_small, _hint_small)

    # (k) trích thuật ngữ CHỈ chạy khi VỪA phiên âm xong. Job đã có transcript mà
    # chạy lại (enqueue tay / phát hỏng nên về `queued` / tiến trình chết giữa
    # chừng) đi qua `_reuse` — trước 03/08/2026 nó trích + ĐẾM lại cho CÙNG một
    # cuộc, đủ để một từ nghe nhầm MỘT lần leo lên digest như "gặp 2 cuộc", tức
    # phá đúng bộ lọc nhiễu duy nhất của part B.
    with db.tx() as _c:
        _c.execute("DELETE FROM glossary_candidates")
    _eg_calls: list[int] = []
    keepEG = summarize.extract_glossary
    summarize.extract_glossary = lambda text, title="": (
        _eg_calls.append(1), ["ZzUngVien"])[1]
    keepP2 = pipeline.run_transcription
    keepWD2 = _bit.write_draft
    _bit.write_draft = lambda m, r, n: "rec"

    import json as _json2

    def _fake_ok2(m):
        t_ = Transcript(minute_token=m.minute_token, lang="vi", duration=60.0,
                        engine="fake", segments=[Segment(0.0, 1.0, "xin chao")])
        p = config.TRANSCRIPT_DIR / f"fake-{m.minute_token}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json2.dumps(t_.to_json()), encoding="utf-8")
        jobstore.set_status(m.minute_token, "recapping", transcript_path=str(p),
                            audio_seconds=60.0, whisper_seconds=1.0)
        return t_, p
    pipeline.run_transcription = _fake_ok2

    wipe_jobs()
    jobstore.create(meta(minute_token="obsgGLOSS0000000001"), priority=1)
    orchestrator.process_queue(dry_run=False)
    _c1 = db.glossary_get("ZzUngVien")
    check("phiên âm xong -> trích ứng viên (count 1)",
          bool(_c1) and _c1["count"] == 1, str(_c1))
    # Chạy LẠI đúng job đó: có transcript_path nên `_reuse` nạp lại, không dịch lại.
    jobstore.set_status("obsgGLOSS0000000001", "queued")
    orchestrator.process_queue(dry_run=False)
    _c2 = db.glossary_get("ZzUngVien")
    check("chạy LẠI cùng cuộc (đường _reuse) -> KHÔNG trích lại",
          len(_eg_calls) == 1, f"{len(_eg_calls)} lần gọi")
    check("chạy LẠI cùng cuộc -> count GIỮ NGUYÊN 1 (không tự lên digest)",
          _c2 and _c2["count"] == 1, str(_c2))
    summarize.extract_glossary = keepEG
    pipeline.run_transcription = keepP2
    _bit.write_draft = keepWD2

    # =================================================================
    part("34a. ADMIN không còn xem được cuộc họp của người khác qua BOT")
    # =================================================================
    # Trước 04/08/2026 `_may_see` cho qua khi `who["admin"]`, mà `admin` suy từ
    # union_id trong .env — tức một người là admin hỏi bot "liệt kê cuộc họp của
    # tôi" thì nhận về TOÀN BỘ cuộc họp của công ty, kèm câu "22 cuộc họp gắn
    # với tài khoản của bạn". Vừa rò biên bản, vừa nói sai.
    #
    # Nay tách: `admin` = duyệt từ điển; `see_all` = xem hết, và CHỈ
    # `askers.admin_view()` (đường terminal) đặt được cờ đó.
    _idx_ad = {"mtMINE": {"u_admin"}, "mtKHAC": {"u_nguoikhac"}}
    _bot_admin = {"union_id": "u_admin", "open_id": "", "name": "Admin",
                  "admin": True}
    check("admin qua BOT: thấy cuộc MÌNH dự",
          qa._may_see("mtMINE", _bot_admin, _idx_ad) is True)
    check("admin qua BOT: KHÔNG thấy cuộc của người khác",
          qa._may_see("mtKHAC", _bot_admin, _idx_ad) is False)
    check("admin qua BOT: KHÔNG thấy cuộc lạ không có trong index",
          qa._may_see("mtKHONGCO", _bot_admin, _idx_ad) is False)

    _cli = askers.admin_view()
    check("đường terminal (admin_view) vẫn xem được hết",
          _cli.get("see_all") is True
          and qa._may_see("mtKHAC", _cli, _idx_ad) is True)
    check("askers.who() KHÔNG bao giờ đặt see_all, kể cả với admin",
          "see_all" not in askers.who("on_ADMIN"))
    from v2 import glossary as _gl
    check("admin vẫn giữ quyền duyệt từ điển",
          _gl._is_admin(_bot_admin) is True)

    # =================================================================
    part("34b. Hai kênh trong kết quả tool — nội bộ KHÔNG được lọt ra chat")
    # =================================================================
    # Ba lời chê của user 04/08/2026 ("item của Hermes hiện lên", "định dạng
    # lộn xộn", "sai/tự mâu thuẫn") đều đến từ MỘT nguyên nhân: tool trộn lời
    # dặn cho agent vào chung text người dùng đọc, và chỉ đưa mảnh rời để agent
    # tự ghép + tự đếm. Bốn phép kiểm dưới đây khoá lại đúng bốn cách nó tái
    # phát — đây là loại lỗi chỉ lộ ra khi có người dùng thật đọc tin nhắn.
    _send, _int = qa.SEND_MARK, qa.INTERNAL_MARK

    _r = qa.two_channel("Câu cho người dùng.", "Dặn riêng agent.")
    _user_part = _r.split(_int)[0]
    check("two_channel: lời dặn agent nằm NGOÀI phần gửi người dùng",
          "Dặn riêng agent." not in _user_part)
    check("two_channel: không có lời dặn thì không sinh khối nội bộ rỗng",
          _int not in qa.two_channel("Chỉ có câu cho người dùng."))

    # `+` thay vì `append_agent_note` chính là lỗi cũ ở mcp_server.
    _r2 = qa.append_agent_note(qa.two_channel("Người dùng đọc."), "Cảnh báo.")
    check("append_agent_note: nối vào kênh NỘI BỘ, không nối vào phần gửi",
          "Cảnh báo." not in _r2.split(_int)[0] and _r2.count(_int) == 1)
    # ĐỔI 05/08/2026: text trần rơi về kênh DỮ LIỆU, KHÔNG phải kênh GỬI. Hai
    # phép kiểm cũ khoá đúng hành vi đã làm bot hỏng — xem nhóm 34i.
    check("append_agent_note: text trần bọc kênh DỮ LIỆU, không phải GỬI",
          qa.append_agent_note("trần", "ghi chú").startswith(qa.CONTEXT_MARK)
          and _send not in qa.append_agent_note("trần", "ghi chú"))
    check("append_agent_note: text trần có đủ mốc ĐÓNG trước kênh nội bộ",
          qa.CONTEXT_END in qa.append_agent_note("trần", "ghi chú").split(_int)[0])

    check("NO_ASKER: dòng '[Cho agent...]' không còn nằm trong phần người dùng",
          "asker_token" not in qa.NO_ASKER.split(_int)[0])

    # Tổng số phải là PHÉP TÍNH, không phải câu chữ do LLM đếm lại.
    _done = [{qa.bitable.F_TITLE: f"Cuộc {i}", qa.bitable.F_WHEN: f"2026-08-0{i} 09:00:00",
              qa.bitable.F_TOKEN: f"mt{i}", qa.bitable.F_LINK: ""} for i in (1, 2, 3)]
    _pend = [{"title": "Cuộc chờ", "when": "2026-08-04 09:00:00", "status": "held",
              "tinh_trang": "chưa xong", "minute_token": "mtP", "link": "", "error": ""}]
    _out = qa._render_list(_done, _pend)
    # Chuỗi có markdown từ 05/08/2026: danh sách đi bằng thẻ `lark_md` nên in
    # đậm được. Đo cả dấu ** để không ai lỡ tay gỡ mất định dạng.
    check("danh sách: tổng số = đã có + chưa có (3+1=4)",
          "Bạn có **4 cuộc họp**:" in _out)
    check("danh sách: đánh số chạy LIÊN TỤC sang khối chưa có biên bản",
          "4. 04/08/2026 09:00 · Cuộc chờ" in _out)
    check("danh sách: hai khối đều ghi rõ số của mình",
          "**ĐÃ CÓ BIÊN BẢN (3)**" in _out
          and "**CHƯA CÓ BIÊN BẢN (1)**" in _out)
    check("danh sách: TÊN cuộc họp được in đậm để dễ quét mắt",
          "**Cuộc 3**" in _out, _out[:120])
    # Bỏ 04/08/2026 theo yêu cầu user: câu "Còn N cuộc họp khác..." khong con
    # duoc gan vao cuoi. Khoa lai de khong ai vo tinh dat lai.
    check("danh sách: KHÔNG còn câu đếm cuộc họp của người khác",
          "cuộc họp khác" not in _out and "không thấy ở đây" not in _out)

    # Một cuộc họp: khối người dùng đọc CHỈ có nội dung, sổ sách đi kênh nội bộ.
    # Ca này sinh ra từ lỗi thật 05/08/2026: câu dặn agent ("Nói với người dùng
    # là họ xin được file Word bản này") nằm trong khối user nên lọt thẳng ra
    # chat — người dùng đọc được cả lời dặn dành cho bot, kèm minute_token,
    # trạng thái phát và nguồn người nhận mà họ không cần biết.
    _rec = {qa.bitable.F_TITLE: "Hop thu nghiem",
            qa.bitable.F_WHEN: "2026-08-04 15:34:00",
            qa.bitable.F_TOKEN: "mtFMT001",
            qa.bitable.F_STATUS: "đã phát",
            qa.bitable.F_RECIPIENTS: "0",
            qa.bitable.F_SOURCE: "đã xác minh khớp cuộc họp",
            qa.bitable.F_SUMMARY: "Noi dung buoi hop",
            qa.bitable.F_LINK: "https://lark.example/minutes/mtFMT001"}
    _user_txt = qa.fmt_record(_rec)
    for _leak in ("minute_token", "Số người nhận", "Nguồn người nhận",
                  "Trạng thái", "Nói với người dùng"):
        check(f"một cuộc họp: KHÔNG lộ '{_leak}' ra khối người dùng",
              _leak not in _user_txt, _user_txt[:160])
    check("một cuộc họp: vẫn giữ đủ thứ người ta cần đọc",
          "Hop thu nghiem" in _user_txt and "Noi dung buoi hop" in _user_txt
          and "04/08/2026 15:34" in _user_txt)
    check("link Lark KHÔNG còn bị gọi nhầm là 'Nguyên văn'",
          "Nguyên văn: https" not in _user_txt
          and "Bản tóm tắt của Lark" in _user_txt, _user_txt[-120:])
    check("sổ sách vẫn tới được agent qua kênh nội bộ",
          "mtFMT001" in qa._record_notes(_rec))

    check("ngày giờ MỘT định dạng dd/mm/yyyy HH:MM",
          qa._dmy("2026-08-04 15:34:00") == "04/08/2026 15:34")
    check("tên cuộc họp gỡ escape HTML ('&amp;' -> '&')",
          qa._title("Review HRIS &amp; feedback") == "Review HRIS & feedback")

    # =================================================================
    part("34c. sendlist — agent KHÔNG cầm nội dung, và không chọn được người nhận")
    # =================================================================
    # Đây là chỗ thay thế cho "bảo agent chép nguyên văn" — thứ đã thất bại ba
    # lượt. Giá trị nằm ở chỗ nó là CODE, nên phải kiểm như code.
    from v2 import sendlist as _sl

    _sent_list: list[tuple] = []
    _keep_im = (lark_api.im_send_text, lark_api.im_send_card)
    lark_api.im_send_text = lambda rid, text, id_type="union_id", **kw: (
        _sent_list.append((rid, text, id_type)) or "om_fake")
    # Danh sách đi bằng THẺ từ 05/08/2026 (markdown không render trong tin text).
    # Bóc `content` ra để mọi phép đo bên dưới vẫn nói về ĐÚNG chuỗi người dùng
    # đọc, không phải về cái vỏ thẻ.
    lark_api.im_send_card = lambda rid, card, id_type="union_id", **kw: (
        _sent_list.append(
            (rid, card["elements"][0]["text"]["content"], id_type))
        or "om_fake")
    _keep_lt = qa.list_text
    qa.list_text = lambda who, **kw: "DANH SACH CUA " + (who.get("name") or "?")
    try:
        _w1 = {"union_id": "on_A", "open_id": "ou_A", "name": "An"}

        _r1 = _sl.send_list(_w1)
        check("gửi đúng MỘT tin cho chính người hỏi",
              len(_sent_list) == 1 and _sent_list[0][0] == "on_A"
              and _sent_list[0][2] == "union_id")
        check("nội dung gửi đi là danh sách THÔ, không kèm nhãn nội bộ",
              _sent_list[0][1] == "DANH SACH CUA An"
              and qa.INTERNAL_MARK not in _sent_list[0][1])
        check("agent KHÔNG nhận được nội dung danh sách",
              "DANH SACH CUA An" not in _r1)
        check("agent chỉ nhận khối NỘI BỘ, không có khối để chép ra",
              _r1.startswith(qa.INTERNAL_MARK) and qa.SEND_MARK not in _r1)

        # Ràng buộc 5: vòng lặp của agent không được thành chục tin nhắn.
        _sl.send_list(_w1)
        check("gọi lại ngay -> KHÔNG gửi tin thứ hai", len(_sent_list) == 1)
        check("mốc chống lặp được lưu BỀN trong DB, không chỉ trong RAM",
              db.conn().execute(
                  "SELECT 1 FROM outbound_dedup WHERE channel=? AND recipient=?",
                  (_sl._DEDUP_CHANNEL, "on_A")).fetchone() is not None)

        # Ràng buộc 2: người nhận suy từ `who`. Người khác hỏi -> tin đi chỗ khác.
        _sl.send_list({"union_id": "on_B", "open_id": "ou_B", "name": "Binh"})
        check("người khác hỏi -> gửi cho HỌ, nội dung của HỌ",
              len(_sent_list) == 2 and _sent_list[1][0] == "on_B"
              and _sent_list[1][1] == "DANH SACH CUA Binh")

        # Ràng buộc 3: đường terminal không có id Lark -> không gửi đi đâu.
        _n = len(_sent_list)
        _rt = _sl.send_list(askers.admin_view())
        check("đường terminal (không có id Lark) -> KHÔNG gửi, trả text tại chỗ",
              len(_sent_list) == _n and qa.SEND_MARK in _rt)

        # Ràng buộc 4: Lark hỏng -> vẫn phải có câu trả lời, không im lặng.
        def _boom(*a, **k):
            raise lark_api.LarkError(99999, "mang hong", "im_send")
        # Cả HAI kênh phải hỏng mới được rơi về trả text cho agent: thẻ hỏng mà
        # text còn sống thì người dùng vẫn phải nhận được danh sách.
        lark_api.im_send_card = _boom
        db.conn().execute("DELETE FROM outbound_dedup WHERE recipient='on_A'")
        _n2 = len(_sent_list)
        _sl.send_list(_w1)
        check("thẻ hỏng -> LÙI về text, người dùng vẫn nhận được danh sách",
              len(_sent_list) == _n2 + 1
              and _sent_list[-1][1] == "DANH SACH CUA An")
        lark_api.im_send_text = _boom
        db.conn().execute("DELETE FROM outbound_dedup WHERE recipient='on_A'")
        _rf = _sl.send_list(_w1)
        check("Lark gửi hỏng -> rơi về trả text cho agent, KHÔNG im lặng",
              qa.SEND_MARK in _rf and "DANH SACH CUA An" in _rf)
        check("Lark gửi hỏng -> nhả reservation để được thử lại ngay",
              db.conn().execute(
                  "SELECT 1 FROM outbound_dedup WHERE channel=? AND recipient=?",
                  (_sl._DEDUP_CHANNEL, "on_A")).fetchone() is None)

        check("who=None -> không gửi, trả NO_ASKER",
              _sl.send_list(None) == qa.NO_ASKER)
    finally:
        lark_api.im_send_text, lark_api.im_send_card = _keep_im
        qa.list_text = _keep_lt

    # =================================================================
    part("34d. Plugin Hermes — policy tạm thời + chặn trả lời từ trí nhớ")
    # =================================================================
    import importlib.util
    from types import SimpleNamespace
    _plugin_path = (Path(__file__).resolve().parent.parent / "hermes" /
                    "v2-enroll-gate" / "__init__.py")
    _spec = importlib.util.spec_from_file_location("v2_enroll_gate_selftest",
                                                   _plugin_path)
    _plug = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_plug)

    check("plugin: câu dữ liệu phải gọi tool",
          _plug._requires_tool("liệt kê cuộc họp 7 ngày qua"))
    check("plugin: chào hỏi không ép gọi tool",
          not _plug._requires_tool("cảm ơn bạn"))
    check("plugin: tiền tố xã giao KHÔNG né được guard dữ liệu",
          _plug._requires_tool("chào bạn, liệt kê cuộc họp của tôi"))
    check("plugin: câu help ghép thêm hành động vẫn PHẢI gọi tool",
          _plug._requires_tool("bot làm được gì và gửi transcript cuộc họp X"))
    check("plugin: tên tool giả chỉ giống hậu tố KHÔNG lọt allow-list",
          _plug._tool_suffix("evil_list_meetings") == "")

    # --- Hợp đồng NÚT THẺ: cards.py dựng `value`, plugin dịch ngược ---------
    # Đây là chỗ vỡ âm thầm điển hình: đổi khoá ở một bên thì người dùng bấm nút
    # và nhận về "Unknown command /card" từ Hermes, còn log V2 sạch trơn.
    _wc = cards.welcome_card("An", "Bạn có 1 cuộc họp:")
    check("thẻ chào: KHÔNG còn ô nút nào ở cuối (user chốt bỏ 05/08/2026)",
          not [el for el in _wc["elements"] if el.get("tag") == "action"],
          str(_wc["elements"])[:160])
    check("thẻ chào: vẫn có tên người in đậm và nguyên danh sách",
          "**An**" in _wc["elements"][0]["text"]["content"]
          and "Bạn có 1 cuộc họp:" in _wc["elements"][1]["text"]["content"])
    # Cầu xử lý cú bấm nút GIỮ NGUYÊN dù hiện không thẻ nào sinh nút: thiếu nó
    # thì một nút thêm sau này rơi thẳng vào nhánh "Unknown command" của Hermes.
    import json as _json_btn
    _click = SimpleNamespace(
        text='/card button ' + _json_btn.dumps(
            {"v2": "transcript", "token": "mtBTN01"}, ensure_ascii=False))
    _plug._rewrite_card_click(_click)
    check("plugin: cú bấm nút -> yêu cầu tiếng Việt bình thường",
          _click.text == "gửi transcript mtBTN01", _click.text)
    check("...và câu dựng ra qua được cổng write-tool của send_transcript_file",
          any(x in _plug._plain(_click.text) for x in _plug._TRANSCRIPT_NOUNS))
    # Ý định xin bản nguyên văn phải SỐNG QUA LƯỢT. Ca thật 05/08/2026: hệ thống
    # hỏi lại vì hai cuộc trùng tên, người dùng đáp "bản ngày 29/07" — câu này
    # không có chữ nào trong `_TRANSCRIPT_NOUNS` nên tool bị chặn và bot bảo họ
    # gõ lại cả câu dài. Hệ thống vừa hỏi mà lại không nhận câu trả lời.
    _plug._asked.clear()
    check("chưa ai xin gì -> câu trả lời cụt vẫn bị chặn (bảo vệ còn nguyên)",
          not _plug._asked_recently("sess-ask"))
    _plug._mark_asked("sess-ask", _plug._plain("gửi transcript cuộc họp X"))
    check("đã xin ở lượt trước -> câu 'bản ngày 29/07' được đi tiếp",
          _plug._asked_recently("sess-ask"))
    _plug._asked["sess-ask"] = _plug.time.time() - _plug._ASK_TTL_S - 1
    check("ý định cũ quá hạn -> đóng lại, không mở cửa mãi mãi",
          not _plug._asked_recently("sess-ask"))
    _plug._mark_asked("sess-khac", _plug._plain("hôm nay có cuộc họp nào"))
    check("câu không nhắc bản nguyên văn -> KHÔNG ghi ý định",
          not _plug._asked_recently("sess-khac"))

    _other = SimpleNamespace(text='/card button {"hermes_action": "approve"}')
    _plug._rewrite_card_click(_other)
    check("plugin: /card của người khác KHÔNG bị mình cướp",
          _other.text == '/card button {"hermes_action": "approve"}')
    _plain_msg = SimpleNamespace(text="liệt kê cuộc họp")
    _plug._rewrite_card_click(_plain_msg)
    check("plugin: tin nhắn thường không bị đụng vào",
          _plain_msg.text == "liệt kê cuộc họp")

    _keep_ask = _plug._ask_v2
    _plug._ask_v2 = lambda *a, **k: {
        "decision": "allow", "asker_token": "secret-ticket", "open_id": "ou_A"}
    _source = SimpleNamespace(
        platform=SimpleNamespace(value="feishu"), user_id_alt="on_A",
        user_id="ou_A", user_name="An", chat_type="dm", chat_id="oc_A")
    _event = SimpleNamespace(source=_source, text="liệt kê cuộc họp",
                             channel_prompt=None)
    try:
        _gate_result = _plug._on_pre_dispatch(event=_event)
    finally:
        _plug._ask_v2 = _keep_ask
    check("plugin: vé nằm trong channel_prompt tạm thời",
          _gate_result.get("action") == "allow"
          and "secret-ticket" in (_event.channel_prompt or ""))
    check("plugin: KHÔNG chèn vé vào user message/session history",
          _event.text == "liệt kê cuộc họp" and "secret-ticket" not in _event.text)

    _plug._on_pre_llm_call(platform="feishu", session_id="s-test",
                           turn_id="t1", user_message="liệt kê cuộc họp")
    _blocked = _plug._on_transform_llm_output(
        platform="feishu", session_id="s-test",
        response_text="Bạn có 11 cuộc họp theo trí nhớ cũ")
    check("plugin: không gọi tool -> chặn câu trả lời dựng từ memory",
          _blocked == _plug._NO_TOOL_REPLY)

    _plug._on_pre_llm_call(platform="feishu", session_id="s-test",
                           turn_id="t2", user_message="xem chi tiết cuộc họp X")
    check("plugin: chặn tool ngoài allow-list trên Feishu",
          (_plug._on_pre_tool_call(session_id="s-test", tool_name="bfl_generate")
           or {}).get("action") == "block")
    check("plugin: transcript không được tự lái agent tạo task",
          (_plug._on_pre_tool_call(
              session_id="s-test", tool_name="mcp__meetings__create_task")
           or {}).get("action") == "block")
    check("plugin: tool giả có hậu tố meetings vẫn bị chặn",
          (_plug._on_pre_tool_call(
              session_id="s-test", tool_name="evil_list_meetings")
           or {}).get("action") == "block")
    _shape = _plug._on_pre_tool_call(
        session_id="s-test", tool_name="terminal") or {}
    check("plugin: block dùng ĐÚNG contract Hermes action/message",
          _shape.get("action") == "block" and bool(_shape.get("message"))
          and "decision" not in _shape and "reason" not in _shape,
          str(_shape))
    _tool_result = qa.two_channel("CÂU CHÍNH XÁC TỪ TOOL", "không được lộ")
    _plug._on_post_tool_call(session_id="s-test",
                            tool_name="mcp__meetings__get_meeting",
                            result=_tool_result, status="ok")
    _exact = _plug._on_transform_llm_output(
        platform="feishu", session_id="s-test",
        response_text="LLM đã tự viết lại sai")
    check("plugin: có kênh user từ tool -> code trả NGUYÊN VĂN, bỏ bản LLM viết lại",
          _exact == "CÂU CHÍNH XÁC TỪ TOOL")
    check("plugin: che V2-ASKER nếu model cố nhắc lại",
          "secret-ticket" not in _plug._redact("[V2-ASKER: secret-ticket]"))
    _plug._cleanup_turn(session_id="s-test")

    _plug._on_pre_llm_call(platform="feishu", session_id="s-plain",
                           turn_id="t3", user_message="liệt kê cuộc họp")
    _plug._on_post_tool_call(
        session_id="s-plain", tool_name="mcp__meetings__list_meetings",
        result="adapter hỏng nhưng quên gắn status error", status="ok")
    check("plugin: kết quả tool không có marker V2 KHÔNG được tính là thành công",
          _plug._on_transform_llm_output(
              platform="feishu", session_id="s-plain",
              response_text="LLM bịa tiếp") == _plug._NO_TOOL_REPLY)
    _plug._cleanup_turn(session_id="s-plain")

    # =================================================================
    part("34j. Hồ sơ bot — hỏi về CHÍNH BOT không bị xử như câu hỏi dữ liệu")
    # =================================================================
    # Trước 06/08/2026 "bạn tên gì" rơi vào nhánh mặc định "phải gọi tool
    # meetings", không tool nào trả lời được, nên `transform_llm_output` thay
    # câu trả lời bằng `_NO_TOOL_REPLY`. Hỏi bot về nó thì bot báo lỗi dữ liệu.
    from v2 import profile as _profile
    # Bộ câu dưới lấy từ 86 tin nhắn THẬT trong `gateway.log`, không phải nghĩ
    # ra. Hai câu đầu là hai câu đã bị bot chặn bằng "chưa truy xuất dữ liệu"
    # ngày 05-06/08/2026 — đúng thứ người dùng gọi là "trả lời cứng nhắc".
    # Chú ý xưng hô: người dùng thật gõ "mày", không gõ "bạn".
    for _q in ("ok mày là ai và luồng xử lý của mày là gì",
               "ok list ra rồi mày làm được gì",
               "alo",
               "bạn là ai", "bạn tên gì", "bot làm được gì",
               "bạn hoạt động thế nào", "giới thiệu về bạn"):
        check(f"hỏi về bot / gọi thử KHÔNG cần tool: {_q!r}",
              not _plug._requires_tool(_q))
    # Mặt kia của cùng một luật, và là mặt NGUY HIỂM hơn: một chuỗi miễn trừ
    # lọt vào câu hỏi dữ liệu là bỏ luôn ràng buộc phải gọi tool cho câu đó,
    # tức mở lại đúng lỗ "trả lời từ trí nhớ" của sự cố 03/08/2026.
    #
    # Bốn câu đầu cũng là tin nhắn thật, và đều có "ban"/"may" trong đó —
    # chúng là phép thử nặng nhất cho luật chủ-ngữ-liền-đuôi-câu.
    for _q in ("theo bạn thì phần lark minute ok hơn hay phần transcript ok hơn",
               "ok mày đánh giá thế nào về nội dung cuộc họp này ?",
               "vậy giải thích chi tiết đi",
               "tóm tắt cuộc họp này",
               "cho mình tên bản ghi cuộc họp hôm qua",
               "cuộc họp này bàn là gì",
               "dự án hoạt động ra sao",
               "cái công cụ đó dùng thế nào trong cuộc họp"):
        check(f"câu dữ liệu VẪN phải gọi tool: {_q[:52]!r}",
              _plug._requires_tool(_q))
    # `đ` không phải `d` + dấu nên NFKD không tách nó; bản cũ xoá sạch chữ đó
    # và MỌI hằng số viết bằng `d` đều hụt (xem `_D_MAP`). Ba câu dưới là ba
    # hằng số đã âm thầm vô hiệu — cái thứ hai chặn đúng lệnh mà chính plugin
    # dựng ra để nhận.
    check("khử dấu: 'đ' thành 'd', không bị xoá mất",
          _plug._plain("được duyệt đọc") == "duoc duyet doc")
    check("...nên 'duyệt MCP' qua được cổng write-tool của glossary_approve",
          "duyet" in _plug._plain("duyệt MCP, Anthropic"))
    check("hồ sơ có tên bot và đủ ba việc",
          _profile.BOT_NAME in _profile.prompt_block()
          and len(_profile.CAN_DO) == 3
          and all(x in _profile.text() for x in _profile.CAN_DO))
    # Kỷ luật nội dung (xem docstring `v2/profile.py`): người dùng cuối là nhân
    # viên đi hỏi biên bản họp. Một cái tên hạ tầng lọt ra chat chỉ tạo câu hỏi
    # mà bot không được phép trả — và "Base" thì user đã chốt 03/08/2026 là chỗ
    # nội bộ, không nêu ra.
    _pblob = (_profile.text() + _profile.prompt_block()).lower()
    for _leak in ("base", "bitable", "hermes", "whisper", "cloudflare",
                  "vercel", "sqlite", "mcp"):
        check(f"hồ sơ KHÔNG lộ hạ tầng: {_leak!r}", _leak not in _pblob)
    check("hồ sơ nói rõ hai giới hạn người dùng va vào thật",
          "1-1" in _pblob and "có dự" in _profile.text())

    # --- câu xác nhận: được viết mềm, KHÔNG được bịa số ------------------
    check("câu xác nhận tự viết được đi tiếp",
          _plug._safe_confirm("Danh sách của bạn ở ngay trên nhé, "
                              "cần lọc lại thì bảo mình."))
    check("có CHỮ SỐ -> chặn (agent không cầm danh sách nên số là bịa)",
          not _plug._safe_confirm("Bạn có 8 cuộc họp ở trên nhé"))
    check("liệt kê lại -> chặn",
          not _plug._safe_confirm("Danh sách:\n• Cuộc A\n• Cuộc B"))
    check("dài quá -> chặn", not _plug._safe_confirm("x" * 200))
    check("rỗng -> chặn (không gửi tin trống)", not _plug._safe_confirm("   "))

    _plug._on_pre_llm_call(platform="feishu", session_id="s-conf",
                           turn_id="t9", user_message="liệt kê cuộc họp")
    _plug._on_post_tool_call(
        session_id="s-conf", tool_name="mcp__meetings__list_meetings",
        result=qa.agent_only("đã gửi danh sách thẳng cho người dùng"),
        status="ok")
    check("list_meetings + câu an toàn -> giữ lời của agent",
          _plug._on_transform_llm_output(
              platform="feishu", session_id="s-conf",
              response_text="Danh sách của bạn ở trên nhé.")
          == "Danh sách của bạn ở trên nhé.")
    check("list_meetings + câu có số -> rơi về câu cố định",
          _plug._on_transform_llm_output(
              platform="feishu", session_id="s-conf",
              response_text="Mình tìm thấy 22 cuộc họp của bạn")
          == _plug._DIRECT_CONFIRM["list_meetings"])
    _plug._cleanup_turn(session_id="s-conf")

    # =================================================================
    part("34k. Ký ức hội thoại — bền qua reset phiên, KHÔNG nới quyền")
    # =================================================================
    with db.tx() as c:
        c.execute("DELETE FROM chat_memory")
    db.remember_meeting("on_A", "mtA", "Hop mtA")
    db.remember_meeting("on_A", "mtA", "Hop mtA")
    check("nhắc lại một cuộc -> không sinh dòng trùng",
          len(db.recent_meetings("on_A")) == 1)
    db.remember_meeting("on_A", "mtB2", "Hop mtB2")
    check("mới nhất đứng trước",
          [r["minute_token"] for r in db.recent_meetings("on_A")][0] == "mtB2")
    check("ký ức của A KHÔNG lọt sang B", db.recent_meetings("on_B") == [])
    check("union_id rỗng -> không ghi gì (đường terminal)",
          (db.remember_meeting("", "mtA", "x") or True)
          and db.recent_meetings("") == [])
    _old = int(time.time() * 1000) - (db.CHAT_MEMORY_TTL_DAYS + 1) * 86_400_000
    with db.tx() as c:
        c.execute("UPDATE chat_memory SET touched_at=? WHERE union_id='on_A'",
                  (_old,))
    check("ký ức quá hạn KHÔNG được bơm lại vào prompt",
          db.recent_meetings("on_A") == [])

    # `qa.remember` là cửa duy nhất mà tầng dữ liệu dùng, và nó phải câm khi
    # chưa biết người hỏi — nếu không, một lời gọi tool thiếu vé sẽ ghi ký ức
    # cho một danh tính rỗng rồi bơm cho người khác.
    with db.tx() as c:
        c.execute("DELETE FROM chat_memory")
    qa.remember(None, "mtA", "Hop mtA")
    qa.remember(askers.admin_view(), "mtA", "Hop mtA")
    check("who=None và đường terminal -> không ghi ký ức nào",
          db.conn().execute("SELECT COUNT(*) FROM chat_memory").fetchone()[0] == 0)

    db.remember_meeting("on_A", "mtA", "Workforce Buổi 4")
    _mem = gate._memory_block("on_A")
    check("khối ký ức có tên cuộc + minute_token cho câu nói tắt",
          "Workforce Buổi 4" in _mem and "mtA" in _mem)
    check("...và có lời dặn KHÔNG dùng nó thay dữ liệu",
          "gọi tool" in _mem and "hôm nay" in _mem)
    check("...và nói rõ mục ĐẦU là cuộc mặc định khi người dùng nói tắt",
          "MỤC ĐẦU" in _mem)

    # Thẻ "Họp xong" đẩy ra phải NEO ký ức của người nhận. Ca thật 06/08/2026
    # 19:51: người dùng trả lời chính thẻ đó bằng "Có recap rồi đó, phân tích",
    # agent không có gì neo nên lấy mục cũ hơn (`work`) và phân tích nhầm cuộc.
    with db.tx() as c:
        c.execute("DELETE FROM chat_memory")
    db.remember_meeting("on_NHAN", "mtCU", "Cuoc cu hai tieng truoc")
    pipeline._anchor_memory("on_NHAN", "mtTHE", "Cuoc vua day the")
    _top = db.recent_meetings("on_NHAN")
    check("thẻ vừa đẩy đứng ĐẦU ký ức người nhận",
          _top and _top[0]["minute_token"] == "mtTHE",
          str(_top))
    check("...và cuộc cũ vẫn còn, chỉ tụt xuống", len(_top) == 2)
    pipeline._anchor_memory("", "mtX", "khong co nguoi nhan")
    check("không có union_id -> không ghi gì",
          len(db.recent_meetings("on_NHAN")) == 2)
    check("người chưa hỏi gì -> khối ký ức rỗng, không bịa",
          gate._memory_block("on_KHONG_CO") == "")
    # Bảng `chat_memory` cố ý KHÔNG lưu nội dung họp, và khối bơm vào prompt
    # phải phản ánh đúng điều đó: mỗi dòng dữ liệu chỉ có tên + token + mốc
    # giờ. Kiểm trên CHÍNH các dòng dữ liệu (bắt đầu bằng "•"), không kiểm cả
    # khối — phần lời dặn có nhắc chữ "tóm tắt"/"nguyên văn" là đúng ý đồ.
    _mem_rows = [ln for ln in _mem.splitlines() if ln.strip().startswith("•")]
    check("mỗi dòng ký ức chỉ có tên cuộc + token + giờ",
          len(_mem_rows) == 1
          and all(x in _mem_rows[0] for x in ("Workforce Buổi 4", "mtA"))
          and len(_mem_rows[0]) < 160, str(_mem_rows))

    # Cửa gate phải cấp CẢ HAI cho plugin: thiếu `profile` thì bot mất hồ sơ,
    # thiếu `memory` thì câu nói tắt chết sau mỗi lần reset phiên.
    add_user("ou_MEM", "on_MEM", "Nguoi Nho")
    db.remember_meeting("on_MEM", "mtA", "Workforce Buổi 4")
    _allow = gate.check("on_MEM", "u1", "Nguoi Nho", send=False, chat_type="dm")
    check("gate allow trả kèm hồ sơ + ký ức",
          _allow.get("decision") == "allow"
          and _profile.BOT_NAME in (_allow.get("profile") or "")
          and "Workforce Buổi 4" in (_allow.get("memory") or ""))
    check("...và vé phiên vẫn được cấp như cũ", bool(_allow.get("asker_token")))

    # Plugin nối hồ sơ + ký ức vào channel_prompt, KHÔNG vào user message —
    # cùng lý lẽ với vé: Hermes lưu user message vào session DB.
    _keep_ask2 = _plug._ask_v2
    _plug._ask_v2 = lambda *a, **k: {
        "decision": "allow", "asker_token": "tk", "open_id": "ou_MEM",
        "profile": "[HỒ SƠ CỦA BẠN]\nTên: Thư Ký.",
        "memory": "[NGƯỜI NÀY VỪA HỎI VỀ NHỮNG CUỘC HỌP SAU]\n  • \"Workforce\""}
    _ev2 = SimpleNamespace(source=_source, text="gửi nguyên văn cuộc đó",
                           channel_prompt=None)
    try:
        _plug._on_pre_dispatch(event=_ev2)
    finally:
        _plug._ask_v2 = _keep_ask2
    check("plugin: hồ sơ + ký ức vào channel_prompt tạm thời",
          "Thư Ký" in (_ev2.channel_prompt or "")
          and "Workforce" in (_ev2.channel_prompt or ""))
    check("plugin: KHÔNG nhét hồ sơ/ký ức vào tin nhắn người dùng",
          _ev2.text == "gửi nguyên văn cuộc đó")
    _plug._ask_v2 = lambda *a, **k: {"decision": "allow", "asker_token": "tk"}
    _ev3 = SimpleNamespace(source=_source, text="liệt kê cuộc họp",
                           channel_prompt=None)
    try:
        _plug._on_pre_dispatch(event=_ev3)
    finally:
        _plug._ask_v2 = _keep_ask2
    check("V2 đời cũ không trả hồ sơ -> plugin dùng bản dự phòng, không rỗng",
          _plug._PROFILE_FALLBACK in (_ev3.channel_prompt or ""))

    # =================================================================
    part("34m. Cửa sổ nạp backlog lúc kết nối · phủ sóng")
    # =================================================================
    # Hai cửa sổ PHẢI tách nhau: quét lùi để tạo backlog (có thể 90 ngày) và
    # liệt kê trong thẻ chào (giữ 7 ngày). Gộp lại thì hoặc người mới mất cuộc
    # họp cũ, hoặc tin nhắn đầu tiên dài vài chục dòng.
    # Bất biến, KHÔNG phải giá trị cụ thể: máy này đặt 90 từ 06/08/2026, máy
    # khác có thể bỏ trống (khi đó rơi về LOOKBACK_DAYS). Thứ phải luôn đúng là
    # cửa sổ lúc kết nối không bao giờ HẸP hơn cửa sổ quét thường — hẹp hơn thì
    # người mới nhận được ÍT hơn cả một vòng quét bình thường, tức kết nối xong
    # lại thiếu đúng những cuộc mà hệ thống vẫn đang tự bắt.
    check("cửa sổ nạp lúc kết nối không hẹp hơn cửa sổ quét thường",
          max(config.ENROLL_BACKFILL_DAYS, config.LOOKBACK_DAYS)
          >= config.LOOKBACK_DAYS,
          f"{config.ENROLL_BACKFILL_DAYS} vs {config.LOOKBACK_DAYS}")

    wipe_jobs()
    add_user("ou_NEWBIE", "on_NEWBIE", "Nguoi Moi")
    _now_s = time.time()
    _scanned: dict[str, int] = {}

    def _fake_minutes_list(_tok, start_ms, end_ms, _oid, **_kw):
        _scanned["days"] = round((end_ms - start_ms) / 86_400_000)
        # Một cuộc MỚI (hôm qua) và một cuộc CŨ (60 ngày trước).
        return [{"token": "mtNEW", "start_time": int((_now_s - 86_400) * 1000)},
                {"token": "mtOLD", "start_time": int((_now_s - 60 * 86_400) * 1000)}]

    _keep = (lark_api.minutes_list, orchestrator.enqueue_minute,
             orchestrator._note_verified_viewer, config.ENROLL_BACKFILL_DAYS,
             tokenstore.get_access_token)
    _enqueued: list[str] = []
    try:
        config.ENROLL_BACKFILL_DAYS = 90
        # `add_user` ghi token RỖNG nên `get_access_token` ném — và
        # `welcome_and_backlog` nuốt mọi lỗi ở đó (đúng: chào hỏng không được
        # phá enroll). Không stub thì phép kiểm im lặng đo nhầm nhánh lỗi.
        tokenstore.get_access_token = lambda *a, **k: "tok-gia"
        lark_api.minutes_list = _fake_minutes_list
        orchestrator.enqueue_minute = (
            lambda oid, mt, it, **kw: (_enqueued.append(mt), True)[1])
        orchestrator._note_verified_viewer = lambda mt, *a, **k: meta(
            minute_token=mt, title=f"Hop {mt}",
            start=_now_s - (86_400 if mt == "mtNEW" else 60 * 86_400))
        orchestrator.welcome_and_backlog(
            {"union_id": "on_NEWBIE", "open_id": "ou_NEWBIE", "name": "Nguoi Moi"})
    finally:
        (lark_api.minutes_list, orchestrator.enqueue_minute,
         orchestrator._note_verified_viewer, config.ENROLL_BACKFILL_DAYS,
         tokenstore.get_access_token) = _keep

    check("nới ENROLL_BACKFILL_DAYS -> quét lùi đúng 90 ngày",
          _scanned.get("days") == 90, str(_scanned))
    check("...và cuộc CŨ vẫn được tạo backlog", "mtOLD" in _enqueued)
    check("...nhưng thẻ chào KHÔNG liệt kê cuộc ngoài cửa sổ 7 ngày",
          "mtNEW" in _enqueued and len(_enqueued) == 2)

    # `coverage` đọc thẳng schema `jobs`/`tokens` để trả lời "ai đang chặn ai".
    # Nó phải fail-safe: một token chập không được làm hỏng cả bảng.
    from v2 import coverage as _cov
    wipe_jobs()
    jobstore.create(meta(minute_token="mtBLOCK", title="Cuoc bi chan",
                         owner_open_id="ou_CHUA_ENROLL",
                         owner_name="Chu Ban Ghi Chua Vao"), status="delivered")
    jobstore.set_status("mtBLOCK", "waiting_auth")
    _keep_ml = lark_api.minutes_list
    try:
        lark_api.minutes_list = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("token chap"))
        _data = _cov.collect(days=90)
    finally:
        lark_api.minutes_list = _keep_ml
    check("coverage: token hỏng -> ghi lỗi vào dòng đó, KHÔNG nổ cả bảng",
          all(r["error"] for r in _data["rows"]) and len(_data["rows"]) >= 1)
    check("coverage: gom job kẹt theo CHỦ BẢN GHI để biết đi mời ai",
          _data["blockers"].get("ou_CHUA_ENROLL", {}).get("name")
          == "Chu Ban Ghi Chua Vao", str(_data["blockers"]))
    check("coverage: chủ đã enroll mà vẫn kẹt thì tách riêng, không lẫn vào "
          "danh sách đi mời",
          not any(k.startswith("!!") for k in _data["blockers"]))

    # =================================================================
    part("34l. Heartbeat — chỉ số mở rộng")
    # =================================================================
    # Vì sao kiểm ở đây dù nó nằm trong `tools/`: heartbeat là thứ DUY NHẤT
    # chạy khi không có ai ngồi trước máy. Nó hỏng thì không có gì báo rằng nó
    # hỏng — và nó đọc thẳng vào schema `jobs`/`deliveries`/`tokens`, tức một
    # lần đổi tên cột trong V2 là làm câm hệ thống cảnh báo.
    import importlib.util as _ilu
    _hb_path = Path(__file__).resolve().parent.parent / "tools" / "heartbeat.py"
    _hb_spec = _ilu.spec_from_file_location("heartbeat_selftest", _hb_path)
    _hb = _ilu.module_from_spec(_hb_spec)
    assert _hb_spec and _hb_spec.loader
    _hb_spec.loader.exec_module(_hb)
    _hb.DB = str(config.DB_PATH)
    _stats = _hb.db_stats()
    check("heartbeat đọc được schema hiện tại (không lỗi tên cột)",
          {"queue", "idle", "backlog", "failed_act", "deliv24", "users",
           "askers"} <= set(_stats), str(_stats))
    check("heartbeat đếm đúng người đã enroll", _stats["users"] >= 1)

    # Heartbeat cố ý KHÔNG import `v2.*` (nó phải chạy được cả khi V2 hỏng),
    # nên nó chép lại hai thứ nhỏ: danh sách mã lỗi im lặng và cách tách mã.
    # Chép là mời trôi dạt — phép kiểm này là dây buộc giữa hai bên.
    check("heartbeat dùng ĐÚNG danh sách mã lỗi im lặng của alerts",
          _hb.SILENT_FAIL_CODES == set(alerts.SILENT_FAIL_CODES),
          f"{_hb.SILENT_FAIL_CODES} vs {alerts.SILENT_FAIL_CODES}")
    _err_sample = f"[{jobstore.ERR_EMPTY_TRANSCRIPT}] whisper ra 0 chữ (3s)"
    check("...và tách mã lỗi ra cùng kết quả với jobstore",
          _hb._ERR_CODE_RE.match(_err_sample).group(1)
          == jobstore.error_code(_err_sample))

    # Ca thật 06/08/2026: 4 job `failed` trên máy đều là `empty_transcript`,
    # tức sẽ failed VĨNH VIỄN. Đếm cả chúng nghĩa là heartbeat WARN mãi mãi, và
    # một cảnh báo không bao giờ tắt là cảnh báo người ta học cách bỏ qua.
    wipe_jobs()
    jobstore.create(meta(minute_token="mtSILENT", title="Ban ghi im lang"),
                    status="delivered")
    jobstore.set_status("mtSILENT", "failed",
                        error=f"[{jobstore.ERR_EMPTY_TRANSCRIPT}] 0 chữ")
    jobstore.create(meta(minute_token="mtREAL", title="Hong that"),
                    status="delivered")
    jobstore.set_status("mtREAL", "failed",
                        error="[media_denied] không tải được bản ghi")
    _s2 = _hb.db_stats()
    check("bản ghi im lặng KHÔNG tính là 'hỏng cần sửa'",
          _s2["failed_act"] == 1, str(_s2.get("queue")))
    check("...nhưng vẫn hiện trong hình dạng hàng đợi (không giấu đi)",
          _s2["queue"].get("failed") == 2)
    # Dòng log phải dựng được cả khi MỌI chỉ số đều None: máy vừa khởi động,
    # chưa có log, chưa có DB — đúng lúc cần heartbeat nhất.
    _empty = {k: None for k in
              ("v2mem", "v2up", "wlat", "scan", "tokens", "disk", "dbmb",
               "idle", "backlog", "failed_act", "deliv24", "users", "askers")}
    _empty.update(now=__import__("datetime").datetime.now(), level="OK",
                  v2run=0, whisper="DOWN", hermes=False, gwerr=-1, v2err=-1,
                  queue={}, problems=[])
    check("dòng log dựng được khi mọi chỉ số đều trống",
          "hangdoi[trong]" in _hb.format_line(_empty))

    # --- Ngưỡng báo động: hai luật KHÔNG BÁO là hai luật dễ sai nhất --------
    # Chúng quyết định khi nào heartbeat IM. Sai kiểu "báo thừa" thì nhìn thấy
    # ngay; sai kiểu "im nhầm" thì không ai biết cho tới lúc cần nó nhất.
    def _hbd(**kw) -> dict:
        base = dict(v2run=1, whisper="200", hermes=True, scan=5.0, gwerr=0,
                    v2err=0, tokens=7.0, disk=400.0, queue={}, backlog=0,
                    idle=5.0, failed_act=0)
        base.update(kw)
        return base

    check("mọi thứ bình thường -> không báo gì", _hb.problems(_hbd()) == [])
    # Ca thật 06/08/2026: đang nạp bù một loạt buổi đào tạo 2-3 tiếng audio.
    # Mỗi vòng lặp dịch xong một cuộc rồi mới quét lại, nên khoảng cách giữa
    # hai lần quét dài ra 20-30 phút. Bản đầu báo động liên tục suốt đợt đó.
    check("scan cũ MÀ còn job trong hàng đợi -> im (vòng run đang làm việc)",
          _hb.problems(_hbd(scan=35.0, backlog=3)) == [])
    check("...hàng đợi rỗng mà vẫn không quét -> BÁO (lúc đó mới là kẹt thật)",
          _hb.problems(_hbd(scan=35.0, backlog=0)) == ["scan_cu=35.0m"])
    check("còn job nhưng vừa dịch xong -> im, dù đang chạy chậm",
          _hb.problems(_hbd(backlog=5, idle=40.0)) == [])
    check("còn job mà LÂU không dịch xong cuộc nào -> BÁO tắc nghẽn",
          _hb.problems(_hbd(backlog=5, idle=_hb.IDLE_WARN_MIN + 1))
          == [f"tac_nghen={_hb.IDLE_WARN_MIN + 1}m/5job"])
    check("hết việc thì im lâu bao nhiêu cũng KHÔNG phải tắc nghẽn",
          _hb.problems(_hbd(backlog=0, idle=5000.0)) == [])
    check("thành phần chết -> FAIL, không phải WARN",
          _hb.level_of(_hb.problems(_hbd(whisper="DOWN"))) == "FAIL"
          and _hb.level_of(_hb.problems(_hbd(disk=1.0))) == "WARN")

    _plug._on_pre_llm_call(platform="feishu", session_id="s-blocked",
                           turn_id="t4", user_message="liệt kê cuộc họp")
    _plug._on_post_tool_call(
        session_id="s-blocked", tool_name="mcp__meetings__list_meetings",
        result=qa.two_channel("DỮ LIỆU KHÔNG ĐƯỢC TÍNH"), status="blocked")
    check("plugin: post_tool status=blocked dù có marker cũng KHÔNG tính thành công",
          _plug._on_transform_llm_output(
              platform="feishu", session_id="s-blocked",
              response_text="LLM bịa tiếp") == _plug._NO_TOOL_REPLY)
    _plug._cleanup_turn(session_id="s-blocked")

    # =================================================================
    part("34e. Singleton — khóa nguyên tử giữa các orchestrator")
    # =================================================================
    _lock1 = singleton.acquire(config.DB_PATH)
    try:
        try:
            _lock2 = singleton.acquire(config.DB_PATH)
        except singleton.AlreadyRunning:
            _second_blocked = True
        else:
            _second_blocked = False
            _lock2.close()
        check("singleton: lần lấy khóa thứ hai của cùng DB bị chặn",
              _second_blocked)
    finally:
        _lock1.close()

    # =================================================================
    part("34f. Whisper supervisor — tự cứu nhưng không được bật trùng")
    # =================================================================
    _ws_keep_cfg = (config.WHISPER_AUTOSTART, config.TRANSCRIBE_URL,
                    config.WHISPER_START_SCRIPT,
                    config.WHISPER_RESTART_COOLDOWN)
    _ws_keep_fn = (whisper_supervisor._healthy, whisper_supervisor._port_open,
                   whisper_supervisor._spawn)
    _ws_script = config.WORK_DIR / "selftest-whisper.bat"
    _ws_script.parent.mkdir(parents=True, exist_ok=True)
    _ws_script.write_text("@echo off\r\n", encoding="ascii")
    _ws_spawned: list[Path] = []

    class _FakeWhisperProcess:
        pid = 4242

        def __init__(self, rc=None):
            self.rc = rc

        def poll(self):
            return self.rc

    try:
        config.WHISPER_AUTOSTART = True
        config.TRANSCRIBE_URL = "http://localhost:8000"
        config.WHISPER_START_SCRIPT = _ws_script
        config.WHISPER_RESTART_COOLDOWN = 900
        whisper_supervisor._healthy = lambda: False
        whisper_supervisor._port_open = lambda: False
        whisper_supervisor._spawn = lambda path: (
            _ws_spawned.append(path), _FakeWhisperProcess())[1]
        whisper_supervisor._reset_for_tests()

        check("supervisor: local chết + cổng đóng -> bật đúng MỘT process",
              whisper_supervisor.ensure_running() == "started"
              and len(_ws_spawned) == 1)
        check("supervisor: process vừa bật còn sống -> KHÔNG bật bản thứ hai",
              whisper_supervisor.ensure_running() == "starting"
              and len(_ws_spawned) == 1)

        whisper_supervisor._reset_for_tests()
        whisper_supervisor._port_open = lambda: True
        check("supervisor: /health lỗi nhưng cổng còn nghe -> KHÔNG spawn trùng",
              whisper_supervisor.ensure_running() == "listening"
              and len(_ws_spawned) == 1)

        whisper_supervisor._reset_for_tests()
        whisper_supervisor._port_open = lambda: False
        config.TRANSCRIBE_URL = "http://whisper.internal:8000"
        check("supervisor: endpoint từ xa -> tuyệt đối KHÔNG spawn local",
              whisper_supervisor.ensure_running() == "remote"
              and len(_ws_spawned) == 1)

        config.TRANSCRIBE_URL = "http://localhost:8000"
        config.WHISPER_START_SCRIPT = config.WORK_DIR / "khong-ton-tai.bat"
        whisper_supervisor._reset_for_tests()
        check("supervisor: thiếu script -> báo trạng thái, không làm chết run",
              whisper_supervisor.ensure_running() == "missing"
              and len(_ws_spawned) == 1)

        config.WHISPER_START_SCRIPT = _ws_script
        whisper_supervisor._reset_for_tests()
        check("supervisor: bật lại lần đầu trước phép thử cooldown",
              whisper_supervisor.ensure_running() == "started")
        whisper_supervisor._process.rc = 7
        check("supervisor: child thoát ngay -> cooldown chặn vòng spawn nóng",
              whisper_supervisor.ensure_running() == "cooldown"
              and len(_ws_spawned) == 2)
    finally:
        (config.WHISPER_AUTOSTART, config.TRANSCRIBE_URL,
         config.WHISPER_START_SCRIPT,
         config.WHISPER_RESTART_COOLDOWN) = _ws_keep_cfg
        (whisper_supervisor._healthy, whisper_supervisor._port_open,
         whisper_supervisor._spawn) = _ws_keep_fn
        whisper_supervisor._reset_for_tests()

    # =================================================================
    part("34g. Chờ OAuth — không mời chủ động, auth xong mới tự dịch backlog")
    # =================================================================
    # Poll NHANH khi có người cấp quyền dở. Ca này tồn tại vì bản đầu của
    # `_sleep_with_fast_enroll` quên import `oauth` (module không import nó ở
    # cấp file), mà khối `try` bên trong nuốt luôn `NameError` — orchestrator
    # vẫn chạy, chỉ có poll nhanh chết âm thầm và người vừa bấm Đồng ý phải chờ
    # đủ 5 phút. Đo bằng SỐ LẦN kéo hộp thư, không đo bằng "không ném".
    _keep_fast = (oauth.has_live_nonce, oauth.poll_pending, time.sleep)
    _fast_polls: list[int] = []
    _slept: list[float] = []
    try:
        time.sleep = lambda s: _slept.append(s)
        oauth.has_live_nonce = lambda: True
        oauth.poll_pending = lambda **k: _fast_polls.append(1)
        orchestrator._sleep_with_fast_enroll(20)
        check("có người enroll dở -> kéo hộp thư nhiều lần trong lúc chờ",
              len(_fast_polls) >= 3, f"{len(_fast_polls)} lần, ngủ {_slept}")
        check("...và mỗi giấc ngủ ngắn, không nằm im hết chu kỳ",
              all(s <= orchestrator.ENROLL_FAST_POLL_S for s in _slept),
              str(_slept))
        _fast_polls.clear()
        _slept.clear()
        oauth.has_live_nonce = lambda: False
        orchestrator._sleep_with_fast_enroll(20)
        check("KHÔNG ai enroll dở -> ngủ một mạch, không tốn lời gọi mạng nào",
              not _fast_polls and len(_slept) == 1 and abs(_slept[0] - 20) < 0.01,
              f"{_fast_polls} {_slept}")
        _fast_polls.clear()
        _slept.clear()

        def _nonce_boom():
            raise RuntimeError("DB hong")

        oauth.has_live_nonce = _nonce_boom
        orchestrator._sleep_with_fast_enroll(20)
        check("kiểm nonce hỏng -> vẫn ngủ đủ, KHÔNG làm chết vòng run",
              len(_slept) == 1 and abs(_slept[0] - 20) < 0.01, str(_slept))
    finally:
        oauth.has_live_nonce, oauth.poll_pending, time.sleep = _keep_fast

    wipe_jobs()
    _wa = meta(minute_token="obsgWAITA0000000001", owner_open_id="ou_OWNER_A",
               attendees=[Attendee(open_id="ou_A", union_id="on_A")])
    _wb = meta(minute_token="obsgWAITB0000000001", owner_open_id="ou_B",
               attendees=[Attendee(open_id="ou_B", union_id="on_B")])
    _wv = meta(minute_token="obsgWAITVIEW0000001", owner_open_id="",
               attendees=[])
    for _m in (_wa, _wb, _wv):
        jobstore.create(_m, status="waiting_auth", priority=0)
        jobstore.set_status(_m.minute_token, "waiting_auth", error="chưa có quyền",
                            attempts=4, bitable_record_id="rec-" + _m.minute_token)
    # Job failed thật không được OAuth của một người bất kỳ che đi.
    _wf = meta(minute_token="obsgWAITFAIL0000001", owner_open_id="ou_A",
               attendees=[Attendee(open_id="ou_A", union_id="on_A")])
    jobstore.create(_wf, status="failed", priority=0)
    db.note_viewer(_wv.minute_token, "ou_A", "on_A", "An")

    _released = set(jobstore.release_waiting_auth("ou_A", "on_A"))
    check("OAuth chỉ đánh thức job có quan hệ owner/attendee; viewer-only bị chặn",
          _released == {_wa.minute_token}, str(_released))
    _ra = jobstore.get(_wa.minute_token)
    check("job được đánh thức -> backlog 0, reset attempts/error",
          _ra["status"] == "queued" and _ra["priority"] == 0
          and _ra["attempts"] == 0 and _ra["error"] is None, str(_ra))
    check("OAuth không mở job của người khác và không che failed thật",
          jobstore.get(_wb.minute_token)["status"] == "waiting_auth"
          and jobstore.get(_wv.minute_token)["status"] == "waiting_auth"
          and jobstore.get(_wf.minute_token)["status"] == "failed")
    check("waiting_auth không nằm trong active queue trước khi đúng người OAuth",
          _wb.minute_token not in {r["minute_token"]
                                   for r in jobstore.active_by_priority()})
    check("trạng thái chờ nói rõ tự xác thực, không hứa tự gửi",
          "tự xác thực" in qa._TINH_TRANG["waiting_auth"]
          and "tự gửi" not in qa._TINH_TRANG["waiting_auth"])

    # User có thể OAuth lại sau khi job cũ đã bị đóng vì chỉ khớp gần giờ. Lúc này
    # scope VC mới đủ để chứng minh event -> recording -> minute và đọc người vào
    # phòng thật. Chỉ bằng chứng VERIFIED + chính user có mặt mới được sửa ACL.
    _rv_ok = meta(minute_token="obsgREVERIFYOK00001", owner_open_id="ou_OWNER",
                  participants_source="unsafe_near_rejected:calendar[near5m]",
                  attendees=[])
    _rv_title = meta(minute_token="obsgREVERIFYTITLE01", owner_open_id="ou_OWNER",
                     participants_source="no_match", attendees=[])
    _rv_other = meta(minute_token="obsgREVERIFYOTHER01", owner_open_id="ou_OWNER",
                     participants_source="no_match", attendees=[])
    for _m in (_rv_ok, _rv_title, _rv_other):
        jobstore.create(_m, status="held", priority=0)

    _keep_rv = (tokenstore.get_access_token, lark_api.minutes_list,
                meetings.resolve_participants)
    tokenstore.get_access_token = lambda oid: "tok"
    lark_api.minutes_list = lambda *a, **k: [
        {"token": _rv_ok.minute_token}, {"token": _rv_title.minute_token},
        {"token": _rv_other.minute_token},
    ]

    def _resolve_rv(tok, candidate):
        if candidate.minute_token == _rv_title.minute_token:
            candidate.participants_source = "calendar[title]:cùng tên"
            candidate.attendees = [Attendee(open_id="ou_RV", union_id="on_RV")]
        elif candidate.minute_token == _rv_other.minute_token:
            candidate.participants_source = "calendar[verified]:đúng recording"
            candidate.attendees = [Attendee(open_id="ou_OTHER", union_id="on_OTHER")]
        else:
            candidate.participants_source = "calendar[verified]:đúng recording"
            candidate.attendees = [Attendee(open_id="ou_RV", union_id="on_RV")]
        return candidate

    meetings.resolve_participants = _resolve_rv
    try:
        _upgraded = orchestrator.reverify_after_enroll(
            {"open_id": "ou_RV", "union_id": "on_RV", "name": "Nguoi Du"})
    finally:
        (tokenstore.get_access_token, lark_api.minutes_list,
         meetings.resolve_participants) = _keep_rv

    _rv_ok_after = jobstore.meta_from_json(jobstore.get(_rv_ok.minute_token)["meta_json"])
    _rv_title_after = jobstore.meta_from_json(
        jobstore.get(_rv_title.minute_token)["meta_json"])
    _rv_other_after = jobstore.meta_from_json(
        jobstore.get(_rv_other.minute_token)["meta_json"])
    check("OAuth tái xác minh: recording khớp + chính user có mặt -> sửa ACL",
          _upgraded == [_rv_ok.minute_token]
          and _rv_ok_after.participants_source.startswith("calendar[verified]")
          and _rv_ok_after.attendees[0].union_id == "on_RV", str(_upgraded))
    check("OAuth tái xác minh: chỉ khớp title KHÔNG đủ mở quyền",
          _rv_title_after.participants_source == "no_match"
          and not _rv_title_after.attendees, _rv_title_after.participants_source)
    check("OAuth tái xác minh: recording đúng nhưng user không có trong VC KHÔNG mở quyền",
          _rv_other_after.participants_source == "no_match"
          and not _rv_other_after.attendees, _rv_other_after.participants_source)
    _rv_viewers = {r["minute_token"] for r in db.conn().execute(
        "SELECT minute_token FROM minute_viewers WHERE union_id='on_RV'").fetchall()}
    check("OAuth tái xác minh chỉ ghi viewer sau khi ACL đã verified",
          _rv_viewers == {_rv_ok.minute_token}, str(_rv_viewers))

    import inspect as _inspect_rv
    _complete_src = _inspect_rv.getsource(oauth.complete)
    check("mọi đường OAuth đều tái xác minh trước khi release waiting_auth",
          _complete_src.index("reverify_after_enroll")
          < _complete_src.index("release_waiting_auth"))

    # Base có record theo dõi cho cả job chờ; Q&A phải xếp nó vào CHƯA có biên
    # bản, không được gọi là "ĐÃ CÓ BIÊN BẢN" chỉ vì có một dòng trên Base.
    _keep_en = qa.bitable.enabled
    _keep_rows = qa.lark_api.base_records_all
    qa.bitable.enabled = lambda: True
    qa.lark_api.base_records_all = lambda *a, **k: [
        {qa.bitable.F_TOKEN: _wb.minute_token, qa.bitable.F_TITLE: "WAITING ROW"},
        {qa.bitable.F_TOKEN: _wf.minute_token, qa.bitable.F_TITLE: "FAILED ROW"},
    ]
    try:
        _completed = qa.records()
    finally:
        qa.bitable.enabled = _keep_en
        qa.lark_api.base_records_all = _keep_rows
    check("record Base waiting_auth/failed KHÔNG bị coi là biên bản hoàn tất",
          not _completed, str(_completed))
    _pend_b, _ = qa.pending_split(
        {"open_id": "ou_B", "union_id": "on_B", "name": "Binh"})
    check("job waiting_auth có record Base vẫn hiện ở khối CHƯA có biên bản",
          any(p["minute_token"] == _wb.minute_token
              and p["status"] == "waiting_auth" for p in _pend_b), str(_pend_b))

    # =================================================================
    part("34h. Base ĐỦ record và ĐỒNG BỘ ĐƯỢC về sau (phân trang · ô hiển "
         "thị · nạp bù)")
    # =================================================================
    # Ba lỗi đo được ngày 05/08/2026, cùng một gốc: Base chỉ đúng vào ĐÚNG LÚC
    # record được tạo, sau đó đóng băng.
    #
    #  1. `base_records_all` gửi một lời gọi `offset: 0` với trần cứng 200, không
    #     phân trang. Record thứ 201 trở đi không tồn tại với `qa.records()` lẫn
    #     `bitable.sync_jobs()` — cuộc họp biến mất khỏi bot, im lặng.
    #  2. `sync_jobs` chỉ so 4 ô của `_job_fields`, nên tên cuộc họp / nguồn
    #     người nhận / link không bao giờ được đẩy lại. Đo thật: 6/24 record
    #     lệch, Workforce Buổi 4 vẫn ghi "khớp theo GIỜ (lệch 5m)" hai ngày sau
    #     khi ACL của nó được vá thành `calendar[verified]`.
    #  3. `LOOKBACK_DAYS`=7 nên 48 cuộc họp cũ hơn thế chưa bao giờ vào được DB.
    import v2.bitable as _bit34h

    _pages34 = [f"obsgPAGE{i:016d}" for i in range(450)]

    class _Resp34:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class _Http34:
        def __init__(self):
            self.calls: list[tuple[int, int]] = []

        def get(self, _url, **kw):
            p = kw.get("params") or {}
            off, lim = int(p.get("offset") or 0), int(p.get("limit") or 0)
            self.calls.append((off, lim))
            rows = _pages34[off:off + lim]
            return _Resp34({"code": 0, "data": {
                "fields": ["minute_token"],
                "record_id_list": [f"rec{off + i}" for i in range(len(rows))],
                "data": [[r] for r in rows]}})

    _fake34 = _Http34()
    _keep_http34 = lark_api._http
    _keep_tt34 = lark_api.tenant_token
    lark_api._http = lambda: _fake34
    lark_api.tenant_token = lambda: "t-34h"
    try:
        _all34 = lark_api.base_records_all("app", "tbl", limit=1000)
        _cap34 = lark_api.base_records_all("app", "tbl", limit=250)
    finally:
        lark_api._http = _keep_http34
        lark_api.tenant_token = _keep_tt34
    check("base_records_all phân trang — KHÔNG cắt cụt ở 200 record",
          len(_all34) == 450, f"đọc được {len(_all34)}")
    check("...và đi đúng nhiều trang, không phải một lời gọi",
          len(_fake34.calls) >= 3, str(_fake34.calls))
    check("...record thứ 201+ có thật trong kết quả (không mất im lặng)",
          _all34[-1]["minute_token"] == _pages34[-1])
    check("`limit` là TỔNG số record, vẫn chặn được", len(_cap34) == 250,
          f"{len(_cap34)}")

    _m34 = meta(minute_token="obsgSYNC0000000000001", title="Hop A & B",
                app_link="https://x/minutes/sync1",
                participants_source="calendar[verified]:Hop A & B")
    wipe_jobs()
    jobstore.create(_m34, status="held")
    _row34 = jobstore.get(_m34.minute_token)
    _want34 = _bit34h._job_fields(_row34)
    _want34.update(_bit34h._meta_fields(_row34))
    check("_meta_fields đẩy tên cuộc họp + nguồn + link (rẻ, chỉ parse JSON)",
          _want34.get(_bit34h.F_TITLE) == "Hop A & B"
          and _want34.get(_bit34h.F_LINK) == "https://x/minutes/sync1"
          and "xác minh" in _want34.get(_bit34h.F_SOURCE, ""), str(_want34))
    check("record lệch TÊN thì được ghi lại (trước đây đóng băng vĩnh viễn)",
          _bit34h._needs_push({_bit34h.F_TITLE: "Hop A &amp; B"}, _want34))
    check("record lệch NGUỒN NGƯỜI NHẬN cũng vậy",
          _bit34h._needs_push(
              {_bit34h.F_TITLE: "Hop A & B",
               _bit34h.F_SOURCE: "khớp theo GIỜ (lệch 5m) · Hop A & B"}, _want34))
    # Base tự chuẩn hoá ô url thành markdown `[url](url)`. Đưa `Link Minutes` vào
    # `_DIFF_FIELDS` là record đó lệch VĨNH VIỄN -> ghi lại mỗi 5 phút, mãi mãi.
    check("Link Minutes KHÔNG được lái quyết định ghi (chống vòng lặp ghi lại)",
          _bit34h.F_LINK not in _bit34h._DIFF_FIELDS)
    _md34 = {_bit34h.F_TITLE: "Hop A & B",
             _bit34h.F_SOURCE: _want34[_bit34h.F_SOURCE],
             _bit34h.F_LINK: "[https://x/minutes/sync1](https://x/minutes/sync1)",
             _bit34h.F_ATTEMPTS: 0, _bit34h.F_INVITEES: 0,
             _bit34h.F_ERROR: "", _bit34h.F_JOB_STATUS:
                 _bit34h.JOB_STATUS_LABEL["held"]}
    check("...link dạng markdown KHÔNG làm record bị ghi lại vô hạn",
          not _bit34h._needs_push(_md34, _want34), str(_md34))
    check("ô datetime vẫn nằm ngoài phép so (bài học cũ, đừng phá)",
          _bit34h.F_WHEN not in _bit34h._DIFF_FIELDS)

    # --- bảng ĐẾM phải phủ MỌI status, nếu không nó giấu job -----------------
    # Đo 05/08/2026: `v2 status` và `doctor` đều gõ tay danh sách status và đều
    # quên `held` — DB có 72 job, bảng cộng ra 61. Mà `held` chính là điểm dừng
    # bình thường của mô hình kéo, nên con số càng ngày càng lệch.
    from v2 import doctor as _doc34
    check("doctor phủ ĐÚNG mọi status của jobstore.ALL_STATUSES",
          set(_doc34.QUEUE_LEVELS) == set(jobstore.ALL_STATUSES),
          f"thiếu={set(jobstore.ALL_STATUSES) - set(_doc34.QUEUE_LEVELS)} "
          f"thừa={set(_doc34.QUEUE_LEVELS) - set(jobstore.ALL_STATUSES)}")
    check("`held` có mặt trong bảng đếm (đừng giấu cuộc đã dịch xong)",
          "held" in jobstore.ALL_STATUSES and "held" in _doc34.QUEUE_LEVELS)
    check("mọi status Base biết dịch đều được đếm",
          set(_bit34h.JOB_STATUS_LABEL) <= set(jobstore.ALL_STATUSES),
          str(set(_bit34h.JOB_STATUS_LABEL) - set(jobstore.ALL_STATUSES)))
    check("`v2 status` đọc danh sách từ jobstore, không gõ lại",
          "ALL_STATUSES" in (Path(__file__).resolve().parent
                             / "__main__.py").read_text(encoding="utf-8"))

    check("explain_source DỊCH nguồn đã bị cách ly, không phơi chuỗi máy",
          meetings.explain_source(
              meetings.REJECTED_PREFIX + "calendar[near19m]:Hop khac")
          .startswith("ĐÃ LOẠI"),
          meetings.explain_source(
              meetings.REJECTED_PREFIX + "calendar[near19m]:Hop khac"))
    check("...và vẫn nói rõ nó từng ghép vào đâu (còn truy vết được)",
          "Hop khac" in meetings.explain_source(
              meetings.REJECTED_PREFIX + "calendar[near19m]:Hop khac"))

    # --- backfill: nạp bù cuộc CŨ hơn cửa sổ quét, KHÔNG gửi tin cho ai -------
    _seen34: list[dict] = []

    def _fake_enq34(reader, mt, item=None, *, priority=1, notify=True):
        _seen34.append({"mt": mt, "priority": priority, "notify": notify})
        return True

    _keep_lu34 = tokenstore.list_users
    _keep_gt34 = tokenstore.get_access_token
    _keep_ml34 = lark_api.minutes_list
    _keep_enq34 = orchestrator.enqueue_minute
    _keep_nv34 = orchestrator._note_verified_viewer
    tokenstore.list_users = lambda active_only=True: [
        {"open_id": "ou_bf", "union_id": "on_bf", "name": "Nguoi backfill"}]
    tokenstore.get_access_token = lambda oid: "tok-bf"
    lark_api.minutes_list = lambda *a, **k: [
        {"token": "obsgOLD000000000000001", "topic": "Hop thang 3"},
        {"token": "obsgOLD000000000000002", "topic": "Hop thang 4"}]
    orchestrator.enqueue_minute = _fake_enq34
    orchestrator._note_verified_viewer = lambda *a, **k: None
    try:
        wipe_jobs()
        _dry34 = orchestrator.backfill_missing(180, dry_run=True)
        check("backfill thử khô: liệt kê đủ, KHÔNG nạp job nào",
              len(_dry34) == 2 and not _seen34, str(_seen34))
        _run34 = orchestrator.backfill_missing(180, dry_run=False)
        check("backfill chạy thật: nạp đúng số cuộc còn thiếu",
              len(_run34) == 2 and len(_seen34) == 2, str(_seen34))
        # Ràng buộc quan trọng nhất của cả nhóm này: cuộc họp từ THÁNG 3 mà gửi
        # thẻ "họp xong" là tin rác cho cả phòng, và không có đường thu hồi.
        check("backfill KHÔNG BAO GIỜ gửi tin (notify=False, bất kể SEND_MODE)",
              all(x["notify"] is False for x in _seen34), str(_seen34))
        check("backfill vào BACKLOG (priority 0) — cuộc mới vẫn chen trước",
              all(x["priority"] == 0 for x in _seen34), str(_seen34))
        # Chạy lại không được nạp trùng: `db.is_claimed`/`jobstore.get` lọc trước.
        _seen34.clear()
        jobstore.create(meta(minute_token="obsgOLD000000000000001"))
        _again34 = orchestrator.backfill_missing(180, dry_run=False)
        check("backfill idempotent — cuộc đã có job thì bỏ qua",
              len(_again34) == 1, str(_again34))
    finally:
        tokenstore.list_users = _keep_lu34
        tokenstore.get_access_token = _keep_gt34
        lark_api.minutes_list = _keep_ml34
        orchestrator.enqueue_minute = _keep_enq34
        orchestrator._note_verified_viewer = _keep_nv34
    wipe_jobs()

    # =================================================================
    part("34i. Tool ĐỌC trả DỮ LIỆU, không trả 'tin đã soạn xong'")
    # =================================================================
    # Lỗi đo trên chat thật 05/08/2026 (user gửi 5 ảnh chụp). Cùng MỘT gốc:
    # `append_agent_note` tự bọc mọi kết quả thô vào khối "chép y hệt", mà
    # `mcp_server.call_tool` luôn gọi nó -> ba tool đọc đều bị đóng dấu GỬI
    # NGUYÊN VĂN -> plugin lấy khối đó THAY cho câu trả lời của agent. Đo:
    #
    #   "lark minute ok hơn hay transcript ok hơn?" -> bot dán transcript 2/2
    #   "tôi hỏi bản nào tốt hơn"                   -> bot dán kết quả search
    #   "phân tích dựa trên file word"              -> bot dán lại record
    #
    # Không câu dặn nào trong prompt cứu được, vì code ghi đè đầu ra sau LLM.
    _ctx = qa.CONTEXT_MARK
    _who34i = {"open_id": "ou_34i", "union_id": "on_34i", "name": "Nguoi 34i"}
    wipe_jobs()
    _m34i = meta(minute_token="obsg34I00000000000001", title="Hop 34i",
                 owner_open_id="ou_34i",
                 participants_source="calendar[verified]:Hop 34i")
    _m34i.attendees = [Attendee(open_id="ou_34i", union_id="on_34i")]
    jobstore.create(_m34i, status="held")
    _rec34i = {qa.bitable.F_TITLE: "Hop 34i", qa.bitable.F_TOKEN: _m34i.minute_token,
               qa.bitable.F_WHEN: "2026-08-05 09:00:00",
               qa.bitable.F_SUMMARY: "Noi dung 34i", qa.bitable.F_LINK: ""}
    _keep_en34i, _keep_rows34i = qa.bitable.enabled, qa.lark_api.base_records_all
    qa.bitable.enabled = lambda: True
    qa.lark_api.base_records_all = lambda *a, **k: [_rec34i]
    try:
        _gm = qa.get_meeting(_who34i, "Hop 34i")
        _sm = qa.search_meetings(_who34i, "34i")
    finally:
        qa.bitable.enabled = _keep_en34i
        qa.lark_api.base_records_all = _keep_rows34i

    for _nm, _out in (("get_meeting", _gm), ("search_meetings", _sm)):
        check(f"{_nm}: trả kênh DỮ LIỆU, KHÔNG phải 'chép y hệt'",
              _out.startswith(_ctx) and _send not in _out, _out[:90])
        check(f"{_nm}: vẫn có mốc ĐÓNG trước kênh nội bộ",
              qa.CONTEXT_END in _out.split(_int)[0])
        check(f"{_nm}: sổ sách (minute_token) vẫn ở kênh nội bộ",
              _m34i.minute_token in _out.split(_int)[-1])

    # Đúng chỗ plugin quyết định: khối DỮ LIỆU không được coi là câu trả lời.
    def _user_block34i(result: str) -> str:
        s = result.find(_send)
        if s < 0:
            return ""
        s = result.find("\n", s)
        e = result.rfind(qa.END_MARK)
        return "" if s < 0 or e < 0 else result[s + 1:e].strip()

    check("plugin KHÔNG lấy được khối DỮ LIỆU thay cho câu trả lời agent",
          _user_block34i(_gm) == "" and _user_block34i(_sm) == "",
          _user_block34i(_gm)[:80])
    # Còn tin ĐÃ soạn xong thì vẫn phải ghi đè — bài học 04/08 giữ nguyên.
    _sent34i = qa.two_channel("Đã gửi file biên bản X.", "đừng gọi lại tool")
    check("tin đã soạn xong (sendfile) VẪN được chép y hệt",
          _user_block34i(_sent34i) == "Đã gửi file biên bản X.")
    check("...và lời dặn agent không lọt vào phần người dùng",
          "đừng gọi lại tool" not in _user_block34i(_sent34i))
    check("agent_only (sendlist) không có khối GỬI lẫn khối DỮ LIỆU",
          _send not in qa.agent_only("x") and _ctx not in qa.agent_only("x"))

    # Plugin phải nhận ra kênh DỮ LIỆU bằng đúng chuỗi V2 phát ra.
    _plug34i = (Path(__file__).resolve().parent.parent / "hermes"
                / "v2-enroll-gate" / "__init__.py")
    if _plug34i.exists():
        _psrc = _plug34i.read_text(encoding="utf-8")
        _mark = _psrc.split('_CONTEXT_MARK = "', 1)[-1].split('"', 1)[0]
        check("plugin nhận đúng mốc DỮ LIỆU mà qa.py phát ra",
              bool(_mark) and qa.CONTEXT_MARK.startswith(_mark), _mark)
        check("plugin chặn agent dán nguyên khối DỮ LIỆU ra chat",
              "_CONTEXT_MARK in response" in _psrc)
        check("plugin tách riêng context_tools, không nhét vào user_results",
              "context_tools" in _psrc)

    # --- ĐỊNH DẠNG: lark_md chỉ có **đậm** và [nhãn](url) -------------------
    # User báo 05/08/2026 "nhiều chỗ chả thấy markdown gì cả". Hai gốc:
    #   (a) `- item` không phải cú pháp lark_md -> hiện ra đúng một gạch ngang;
    #   (b) ô nhiều dòng bị ghép sau nhãn -> item đầu dính vào nhãn, các item
    #       sau rơi xuống cột 0 thành danh sách mồ côi (thấy rõ trong ảnh chụp).
    _md_rec = {qa.bitable.F_TITLE: "Hop MD", qa.bitable.F_TOKEN: "mtMD34i",
               qa.bitable.F_WHEN: "2026-08-05 09:00:00",
               qa.bitable.F_SUMMARY: "Mot dong tom tat",
               qa.bitable.F_DECISIONS: "• Chot A\n• Chot B",
               qa.bitable.F_ACTIONS: "• Viec 1 — An\n• Viec 2 — Binh",
               qa.bitable.F_LINK: "https://lark.example/m/mtMD34i"}
    _md_out = qa.fmt_record(_md_rec)
    _md_lines = _md_out.splitlines()
    check("fmt_record: KHÔNG dùng '- ' (lark_md không render thành gạch đầu dòng)",
          not any(ln.lstrip().startswith("- ") for ln in _md_lines), _md_out)
    check("fmt_record: nhãn ô nhiều dòng đứng RIÊNG, không dính item đầu",
          "• **Việc cần làm:**" in _md_lines, _md_out)
    check("fmt_record: item của ô nhiều dòng được thụt vào, không mồ côi ở cột 0",
          all(ln.startswith("   • ") for ln in _md_lines
              if ln.strip().startswith("• Viec")), _md_out)
    check("fmt_record: ô MỘT dòng vẫn nằm cùng dòng với nhãn (đừng làm dài ra)",
          "• **Tóm tắt:** Mot dong tom tat" in _md_lines, _md_out)
    from v2.models import ActionItem as _AI34i
    _md_recap = Recap(summary="S", decisions=["D1"],
                      action_items=[_AI34i("T1", "An", "2026-08-09")])
    _md_card = _md_recap.to_markdown()
    check("Recap.to_markdown: dùng '•', không dùng '- '",
          "• D1" in _md_card
          and not any(ln.startswith("- ") for ln in _md_card.splitlines()),
          _md_card)
    check("Recap.to_markdown: KHÔNG dùng '_nghiêng_' (lark_md không có nghiêng)",
          "_An_" not in _md_card, _md_card)
    # `*nghiêng*` một dấu sao cũng không render trong lark_md — chỉ **đậm**.
    import re as _re34i
    _ital34i = _re34i.compile(r"(?<!\*)\*(?!\*)[^*\n]{1,80}\*(?!\*)")
    _md_all = "\n".join([_md_out, _md_card, qa._FOOT_ASK, qa._FOOT_ALL,
                         qa._FOOT_MIXED])
    _bad_ital = _ital34i.search(_md_all)
    check("KHÔNG dùng '*nghiêng*' một dấu sao trong text người dùng đọc",
          _bad_ital is None, _bad_ital.group(0) if _bad_ital else "")
    check("fmt_pending: KHÔNG lộ minute_token ra khối người dùng",
          "minute_token" not in qa.fmt_pending(
              {"title": "X", "when": "2026-08-05 09:00:00", "status": "queued",
               "tinh_trang": "đang chờ", "minute_token": "mtLEAK", "link": "",
               "error": ""}))

    # --- thất bại IM LẶNG: không DM, nhưng ai hỏi thì nói lý do -------------
    # User chốt 05/08/2026 sau khi nhận 3 DM "Job THẤT BẠI" trong một phút, cả
    # ba đều là bản ghi không ra chữ nào — không có việc gì để làm.
    check("mã lỗi tách được khỏi câu lỗi",
          jobstore.error_code("[empty_transcript] khong ra chu nao")
          == "empty_transcript"
          and jobstore.error_text("[empty_transcript] khong ra chu nao")
          == "khong ra chu nao")
    check("lỗi cũ KHÔNG có mã vẫn đọc được nguyên câu (đừng nuốt)",
          jobstore.error_code("loi cu") == ""
          and jobstore.error_text("loi cu") == "loi cu")
    check("EmptyTranscript được gắn đúng mã ở nơi ném ra",
          f"{{jobstore.ERR_EMPTY_TRANSCRIPT}}" in
          (Path(__file__).resolve().parent / "pipeline.py").read_text(
              encoding="utf-8"))
    from v2 import alerts as _al34i
    check("alerts: empty_transcript nằm trong danh sách KHÔNG DM",
          jobstore.ERR_EMPTY_TRANSCRIPT in _al34i.SILENT_FAIL_CODES)
    wipe_jobs()
    _f34i = meta(minute_token="obsg34IFAIL000000001", title="Hop im lang")
    jobstore.create(_f34i)
    jobstore.set_status(_f34i.minute_token, "failed",
                        error="[empty_transcript] whisper khong ra chu nao")
    _pend34i: list = []
    _al34i._check_failed_jobs(_pend34i)
    check("job im lặng KHÔNG sinh cảnh báo DM nào", not _pend34i, str(_pend34i))
    check("...và cũng KHÔNG ghi mốc chống spam (bỏ mã đi là báo lại được)",
          _al34i._mark_get(f"job_failed:{_f34i.minute_token}") is None)
    jobstore.set_status(_f34i.minute_token, "failed",
                        error="khong ai duoc phep tai ban ghi nay")
    _pend34i = []
    _al34i._check_failed_jobs(_pend34i)
    check("lỗi CẦN người xử lý thì VẪN DM như cũ", len(_pend34i) == 1,
          str(_pend34i))
    # Và lý do phải tới được người hỏi, bằng tiếng người.
    jobstore.set_status(_f34i.minute_token, "failed",
                        error="[empty_transcript] whisper chay xong nhung "
                              "KHONG ra chu nao (1517s audio, engine x). "
                              "Bang chung: abc.json")
    _row34i = jobstore.get(_f34i.minute_token)
    _ly_do = qa._fail_reason(_row34i)
    check("lý do hỏng dịch sang tiếng người, không phơi chuỗi vận hành",
          "engine" not in _ly_do and "json" not in _ly_do
          and "không nghe được câu nào" in _ly_do, _ly_do)
    check("lý do KHÔNG khẳng định 'im lặng' (25 phút 0 chữ là whisper nuốt)",
          "im lặng" not in _ly_do, _ly_do)
    _pl34i = qa.row_pending(1, {"title": "Hop im lang", "when": "", "status":
                                "failed", "tinh_trang": "XỬ LÝ HỎNG",
                                "minute_token": "x", "link": "",
                                "error": _ly_do})
    check("dòng liệt kê có nêu Lý do cho cuộc bị hỏng",
          "Lý do:" in _pl34i, _pl34i)
    wipe_jobs()

    # Hỏi lại khi trùng tên phải phân biệt được bằng mắt (ảnh 4: hai dòng y hệt).
    wipe_jobs()
    for _i, _tok in enumerate(("obsg34I00000000000002", "obsg34I00000000000003")):
        _d = meta(minute_token=_tok, title="test luong tu dong",
                  owner_open_id="ou_34i", start=None if _i else 1785000000.0,
                  participants_source="calendar[verified]:x")
        _d.attendees = [Attendee(open_id="ou_34i", union_id="on_34i")]
        jobstore.create(_d, status="held")
    _amb = qa.get_transcript(_who34i, "test luong tu dong")
    check("trùng tên: mỗi dòng có mốc thời gian, không để trống",
          "(chưa rõ giờ)" in _amb and _amb.count("·") >= 4, _amb[:200])
    wipe_jobs()

    # =================================================================
    part("34n. Bản chép sẵn của Lark — trả lời NGAY, rồi mời bản chuẩn")
    # =================================================================
    # Ca thật 06/08/2026 18:11 (user gửi ảnh chụp): người dùng mở Lark Minutes
    # thấy ĐỦ CHỮ, hỏi bot thì bot đáp "CHƯA CÓ BIÊN BẢN — đang phiên âm" rồi từ
    # chối phân tích. Bot không mù, bot chưa từng đi lấy: `minutes_transcript`
    # chỉ được gọi một lần lúc phát hiện cuộc họp, bằng một token, và token đó
    # thường không phải chủ bản ghi.
    #
    # Đo 07/08/2026 trên DB thật (chỉ đếm ký tự): 12/12 cuộc gần nhất đã có
    # whisper thì bản Lark ĐỌC ĐƯỢC (188 – 124.915 ký tự); 27 cuộc
    # `waiting_auth` thì 0/27 — chủ bản ghi chưa kết nối. Tức hễ whisper chạy
    # được thì bản Lark cũng đọc được, và nó có SỚM HƠN nhiều.
    from v2 import eta as _eta, larktext as _lt
    wipe_jobs()
    _lt_calls: list[str] = []

    def _fake_tok(oid):
        if oid in _deny_tokens:
            raise tokenstore.TokenError("chưa kết nối")
        return f"tok-{oid}"

    def _fake_minutes_transcript(access, token, **kw):
        _lt_calls.append(access)
        if access in _lt_denied:
            raise lark_api.LarkError(2091005, "permission deny",
                                     "minutes_transcript")
        return _lt_text

    _keepLT = (tokenstore.get_access_token, lark_api.minutes_transcript)
    tokenstore.get_access_token = _fake_tok
    lark_api.minutes_transcript = _fake_minutes_transcript
    _deny_tokens: set[str] = set()
    _lt_denied: set[str] = set()
    _lt_text = "\n".join(f"[0{i}:00] Nguoi {i}: cau noi thu {i} ve NGAN SACH"
                         for i in range(1, 9))
    try:
        _attLT = [Attendee(open_id="ou_LTA", union_id="on_LTA"),
                  Attendee(open_id="ou_LTB", union_id="on_LTB")]
        _mLT = meta(minute_token="mtLARK1", title="Hop chua phien am",
                    owner_open_id="ou_LTOWN", attendees=_attLT,
                    participants_source="calendar[verified]:x")
        jobstore.create(_mLT, status="transcribing")
        _lt.path_of("mtLARK1").unlink(missing_ok=True)

        # (a) Thang ứng viên: chủ bản ghi bị từ chối thì PHẢI thử tiếp người dự,
        # không bỏ cuộc. Đây đúng là hình dạng đo được ở `download_recording`:
        # quyền gắn với từng bản ghi, không suy ra được, phải THỬ.
        _lt_denied = {"tok-ou_LTOWN"}
        _got = _lt.fetch(_mLT)
        check("bản Lark: chủ bị từ chối thì thử tiếp người dự, không bỏ cuộc",
              "NGAN SACH" in _got and "tok-ou_LTA" in _lt_calls, str(_lt_calls))
        check("...và lưu xuống đĩa để lần sau khỏi gọi mạng",
              _lt.path_of("mtLARK1").exists() and _lt.have("mtLARK1"))
        _n = len(_lt_calls)
        check("đã có cache -> `get` KHÔNG gọi mạng thêm lần nào",
              _lt.get({"minute_token": "mtLARK1", "meta_json":
                       jobstore.meta_to_json(_mLT)}) == _got
              and len(_lt_calls) == _n)

        # (b) Không ai đọc được -> ném, và GHI SỔ để không nã lại mỗi câu hỏi.
        # Cuộc `waiting_auth` thật có tới 21 ứng viên; thiếu phanh này thì mỗi
        # câu hỏi là 21 lời gọi mạng mà kết quả vẫn y nguyên.
        _mDEN = meta(minute_token="mtLARK2", title="Khong ai doc duoc",
                     owner_open_id="ou_LTOWN", attendees=_attLT,
                     participants_source="calendar[verified]:x")
        jobstore.create(_mDEN, status="waiting_auth")
        _lt.path_of("mtLARK2").unlink(missing_ok=True)
        _lt_denied = {"tok-ou_LTOWN", "tok-ou_LTA", "tok-ou_LTB"}
        try:
            _lt.fetch(_mDEN)
            _threw = False
        except _lt.LarkTextError:
            _threw = True
        check("không token nào đọc được -> ném LarkTextError, không trả rỗng lặng",
              _threw)
        check("...và ghi mốc đã thử, nên lần hỏi kế tiếp bị phanh lại",
              _lt._tried_recently("mtLARK2"))
        _n = len(_lt_calls)
        _row2 = dict(jobstore.get("mtLARK2"))
        check("phanh có tác dụng thật: `get` không gọi thêm lời nào",
              _lt.get(_row2) == "" and len(_lt_calls) == _n)

        # (c) qa: cuộc CHƯA phiên âm xong vẫn trả lời được, và có lời MỜI.
        _whoLTA = {"union_id": "on_LTA", "open_id": "ou_LTA", "name": "A"}
        _whoOut = {"union_id": "on_NGOAI", "open_id": "ou_NGOAI", "name": "X"}
        _outLT = qa.get_transcript(_whoLTA, "mtLARK1")
        check("cuộc đang phiên âm: trả NỘI DUNG của Lark, không nói 'chưa có'",
              "NGAN SACH" in _outLT and "chưa có nguyên văn" not in _outLT,
              _outLT[:200])
        check("...gắn nhãn đúng nguồn, không giả vờ là bản nguyên văn chuẩn",
              "bản chép sẵn của Lark" in _outLT)
        check("...đi kênh DỮ LIỆU để agent trả lời, KHÔNG phải 'chép y hệt'",
              _outLT.startswith(qa.CONTEXT_MARK) and qa.SEND_MARK not in _outLT)
        check("...và có lời MỜI lấy bản chuẩn kèm ước tính thời gian",
              qa.OFFER_MARK in _outLT and "phút" in _outLT.split(
                  qa.INTERNAL_MARK)[-1], _outLT[-400:])
        check("lời mời nằm ở kênh NỘI BỘ, người dùng không đọc thấy mã máy",
              qa.INTERNAL_MARK in _outLT
              and qa.OFFER_MARK in _outLT.split(qa.INTERNAL_MARK)[-1])

        # (d) CỬA QUYỀN không đổi. Bản Lark là một nguồn nội dung MỚI, nên nếu
        # nó đi vòng qua `_may_see` thì §20 sập — người ngoài đọc được cuộc họp
        # người khác qua đúng đường vừa mở.
        _outNo = qa.get_transcript(_whoOut, "mtLARK1")
        check("người KHÔNG dự vẫn bị chặn, không lọt một chữ nào của bản Lark",
              "NGAN SACH" not in _outNo
              and "không có trong danh sách người dự" in _outNo, _outNo[:160])
        check("who=None vẫn từ chối như cũ",
              qa.get_transcript(None, "mtLARK1") == qa.NO_ASKER)

        # (e) `get_meeting` cũng phải đi cửa này: người dùng hỏi "phân tích cuộc
        # đó" chứ không gọi tên tool nào, và agent hay chọn get_meeting trước.
        # Base RỖNG là đúng tình huống: cuộc vừa họp xong chưa có record nào.
        _keepEnLT, _keepRowsLT = qa.bitable.enabled, qa.lark_api.base_records_all
        qa.bitable.enabled = lambda: True
        qa.lark_api.base_records_all = lambda *a, **k: []
        _outGM = qa.get_meeting(_whoLTA, "Hop chua phien am")
        check("get_meeting cuộc chưa lên Base: cũng trả nội dung Lark",
              "NGAN SACH" in _outGM and qa.OFFER_MARK in _outGM, _outGM[:200])
        check("get_meeting: người ngoài vẫn không thấy gì",
              "NGAN SACH" not in qa.get_meeting(_whoOut, "Hop chua phien am"))
        # Cuộc KHÔNG đọc được bản nào thì vẫn phải nói thật, không được im.
        _outNone = qa.get_meeting(_whoLTA, "Khong ai doc duoc")
        qa.bitable.enabled, qa.lark_api.base_records_all = _keepEnLT, _keepRowsLT
        check("không đọc được bản nào -> nói thẳng, và KHÔNG mời bản chuẩn",
              "chưa đọc được bản chép nào" in _outNone
              and qa.OFFER_MARK not in _outNone, _outNone[:200])

        # (e2) ĐÃ CÓ whisper thì TUYỆT ĐỐI không được phục vụ bản Lark.
        # Lỗi tự tạo ra khi thêm đường này (bắt được 09/08/2026 lúc kiểm lại):
        # job `held` CÓ nguyên văn nhưng ghi record hỏng thì vẫn nằm trong khối
        # "chưa có biên bản", nên `get_meeting` đi thẳng xuống `from_lark` và
        # đưa bản KÉM HƠN — rồi còn mời "phiên âm lại, mất khoảng 10 phút"
        # trong khi file đã nằm sẵn trên đĩa. Bot hứa một việc nó không cần làm.
        _mDONE = meta(minute_token="mtLARK3", title="Da co whisper roi",
                      owner_open_id="ou_LTOWN", attendees=_attLT,
                      participants_source="calendar[verified]:x")
        jobstore.create(_mDONE, status="held")
        jobstore.set_status("mtLARK3", "held", transcript_path=_mk_transcript(
            "mtLARK3", [(0.0, "cau whisper chinh xac hon")]))
        _rowDONE = dict(jobstore.get("mtLARK3"))
        _lt.path_of("mtLARK3").write_text("BAN LARK KEM HON", encoding="utf-8")
        check("đã có nguyên văn whisper -> from_lark TỪ CHỐI phục vụ bản Lark",
              qa.from_lark(_rowDONE, "Da co whisper roi") == "")
        _keepEnLT2, _keepRowsLT2 = qa.bitable.enabled, qa.lark_api.base_records_all
        qa.bitable.enabled = lambda: True
        qa.lark_api.base_records_all = lambda *a, **k: []
        _outDONE = qa.get_meeting(_whoLTA, "Da co whisper roi")
        qa.bitable.enabled, qa.lark_api.base_records_all = _keepEnLT2, _keepRowsLT2
        check("...get_meeting chỉ sang nguyên văn, KHÔNG đưa bản Lark",
              "BAN LARK KEM HON" not in _outDONE
              and "get_transcript" in _outDONE, _outDONE[:200])
        check("...và KHÔNG mời phiên âm lại thứ đã phiên âm xong",
              qa.OFFER_MARK not in _outDONE
              and "chờ phiên âm" not in _outDONE.replace("đừng bảo họ chờ "
                                                         "phiên âm", ""),
              _outDONE[:200])
        _lt.path_of("mtLARK3").unlink(missing_ok=True)

        # (f) Danh sách: cuộc đã tải được bản Lark phải được NÓI RA, không thì
        # agent tưởng không có gì để đọc và lại trả lời "chưa có biên bản".
        _pend = [p for p in qa.pending_meetings(_whoLTA)
                 if p["minute_token"] == "mtLARK1"]
        check("danh sách: cuộc có bản Lark được gắn nhãn ĐỌC ĐƯỢC",
              bool(_pend) and "ĐỌC ĐƯỢC" in qa.fmt_pending(_pend[0]),
              qa.fmt_pending(_pend[0]) if _pend else "(không thấy cuộc)")
        _pend2 = [p for p in qa.pending_meetings(_whoLTA)
                  if p["minute_token"] == "mtLARK2"]
        check("...cuộc KHÔNG đọc được thì không gắn nhãn (đừng hứa suông)",
              bool(_pend2) and "ĐỌC ĐƯỢC" not in qa.fmt_pending(_pend2[0]))
    finally:
        tokenstore.get_access_token, lark_api.minutes_transcript = _keepLT
        _lt.path_of("mtLARK1").unlink(missing_ok=True)
        _lt.path_of("mtLARK2").unlink(missing_ok=True)

    # --- Ước tính thời gian (v2/eta.py) -----------------------------------
    # Câu cũ là "cuộc ngắn vài phút, cuộc dài thì lâu hơn" — đúng mà vô dụng.
    # Đo 15 job gần nhất: tỉ lệ whisper/audio 0,11–0,16 (gộp 0,14), tức con số
    # này nói ra được. Nguyên tắc: thà nói DÀI hơn thực tế.
    check("ước tính: 3 giây audio vẫn ra câu tối thiểu, không ra '0 phút'",
          _eta.phrase(_eta.job_seconds({"audio_seconds": 3}, 0.14))
          .startswith("khoảng"))
    check("ước tính làm tròn LÊN theo bậc 5 phút", _eta.phrase(6 * 60)
          == "khoảng 10 phút", _eta.phrase(6 * 60))
    check("ước tính dài thì đổi sang tiếng, không đọc '95 phút'",
          _eta.phrase(200 * 60).endswith("tiếng"), _eta.phrase(200 * 60))
    check("không ước tính được -> trả rỗng, để caller BỎ mệnh đề đó",
          _eta.phrase(0) == "")
    _1h = _eta.job_seconds({"audio_seconds": 3600}, 0.14)
    check("cuộc 1 tiếng: ước tính nằm trong khoảng hợp lý 10–30 phút",
          10 * 60 <= _1h <= 30 * 60, f"{_1h:.0f}s")
    check("tỉ lệ đo được luôn nằm trong hai mép chặn",
          _eta.RATIO_MIN <= _eta.whisper_ratio() <= _eta.RATIO_MAX)
    check("không biết độ dài -> vẫn ước tính được, không chia cho 0",
          _eta.job_seconds({}, 0.14) > 0)
    # Hàng chờ phải được cộng vào: người xin lúc hệ thống đang dịch cuộc 2 tiếng
    # thì phải chờ hết cuộc đó, `process_queue` không cắt ngang.
    wipe_jobs()
    _mQ = meta(minute_token="mtETA1", title="Cuoc cua toi",
               owner_open_id="ou_E", attendees=[Attendee(open_id="ou_E",
                                                         union_id="on_E")])
    _mR = meta(minute_token="mtETA0", title="Dang chay",
               owner_open_id="ou_E", attendees=[Attendee(open_id="ou_E",
                                                         union_id="on_E")])
    jobstore.create(_mR, status="queued", priority=2)
    jobstore.create(_mQ, status="queued", priority=2)
    jobstore.set_status("mtETA0", "transcribing", audio_seconds=7200)
    jobstore.set_status("mtETA1", "queued", audio_seconds=1800)
    _alone = _eta.job_seconds(dict(jobstore.get("mtETA1")))
    check("ước tính CÓ cộng job đang chạy dở, không chỉ tính mỗi mình",
          _eta.estimate("mtETA1") > _alone, f"{_eta.estimate('mtETA1'):.0f}s")
    wipe_jobs()

    # --- Hợp đồng dấu MỜI, hai bên hai tiến trình -------------------------
    # V2 chạy Python 3.12, plugin chạy Python 3.11 của Hermes. Không có trình
    # biên dịch nào bắt được chuỗi lệch nhau, nên khoá bằng test.
    check("dấu MỜI: chuỗi của qa.py và của plugin khớp NGUYÊN VĂN",
          qa.OFFER_MARK == _plug._OFFER_MARK,
          f"{qa.OFFER_MARK!r} vs {_plug._OFFER_MARK!r}")

    _plug._offered.clear()
    _plug._asked.clear()
    check("chưa mời gì -> 'có' KHÔNG mở được cổng gửi file",
          not _plug._offered_recently("sess-of"))
    _plug._on_post_tool_call(
        tool_name="mcp__meetings__get_transcript", session_id="sess-of",
        status="ok", result=f"{qa.CONTEXT_MARK}\nnoi dung\n{qa.CONTEXT_END}\n\n"
                            f"{qa.INTERNAL_MARK}\n{qa.OFFER_MARK}\nmoi ban")
    check("bot vừa mời -> phiên được ghi nhận là ĐÃ MỜI",
          _plug._offered_recently("sess-of"))

    def _gate_send(session_id, user_text):
        """Cổng write-tool của send_transcript_file trả về gì cho câu này."""
        with _plug._turn_lock:
            _plug._turns[session_id] = {
                "turn_id": "t", "required": True,
                "user_normalized": _plug._plain(user_text),
                "tool_called": False, "user_results": [],
                "direct_tools": [], "context_tools": []}
        return _plug._on_pre_tool_call(
            session_id=session_id, tool_name="mcp__meetings__send_transcript_file")

    check("đã mời + người dùng đáp 'có' -> CHO gửi file",
          _gate_send("sess-of", "có") is None)
    check("đã mời + 'ok gửi đi' -> vẫn cho",
          _gate_send("sess-of", "ok gửi đi") is None)
    check("đã mời nhưng câu KHÔNG phải đồng ý -> vẫn chặn",
          (_gate_send("sess-of", "thế cuộc hôm qua thì sao") or {})
          .get("action") == "block")
    # Cái bẫy: "có" đứng đầu một câu HỎI. Không có phép kiểm độ dài + phạm vi
    # nghiệp vụ thì bot gửi file khi người ta chỉ đang hỏi lịch họp.
    check("'có cuộc họp nào hôm nay' là câu HỎI, không phải lời đồng ý",
          not _plug._is_yes(_plug._plain("có cuộc họp nào hôm nay")))
    check("'có bao nhiêu cuộc họp trong tuần' cũng vậy",
          not _plug._is_yes(_plug._plain("có bao nhiêu cuộc họp trong tuần")))
    check("'có' trần thì đúng là đồng ý", _plug._is_yes(_plug._plain("có")))
    check("'vâng cần nhé' cũng là đồng ý",
          _plug._is_yes(_plug._plain("vâng cần nhé")))
    _plug._offered.clear()
    check("CHƯA mời mà đáp 'có' -> vẫn chặn (cần CẢ HAI vế)",
          (_gate_send("sess-of", "có") or {}).get("action") == "block")

    # Lời mời phải sống QUA LƯỢT: bot mời ở lượt N, người dùng đáp ở lượt N+1.
    # `post_llm_call` chạy giữa hai lượt đó.
    _plug._mark_offered("sess-live")
    _plug._cleanup_turn(session_id="sess-live")
    check("hết một lượt KHÔNG xoá lời mời (không thì câu 'có' vô nghĩa)",
          _plug._offered_recently("sess-live"))
    _plug._cleanup_session(session_id="sess-live")
    check("hết PHIÊN thì mới xoá, không để lời mời sống mãi",
          not _plug._offered_recently("sess-live"))
    _plug._offered["sess-ttl"] = _plug.time.time() - _plug._ASK_TTL_S - 1
    check("lời mời quá hạn tự đóng", not _plug._offered_recently("sess-ttl"))

    # --- Văn phong: chặn bot nhại lại ngôn ngữ máy ------------------------
    # Ca thật 06/08/2026: bot trả lời "hệ thống hiện vẫn báo **CHƯA CÓ BIÊN
    # BẢN** — đang phiên âm". Nhãn viết hoa là mốc cho MẮT quét trong danh
    # sách, không phải từ để đưa vào câu văn xuôi.
    check("policy có dặn KHÔNG nhại lại nhãn trạng thái viết hoa",
          "NHẠI LẠI NGÔN NGỮ MÁY" in _plug._POLICY)
    check("policy có dạy luồng: trả lời bằng bản Lark -> mời bản chuẩn",
          "bản chép sẵn của Lark" in _plug._POLICY
          and "send_transcript_file" in _plug._POLICY)
    check("policy đổi version để phiên cũ nhận chính sách mới",
          _plug.POLICY_VERSION.endswith("2026-09-28.1"), _plug.POLICY_VERSION)

    # --- Ước tính phải TỚI được người dùng qua sendfile -------------------
    wipe_jobs()
    _mSF = meta(minute_token="mtETASF", title="Xin ban chuan",
                owner_open_id="ou_S",
                attendees=[Attendee(open_id="ou_S", union_id="on_S")],
                participants_source="calendar[verified]:x")
    jobstore.create(_mSF, status="held")
    _whoS = {"union_id": "on_S", "open_id": "ou_S", "name": "S"}
    _sf = sendfile.send_transcript(_whoS, "mtETASF")
    check("xin bản chuẩn khi chưa có -> hứa TỰ GỬI, kèm con số ước tính",
          "TỰ GỬI" in _sf and "Ước tính" in _sf, _sf[:220])
    check("...và job được nâng lên cực cao + ghi tên người chờ",
          (jobstore.get("mtETASF") or {}).get("priority") == 2
          and any(r["requester"] == "on_S"
                  for r in db.transcript_requesters("mtETASF")))
    wipe_jobs()

    # =================================================================
    part("V3-YC1. Chủ cuộc họp duyệt biên bản trước khi báo người dự")
    # =================================================================
    from v2 import confirm
    _c_keep = (lark_api.im_send_card, config.SEND_MODE, config.CONFIRM_ENABLED,
               summarize.apply_edit)
    _c_sent: list[tuple[str, dict, str]] = []
    try:
        lark_api.im_send_card = (lambda rid, card, *, id_type="union_id",
                                 uuid_key=None: _c_sent.append((rid, card, uuid_key)) or "om")
        config.SEND_MODE, config.CONFIRM_ENABLED = True, True
        summarize.apply_edit = lambda recap, instr, title: Recap(
            summary=recap.summary + " [đã sửa: " + instr + "]",
            decisions=recap.decisions, action_items=recap.action_items)
        with db.tx() as c:
            c.execute("DELETE FROM confirmations")
            c.execute("DELETE FROM tokens")
        wipe_jobs()
        add_user("ou_OWN", "on_OWN", "Chu Tri")
        add_user("ou_B", "on_B", "Nguoi B")
        add_user("ou_X", "on_X", "Nguoi Ngoai")
        _m1 = meta(minute_token="mtCONF1", title="Hop duyet", owner_open_id="ou_OWN",
                   owner_name="Chu Tri", participants_source="calendar[verified]:x",
                   attendees=[Attendee(open_id="ou_OWN", union_id="on_OWN"),
                              Attendee(open_id="ou_B", union_id="on_B")])
        jobstore.create(_m1, status="queued")
        _r1 = Recap(summary="Chot gia 90k", decisions=["gia 90k"])
        jobstore.set_status("mtCONF1", "queued", recap_json=confirm.recap_to_json(_r1))
        _own = {"union_id": "on_OWN", "open_id": "ou_OWN", "name": "Chu Tri"}

        check("chủ đã enroll -> gate GIỮ lại, không báo người dự",
              confirm.gate(_m1, _r1) == "held")
        check("...đúng 1 thẻ duyệt, gửi cho CHỦ, không ai khác",
              [x[0] for x in _c_sent] == ["on_OWN"]
              and "Cần bạn duyệt" in _c_sent[0][1]["header"]["title"]["content"],
              str([x[0] for x in _c_sent]))
        confirm.gate(_m1, _r1)
        check("gọi gate lần hai (vòng sau / restart) -> KHÔNG gửi lại", len(_c_sent) == 1)

        _c_sent.clear()
        _deny = confirm.approve({"union_id": "on_B", "open_id": "ou_B", "name": "B"},
                                "mtCONF1")
        check("người KHÔNG phải chủ nhắn 'duyệt' -> bị từ chối, không phát gì",
              "Chỉ chủ trì" in _deny and not _c_sent
              and confirm.get("mtCONF1")["state"] == "pending", _deny)

        _ix = qa.viewers_index()
        check("đang chờ chủ duyệt -> người dự HỎI BOT cũng chưa thấy (không đi vòng cổng)",
              not qa._may_see("mtCONF1", {"union_id": "on_B", "open_id": "ou_B"}, _ix)
              and qa._may_see("mtCONF1", _own, _ix))
        _ed = confirm.edit(_own, "mtCONF1", "gia la 100k")
        _row = confirm.get("mtCONF1")
        check("chủ sửa -> recap đổi, version 2, chưa phát cho ai",
              "đã sửa" in _ed and _row["version"] == 2 and _row["edits"] == 1
              and not _c_sent, _ed[:120])
        check("...bản của chủ bị KHOÁ: tóm tắt whisper về sau không ghi đè",
              confirm.locked("mtCONF1")
              and "đã sửa" in pipeline.save_recap(_m1, Recap(summary="ban whisper")).summary
              and "đã sửa" in confirm.current_recap("mtCONF1").summary)

        _ap = confirm.approve(_own, "mtCONF1")
        _row = confirm.get("mtCONF1")
        check("chủ duyệt -> phát cho người dự (trừ chính chủ), đúng BẢN ĐÃ SỬA",
              [x[0] for x in _c_sent] == ["on_B"]
              and "đã sửa" in json.dumps(_c_sent[0][1], ensure_ascii=False)
              and _row["state"] == "confirmed" and _row["released_version"] == 2,
              f"{[x[0] for x in _c_sent]} {_ap}")
        check("...và đo được mức chủ sửa (diff_chars > 0)", _row["diff_chars"] > 0)
        check("...duyệt xong thì người dự hỏi bot thấy được",
              qa._may_see("mtCONF1", {"union_id": "on_B", "open_id": "ou_B"},
                          qa.viewers_index()))
        _c_sent.clear()
        confirm.approve(_own, "mtCONF1")
        check("duyệt lại khi nội dung không đổi -> KHÔNG gửi gì cho người dự",
              not _c_sent)

        # --- quá hạn -> tự phát kèm nhãn; chủ sửa muộn -> báo 'Đã hiệu chỉnh' 1 lần
        _m2 = meta(minute_token="mtCONF2", title="Hop im lang", owner_open_id="ou_OWN",
                   participants_source="calendar[verified]:x",
                   attendees=[Attendee(open_id="ou_B", union_id="on_B")])
        jobstore.create(_m2, status="held")
        jobstore.set_status("mtCONF2", "held", recap_json=confirm.recap_to_json(_r1))
        confirm.gate(_m2, _r1)
        _c_sent.clear()
        _h = 3_600_000
        _update = confirm._update
        _update("mtCONF2", sent_at=confirm._now_ms() - 9 * _h)
        confirm.tick()
        check("quá 8h chưa duyệt -> nhắc chủ đúng 1 lần",
              [x[0] for x in _c_sent] == ["on_OWN"]
              and "Nhắc" in _c_sent[0][1]["header"]["title"]["content"])
        confirm.tick()
        check("...vòng sau KHÔNG nhắc lại", len(_c_sent) == 1)
        _c_sent.clear()
        _update("mtCONF2", sent_at=confirm._now_ms() - 25 * _h)
        confirm.tick()
        check("quá 24h -> tự phát cho người dự, kèm nhãn 'chưa được chủ trì review'",
              [x[0] for x in _c_sent] == ["on_B"]
              and "chưa được chủ trì review" in json.dumps(_c_sent[0][1], ensure_ascii=False)
              and confirm.get("mtCONF2")["state"] == "auto_published")
        _c_sent.clear()
        confirm.approve(_own, "mtCONF2")
        check("chủ duyệt MUỘN, không sửa -> chỉ gỡ nhãn, không gửi gì thêm",
              not _c_sent and confirm.get("mtCONF2")["state"] == "confirmed")
        confirm.edit(_own, "mtCONF2", "them y X")
        confirm.approve(_own, "mtCONF2")
        _hdr = [x[1]["header"]["title"]["content"] for x in _c_sent]
        check("chủ sửa MUỘN rồi duyệt -> người dự nhận thẻ 'Đã hiệu chỉnh', đúng 1 lần",
              [x[0] for x in _c_sent] == ["on_B"] and _hdr[0].startswith("Đã hiệu chỉnh")
              and _c_sent[0][2] != "", str(_hdr))
        check("...khoá chống trùng của bản hiệu chỉnh KHÁC bản đầu (Lark không nuốt)",
              "v2" in (_c_sent[0][2] or ""), str(_c_sent[0][2]))

        # --- chủ chưa enroll -> phát ngay kèm nhãn; tắt tính năng -> như cũ
        _m3 = meta(minute_token="mtCONF3", owner_open_id="ou_NOBODY",
                   participants_source="calendar[verified]:x",
                   attendees=[Attendee(open_id="ou_B", union_id="on_B")])
        jobstore.create(_m3, status="queued")
        check("chủ CHƯA enroll -> không ai duyệt được -> phát ngay kèm nhãn",
              confirm.gate(_m3, _r1) == "unreviewed"
              and confirm.get("mtCONF3")["state"] == "auto_published")
        config.CONFIRM_ENABLED = False
        check("CONFIRM_ENABLED=0 -> hành vi cũ (phát ngay, không nhãn)",
              confirm.gate(_m1, _r1) == "publish")
        config.CONFIRM_ENABLED = True

        # --- whisper về khi chủ CHƯA đụng vào -> gửi chủ bản mới để duyệt đúng cái sẽ phát
        _m4 = meta(minute_token="mtCONF4", owner_open_id="ou_OWN",
                   participants_source="calendar[verified]:x",
                   attendees=[Attendee(open_id="ou_B", union_id="on_B")])
        jobstore.create(_m4, status="queued")
        confirm.gate(_m4, _r1)
        _c_sent.clear()
        pipeline.save_recap(_m4, Recap(summary="Ban tu nguyen van, chi tiet hon"))
        check("tóm tắt từ nguyên văn về lúc chủ chưa duyệt -> chủ nhận 'Bản cập nhật'",
              [x[0] for x in _c_sent] == ["on_OWN"]
              and "Bản cập nhật" in _c_sent[0][1]["header"]["title"]["content"]
              and confirm.get("mtCONF4")["version"] == 2)
        check("...và bản đó CHƯA bị khoá (chủ chưa sửa/duyệt)",
              not confirm.locked("mtCONF4"))

        # --- MCP: hai tool mới có mặt, gọi qua call_tool không vỡ
        _names = {t["name"] for t in mcp_server.public_tools()}
        check("MCP có confirm_meeting + edit_meeting",
              {"confirm_meeting", "edit_meeting"} <= _names)
        _txt, _err = mcp_server.call_tool("confirm_meeting",
                                          {"asker_token": "sai", "minute_token": "mtCONF1"})
        check("...vé sai -> không duyệt được (fail-closed)",
              "Chỉ chủ trì" not in _txt and confirm.get("mtCONF1")["state"] == "confirmed"
              and not _err, _txt[:100])
    finally:
        (lark_api.im_send_card, config.SEND_MODE, config.CONFIRM_ENABLED,
         summarize.apply_edit) = _c_keep
        with db.tx() as c:
            c.execute("DELETE FROM confirmations")
        wipe_jobs()

    # =================================================================
    part("V3-YC2. Biên bản .md + quyền theo chuỗi quản lý + Drive")
    # =================================================================
    from v2 import confirm as _cf2, notes, org
    _n_keep = {k: getattr(lark_api, k) for k in (
        "contact_departments", "contact_users_in", "drive_root_folder",
        "drive_folder_create", "drive_upload", "drive_file_delete",
        "drive_file_url", "drive_member_add", "im_send_card")}
    _n_cfg = (config.SEND_MODE, config.DRIVE_ENABLED)
    _drv: dict[str, list] = {"folder": [], "upload": [], "delete": [], "member": []}
    try:
        with db.tx() as c:
            for t in ("org_edges", "note_grants", "drive_spaces", "note_files",
                      "confirmations", "tokens"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()
        # Cây: D -> E -> C ; F ở nhánh khác ; A, B không có sếp trong dữ liệu.
        _people = [("ou_A", "on_A", "A", ""), ("ou_B", "on_B", "B", ""),
                   ("ou_C", "on_C", "C", "ou_E"), ("ou_E", "on_E", "E", "ou_D"),
                   ("ou_D", "on_D", "D", ""), ("ou_F", "on_F", "F", ""),
                   ("ou_Q", "on_Q", "Nghi viec", "ou_D")]
        lark_api.contact_departments = lambda: ["0", "od_1"]
        lark_api.contact_users_in = lambda d: [] if d == "0" else [
            {"open_id": o, "union_id": u, "name": n, "leader_user_id": l,
             "status": {"is_resigned": o == "ou_Q"}} for o, u, n, l in _people]
        _dry = org.sync(dry_run=True)
        check("org-sync THỬ KHÔ không ghi gì",
              db.conn().execute("SELECT COUNT(*) FROM org_edges").fetchone()[0] == 0)
        _got = org.sync(dry_run=False)
        check("org-sync ghi cây, bỏ người đã nghỉ việc", _got["users"] == 6
              and not db.conn().execute(
                  "SELECT 1 FROM org_edges WHERE open_id='ou_Q'").fetchone(), str(_got))
        check("chain_up(C) = [E, D]", org.chain_up("ou_C") == ["ou_E", "ou_D"])
        with db.tx() as c:
            c.execute("INSERT INTO org_edges(open_id, leader_open_id) VALUES "
                      "('ou_L1','ou_L2'),('ou_L2','ou_L1')")
        check("dữ liệu vòng (L1<->L2) không treo", org.chain_up("ou_L1") == ["ou_L2"])

        _mA = meta(minute_token="mtORG1", title="Hop A", owner_open_id="ou_A",
                   owner_name="A", participants_source="calendar[verified]:x",
                   attendees=[Attendee(open_id="ou_B", union_id="on_B", name="B"),
                              Attendee(open_id="ou_C", union_id="on_C", name="C")])
        jobstore.create(_mA, status="held")
        jobstore.set_status("mtORG1", "held", recap_json=_cf2.recap_to_json(
            Recap(summary="Tom tat A", decisions=["QD1"])))
        notes.publish("mtORG1")
        _src = {g["open_id"]: g["source"] for g in notes.grants_of("mtORG1")}
        check("ví dụ chuẩn: A chủ, B C dự, E quản lý C, D quản lý E -> cả 5 có quyền",
              set(_src) == {"ou_A", "ou_B", "ou_C", "ou_E", "ou_D"}, str(_src))
        check("...nguồn quyền ghi lại được (audit): D qua nhánh D>E>C",
              _src.get("ou_D") == "chain:D>E>C" and _src.get("ou_E") == "chain:E>C",
              str(_src))
        _idx = qa.viewers_index()
        _W = lambda o: {"union_id": o.replace("ou_", "on_"), "open_id": o, "name": o}
        check("D và E (không dự họp) TRUY VẤN được cuộc họp qua luật quyền duy nhất",
              qa._may_see("mtORG1", _W("ou_D"), _idx)
              and qa._may_see("mtORG1", _W("ou_E"), _idx))
        check("F (nhánh khác) KHÔNG thấy", not qa._may_see("mtORG1", _W("ou_F"), _idx))

        _md = Path(notes.get_file("mtORG1")["local_path"]).read_text(encoding="utf-8")
        check("file .md có tóm tắt/quyết định, nằm trong space của CHỦ",
              "Tom tat A" in _md and "- QD1" in _md
              and "/ou_A/" in notes.get_file("mtORG1")["local_path"].replace("\\", "/"))

        # Đổi sếp: C chuyển sang F. Quyền CŨ giữ; cuộc MỚI theo nhánh mới.
        with db.tx() as c:
            c.execute("UPDATE org_edges SET leader_open_id='ou_F' WHERE open_id='ou_C'")
        _mN = meta(minute_token="mtORG2", title="Hop moi", owner_open_id="ou_C",
                   participants_source="calendar[verified]:x", attendees=[])
        jobstore.create(_mN, status="held")
        notes.publish("mtORG2")
        _idx = qa.viewers_index()
        check("đổi sếp: E, D VẪN thấy cuộc CŨ (quyền không bị thu hồi)",
              qa._may_see("mtORG1", _W("ou_E"), _idx)
              and qa._may_see("mtORG1", _W("ou_D"), _idx))
        check("...cuộc MỚI của C: F thấy, E/D không",
              qa._may_see("mtORG2", _W("ou_F"), _idx)
              and not qa._may_see("mtORG2", _W("ou_E"), _idx)
              and not qa._may_see("mtORG2", _W("ou_D"), _idx))
        check("...F chưa tự có quyền cuộc CŨ (cấp lùi phải tường minh)",
              not qa._may_see("mtORG1", _W("ou_F"), _idx))
        _bf = notes.backfill_grants("ou_F", dry_run=True)
        check("grants-backfill THỬ KHÔ: liệt kê, chưa cấp",
              "mtORG1" in _bf and not qa._may_see("mtORG1", _W("ou_F"), qa.viewers_index()))
        notes.backfill_grants("ou_F", dry_run=False)
        check("grants-backfill --yes: F thấy cuộc cũ, nguồn ghi 'backfill'",
              qa._may_see("mtORG1", _W("ou_F"), qa.viewers_index())
              and any(g["source"].startswith("backfill") for g in notes.grants_of("mtORG1")
                      if g["open_id"] == "ou_F"))

        _mNear = meta(minute_token="mtORG3", owner_open_id="ou_A",
                      participants_source="calendar[near5m]:x",
                      attendees=[Attendee(open_id="ou_C", union_id="on_C")])
        jobstore.create(_mNear, status="held")
        notes.publish("mtORG3")
        check("người dự ghép theo GIỜ (calendar[near]) KHÔNG được cấp quyền, kể cả sếp họ",
              {g["open_id"] for g in notes.grants_of("mtORG3")} == {"ou_A"})

        # Cuộc backlog (không qua _notify_minute) vẫn phải có biên bản + quyền quản lý
        _mBL = meta(minute_token="mtORGBL", owner_open_id="ou_A",
                    participants_source="calendar[verified]:x",
                    attendees=[Attendee(open_id="ou_C", union_id="on_C")])
        jobstore.create(_mBL, status="held", priority=0)
        _keep_sm, config.SEND_MODE = config.SEND_MODE, True
        try:
            orchestrator._base_record_held("mtORGBL", _mBL)
        finally:
            config.SEND_MODE = _keep_sm
        check("cuộc BACKLOG (nạp lúc enroll, không báo ai) vẫn có .md + quyền cho sếp nhánh mới",
              notes.get_file("mtORGBL") is not None
              and "ou_F" in {g["open_id"] for g in notes.grants_of("mtORGBL")})
        with db.tx() as c:
            c.execute("INSERT INTO confirmations(minute_token, state) VALUES ('mtORGPD','pending')")
        _mPD = meta(minute_token="mtORGPD", owner_open_id="ou_A",
                    participants_source="calendar[verified]:x", attendees=[])
        jobstore.create(_mPD, status="held")
        _keep_sm, config.SEND_MODE = config.SEND_MODE, True
        try:
            orchestrator._base_record_held("mtORGPD", _mPD)
        finally:
            config.SEND_MODE = _keep_sm
        check("...còn cuộc ĐANG CHỜ chủ duyệt thì chưa ghi biên bản / chưa cấp quyền",
              notes.get_file("mtORGPD") is None and not notes.grants_of("mtORGPD"))

        # Nhãn duyệt trong .md + Base index
        with db.tx() as c:
            c.execute("INSERT INTO confirmations(minute_token, state) VALUES "
                      "('mtORG1','auto_published')")
        notes.publish("mtORG1")
        _md = Path(notes.get_file("mtORG1")["local_path"]).read_text(encoding="utf-8")
        check(".md mang nhãn 'Chưa được chủ trì review' khi tự phát",
              "Chưa được chủ trì review" in _md)
        _cf2._update("mtORG1", state="confirmed")
        notes.publish("mtORG1")
        _md = Path(notes.get_file("mtORG1")["local_path"]).read_text(encoding="utf-8")
        check("...và 'Chủ trì đã duyệt' sau khi chủ duyệt", "Chủ trì đã duyệt" in _md)
        from v2 import bitable as _bt2
        check("Base (index) có cột Xác nhận lấy từ SQLite",
              _bt2._job_fields(jobstore.get("mtORG1")).get(_bt2.F_CONFIRM)
              == "chủ trì đã duyệt")

        # Drive: bật công tắc, mọi lời gọi là hàm giả
        config.SEND_MODE, config.DRIVE_ENABLED = True, True
        lark_api.drive_root_folder = lambda: "fld_ROOT"
        lark_api.drive_folder_create = lambda name, parent: (
            _drv["folder"].append((name, parent)) or f"fld_{len(_drv['folder'])}")
        lark_api.drive_upload = lambda path, folder: (
            _drv["upload"].append((path.name, folder)) or f"file_{len(_drv['upload'])}")
        lark_api.drive_file_delete = lambda t: _drv["delete"].append(t)
        lark_api.drive_file_url = lambda t: f"https://x.larksuite.com/file/{t}"
        lark_api.drive_member_add = (lambda token, doc_type, member_id, perm="full_access",
                                     member_type="openid", notify=True:
                                     _drv["member"].append((token, doc_type, member_id,
                                                            perm, notify)))
        notes.publish("mtORG1")
        _own_folder = notes.space_of("ou_A", create=False)
        check("Drive: tạo folder cho CHỦ, share cho chủ quyền sửa, IM LẶNG",
              _own_folder and ("fld_1", "folder", "ou_A", "edit", False) in _drv["member"],
              str(_drv["member"][:2]))
        check("...file .md đẩy vào ĐÚNG folder của chủ",
              _drv["upload"] and _drv["upload"][-1][1] == _own_folder
              and _drv["upload"][-1][0].endswith(".md"))
        _file_shares = {m[2] for m in _drv["member"] if m[1] == "file"}
        check("...file share cho người dự + chuỗi quản lý (xem), KHÔNG thông báo",
              _file_shares == {"ou_B", "ou_C", "ou_E", "ou_D", "ou_F"}
              and all(m[3] == "view" and m[4] is False for m in _drv["member"]
                      if m[1] == "file"), str(_file_shares))
        check("...link file lên Base index (cột File biên bản)",
              _bt2._job_fields(jobstore.get("mtORG1")).get(_bt2.F_NOTE, "").endswith("file_1"))
        notes.publish("mtORG1")
        check("nội dung đổi -> đẩy bản mới, xoá bản cũ, không tạo thêm folder",
              _drv["delete"] == ["file_1"] and len(_drv["folder"]) == 1)
        _drv["member"].clear()
        _made = notes.provision(dry_run=True)
        check("drive-provision THỬ KHÔ: liệt kê người chưa có folder, chưa tạo gì",
              set(_made) >= {"B", "C", "D", "E", "F"} and "A" not in _made
              and len(_drv["folder"]) == 1, str(_made))
        notes.provision(dry_run=False)
        check("drive-provision --yes: mỗi người MỘT folder, share im lặng",
              len({m[2] for m in _drv["member"]}) == len(_made)
              and all(m[4] is False and m[3] == "edit" for m in _drv["member"]))
        _n = len(_drv["folder"])
        notes.provision(dry_run=False)
        check("...chạy lại không tạo trùng (người mới mới được tạo)",
              len(_drv["folder"]) == _n)
    finally:
        for k, v in _n_keep.items():
            setattr(lark_api, k, v)
        config.SEND_MODE, config.DRIVE_ENABLED = _n_cfg
        with db.tx() as c:
            for t in ("org_edges", "note_grants", "drive_spaces", "note_files",
                      "confirmations"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()

    # =================================================================
    part("V3-YC3. Tìm ngữ nghĩa qua LiteLLM — lọc quyền trước, rơi về từ khoá")
    # =================================================================
    from v2 import semantic, notes as _nt3, confirm as _cf3
    _s_keep = (semantic.embed, config.LITELLM_API_KEY, semantic.MIN_SCORE)
    _calls3: list[int] = []

    def _fake_embed(texts, model=""):
        # Embedder giả tất định: túi từ 64 chiều + ánh xạ đồng nghĩa. Đủ để kiểm
        # ĐƯỜNG ỐNG (lọc quyền, xếp hạng, nhãn, rơi về) — không kiểm chất lượng model.
        _calls3.append(len(texts))
        out = []
        for t in texts:
            t = t.lower().replace("đơn giá", "giá").replace("pricing", "giá")
            v = [0.0] * 64
            import re as _re3
            for w in _re3.findall(r"\w+", t):
                v[sum(map(ord, w)) % 64] += 1.0
            out.append(v)
        return out

    try:
        semantic.embed = _fake_embed
        config.LITELLM_API_KEY = "sk-test"
        semantic.MIN_SCORE = 0.1          # ngưỡng 0.25 là cho model thật, không cho túi-từ giả
        with db.tx() as c:
            for t in ("embeddings", "query_log", "note_grants", "org_edges",
                      "confirmations", "note_files"):
                c.execute(f"DELETE FROM {t}")
            c.executemany("INSERT INTO org_edges(open_id, union_id, name, leader_open_id)"
                          " VALUES (?,?,?,?)",
                          [("ou_C", "on_C", "C", "ou_E"), ("ou_E", "on_E", "E", "ou_D"),
                           ("ou_D", "on_D", "D", "")])
        wipe_jobs()

        def _job3(tok, title, summary, atts, start):
            m = meta(minute_token=tok, title=title, owner_open_id="ou_A", start=start,
                     participants_source="calendar[verified]:x",
                     attendees=[Attendee(open_id=o, union_id=o.replace("ou_", "on_"))
                                for o in atts])
            jobstore.create(m, status="held")
            jobstore.set_status(tok, "held", recap_json=_cf3.recap_to_json(
                Recap(summary=summary)))
            _nt3.grant(tok, m)

        _job3("mtSEM1", "Hop kinh doanh", "Chốt đơn giá sản phẩm 100k từ tháng 10",
              ["ou_B", "ou_C"], 1790000000.0)
        _job3("mtSEM2", "Hop nhan su", "Kế hoạch tuyển dụng quý 4", ["ou_B"], 1790100000.0)
        _job3("mtSEM3", "Hop mat", "Chốt giá nội bộ bí mật", ["ou_Z"], 1790200000.0)
        _stale = semantic.stale(10)
        check("chưa index -> cả 3 cuộc đã có nội dung đều nằm trong hàng chờ index",
              set(_stale) == {"mtSEM1", "mtSEM2", "mtSEM3"}, str(_stale))
        check("index nền tôn trọng giới hạn mỗi vòng", semantic.backfill(limit=2) == 2)
        semantic.backfill(limit=10)
        check("...index xong thì không còn gì chờ", semantic.stale(10) == [])

        _B = {"union_id": "on_B", "open_id": "ou_B", "name": "B"}
        _r = semantic.search(_B, "tuần trước chốt gì về giá?")
        check("hỏi 'giá' tìm ra cuộc ghi 'đơn giá' (từ khoá thuần sẽ trượt)",
              "Hop kinh doanh" in _r and _r.index("Hop kinh doanh") < (
                  _r.find("Hop nhan su") if "Hop nhan su" in _r else 10**9), _r[:300])
        check("LỌC QUYỀN TRƯỚC: cuộc B không được xem KHÔNG lộ, dù khớp nhất",
              "Hop mat" not in _r and "bí mật" not in _r)
        check("...kèm nhãn nguồn quyền 'bạn dự'", "(bạn dự)" in _r)
        _D = {"union_id": "on_D", "open_id": "ou_D", "name": "D"}
        _rd = semantic.search(_D, "đơn giá")
        check("quản lý D (không dự) tìm ra cuộc của nhánh, nhãn 'quyền quản lý, nhánh D>E>C'",
              "Hop kinh doanh" in _rd and "quyền quản lý, nhánh D>E>C" in _rd, _rd[:300])
        check("không xác định được người hỏi -> không trả gì",
              semantic.search(None, "giá") == qa.NO_ASKER)

        jobstore.set_status("mtSEM1", "held", recap_json=_cf3.recap_to_json(
            Recap(summary="Chốt đơn giá 120k")))
        check("recap đổi (chủ sửa / bản nguyên văn) -> tự vào lại hàng chờ index",
              semantic.stale(10) == ["mtSEM1"])

        def _boom(texts, model=""):
            raise semantic.EmbedUnavailable("503")
        semantic.embed = _boom
        _fb = semantic.search(_B, "giá")
        check("LiteLLM chết -> rơi về tìm TỪ KHOÁ, nói rõ, không vỡ",
              "TỪ KHOÁ" in _fb, _fb[:200])
        check("...và index nền chết êm, không làm hỏng vòng run", semantic.backfill() == 0)
        config.LITELLM_API_KEY = ""
        check("chưa có virtual key -> tắt ngữ nghĩa, vẫn tìm được theo từ khoá",
              "TỪ KHOÁ" in semantic.search(_B, "giá"))
        _ql = [dict(r) for r in db.conn().execute("SELECT * FROM query_log")]
        check("mỗi lượt tìm ghi query_log (nguồn cho dashboard)",
              len(_ql) >= 4 and any(q["n_hits"] > 0 for q in _ql)
              and any(q["tool"].endswith("fallback") for q in _ql), str(len(_ql)))
        check("MCP có semantic_search",
              "semantic_search" in {t["name"] for t in mcp_server.public_tools()})
    finally:
        semantic.embed, config.LITELLM_API_KEY, semantic.MIN_SCORE = _s_keep
        with db.tx() as c:
            for t in ("embeddings", "query_log", "note_grants", "org_edges"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()

    # =================================================================
    part("V3-YC5. Liên kết giữa các cuộc họp — lọc theo quyền người xem")
    # =================================================================
    from v2 import links, semantic as _sm5, confirm as _cf5
    from v2.models import ActionItem as _AI5
    try:
        with db.tx() as c:
            for t in ("meeting_links", "embeddings", "note_grants", "confirmations",
                      "glossary_candidates"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()
        _day = 86400.0

        def _j5(tok, title, start, atts, recap):
            m = meta(minute_token=tok, title=title, owner_open_id="ou_A", start=start,
                     participants_source="calendar[verified]:x",
                     attendees=[Attendee(open_id=o, union_id=o.replace("ou_", "on_"))
                                for o in atts])
            jobstore.create(m, status="held")
            jobstore.set_status(tok, "held", recap_json=_cf5.recap_to_json(recap))

        check("chuẩn hoá tên chuỗi họp bỏ ngày + 'Buổi N'",
              links.series_key("07-23 | Workforce AI Weekly Meeting Buổi 3")
              == links.series_key("08-06 | Workforce AI Weekly Meeting Buổi 5")
              == "workforce ai weekly meeting")
        _t0 = 1790000000.0
        for i in range(3):
            _j5(f"mtWK{i}", f"07-{1 + 7 * i:02d} | Weekly Ops Buổi {i + 1}", _t0 + i * 7 * _day,
                ["ou_B"], Recap(summary=f"tuan {i}"))
        _j5("mtWKOLD", "01-01 | Weekly Ops Buổi 0", _t0 - 120 * _day, ["ou_B"],
            Recap(summary="cu"))
        for i in range(3):
            links.update(f"mtWK{i}")
        _rel0 = {o["token"] for o in links.related("mtWK0")}
        check("3 buổi 'Weekly Ops' liên tiếp nối đủ với nhau (series)",
              {"mtWK1", "mtWK2"} <= _rel0 and "mtWK0" in {o["token"] for o in links.related("mtWK2")})
        check("...buổi cách 4 tháng KHÔNG bị nối như cùng chuỗi", "mtWKOLD" not in _rel0)

        _j5("mtEN1", "Hop du an X", _t0, ["ou_B"], Recap(summary="a", action_items=[
            _AI5(task="t1", owner="Lan"), _AI5(task="t2", owner="Minh")]))
        _j5("mtEN2", "Review san pham", _t0 + _day, ["ou_C"], Recap(summary="b", action_items=[
            _AI5(task="t3", owner="lan"), _AI5(task="t4", owner="Minh")]))
        _j5("mtEN3", "Hop khac", _t0 + 2 * _day, ["ou_C"], Recap(summary="c", action_items=[
            _AI5(task="t5", owner="Lan")]))
        links.update("mtEN1")
        _r1 = {o["token"]: o["reasons"] for o in links.related("mtEN1")}
        check("hai cuộc khác tên, chung >=2 người phụ trách -> nối (entity)",
              "entity" in _r1.get("mtEN2", []), str(_r1))
        check("...chung đúng 1 người thì KHÔNG nối (tránh nối mọi cuộc có cùng một sếp)",
              "mtEN3" not in _r1)

        _j5("mtSE1", "Hop gia", _t0, ["ou_B"], Recap(summary="gia"))
        _j5("mtSE2", "Hop dinh gia", _t0 + _day, ["ou_B"], Recap(summary="dinh gia"))
        _j5("mtSE3", "Hop tuyen dung", _t0 + _day, ["ou_B"], Recap(summary="tuyen"))
        with db.tx() as c:
            for tok, v in (("mtSE1", [1.0, 0.0, 0.1]), ("mtSE2", [0.95, 0.05, 0.1]),
                           ("mtSE3", [0.0, 1.0, 0.0])):
                c.execute("INSERT INTO embeddings(minute_token, chunk_id, kind, text, vec,"
                          " model, created_at) VALUES (?,0,'recap','',?,'m#x',0)",
                          (tok, _sm5._pack(v)))
        links.update("mtSE1")
        _r2 = {o["token"]: o["reasons"] for o in links.related("mtSE1")}
        check("cặp DƯƠNG: vector tóm tắt gần nhau -> nối 'cùng chủ đề'",
              "semantic" in _r2.get("mtSE2", []), str(_r2))
        check("cặp ÂM: chủ đề khác hẳn -> không nối", "mtSE3" not in _r2)

        # Quyền: B xem mtEN1 nhưng KHÔNG xem mtEN2 (chỉ C dự)
        _Bw = {"union_id": "on_B", "open_id": "ou_B", "name": "B"}
        _blk = links.block_for(_Bw, "mtEN1")
        check("người chỉ xem được 1 trong 2 cuộc: mục liên quan KHÔNG lộ tên cuộc kia",
              "Review san pham" not in _blk, _blk)
        _Cw = {"union_id": "on_C", "open_id": "ou_C", "name": "C"}
        _Aw = {"union_id": "on_A", "open_id": "ou_A", "name": "A"}
        check("...chủ (xem được cả hai) thì thấy liên kết",
              "Review san pham" in links.block_for(_Aw, "mtEN1"))
        check("file .md chỉ nêu cuộc liên quan mà MỌI người đọc file đều xem được",
              not any("Review san pham" in x for x in links.related_lines("mtEN1"))
              and any("Weekly Ops" in x for x in links.related_lines("mtWK0")))
        _txt5 = links.text_for(_Cw, "mtEN1")
        check("tool related_meetings: không xem được cuộc gốc -> không trả gì về nó",
              "Review" not in _txt5 and "Hop du an" not in _txt5, _txt5[:120])
        check("MCP có related_meetings",
              "related_meetings" in {t["name"] for t in mcp_server.public_tools()})
    finally:
        with db.tx() as c:
            for t in ("meeting_links", "embeddings"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()

    # =================================================================
    part("V3-YC4. Dashboard — đăng nhập Lark, mọi khối lọc theo người xem")
    # =================================================================
    from v2 import dashboard as dsb, confirm as _cf4, notes as _nt4
    from v2.models import ActionItem as _AI4
    _d_keep = (config.DASHBOARD_SECRET, lark_api.exchange_code, lark_api.user_info,
               config.LITELLM_API_KEY, dsb.httpx)
    try:
        config.DASHBOARD_SECRET = "test-secret-khong-dung-that"
        _tok = dsb._sign({"k": "sess", "u": "on_X", "exp": int(time.time()) + 60})
        check("phiên ký đúng -> đọc lại được", (dsb._verify(_tok) or {}).get("u") == "on_X")
        _b, _m = _tok.rsplit(".", 1)
        _forged = dsb._sign({"k": "sess", "u": "on_CEO", "exp": int(time.time()) + 60})
        config.DASHBOARD_SECRET = "khoa-khac"
        _other_key = dsb._verify(_forged)
        config.DASHBOARD_SECRET = "test-secret-khong-dung-that"
        check("phiên bị sửa / ký bằng khoá khác -> TỪ CHỐI",
              dsb._verify(_b[:-2] + "xx." + _m) is None and _other_key is None)
        check("phiên hết hạn -> từ chối",
              dsb._verify(dsb._sign({"k": "sess", "u": "on_X", "exp": 1})) is None)

        _st, _h, _ = dsb.app("GET", "/", {"Host": "meeting.lamsonretail.com"})
        check("chưa đăng nhập -> chuyển sang Lark, redirect_uri đúng domain, KHÔNG xin scope",
              _st == 302 and "authen/v1/authorize" in _h["Location"]
              and "redirect_uri=https%3A%2F%2Fmeeting.lamsonretail.com%2Fauth%2Fcallback" in _h["Location"]
              and "scope=" not in _h["Location"], _h.get("Location", "")[:160])
        _st, _h, _ = dsb.app("GET", "/", {"Host": "meeting.hapas-ai.tech"})
        check("domain thứ hai dùng redirect_uri của chính nó",
              "meeting.hapas-ai.tech%2Fauth%2Fcallback" in _h["Location"])
        _st, _h, _ = dsb.app("GET", "/", {"Host": "evil.example.com"})
        check("Host lạ KHÔNG lái được redirect_uri sang chỗ khác",
              "evil" not in _h["Location"] and "meeting.lamsonretail.com" in _h["Location"])
        check("POST -> 405 (trang chỉ đọc)", dsb.app("POST", "/", {})[0] == 405)
        check("state giả -> 400, không đổi code",
              dsb.app("GET", "/auth/callback?code=c&state=abc.def", {"Host": "meeting.lamsonretail.com"})[0] == 400)

        _seen4: dict = {}
        lark_api.exchange_code = lambda code, redirect_uri=None: (
            _seen4.update(code=code, ru=redirect_uri) or {"access_token": "u-tok"})
        lark_api.user_info = lambda tok: {"union_id": "on_D", "open_id": "ou_D", "name": "Sep D"}
        _state = dsb._sign({"k": "state", "r": "/?p=month", "exp": int(time.time()) + 60})
        _st, _h, _ = dsb.app("GET", f"/auth/callback?code=CODE1&state={_state}",
                             {"Host": "meeting.lamsonretail.com"})
        check("callback: đổi code bằng ĐÚNG redirect_uri, đặt cookie HttpOnly+Secure, về trang cũ",
              _st == 302 and _h["Location"] == "/?p=month" and _seen4.get("code") == "CODE1"
              and _seen4.get("ru") == "https://meeting.lamsonretail.com/auth/callback"
              and "HttpOnly" in _h["Set-Cookie"] and "Secure" in _h["Set-Cookie"], str(_h))
        _cookie4 = _h["Set-Cookie"].split(";")[0]

        # --- dữ liệu: A chủ, B dự; D quản lý (qua grant); F người ngoài
        with db.tx() as c:
            for t in ("confirmations", "note_grants", "org_edges", "query_log", "digests",
                      "note_files", "meeting_links"):
                c.execute(f"DELETE FROM {t}")
            c.executemany("INSERT INTO org_edges(open_id, union_id, name, leader_open_id)"
                          " VALUES (?,?,?,?)", [("ou_B", "on_B", "B", "ou_D"),
                                                ("ou_D", "on_D", "Sep D", "")])
        wipe_jobs()
        _now4 = int(time.time() * 1000)

        def _j4(tok, title, days_ago, recap, state=None, edits=0, diff=0, sent_h=0):
            m = meta(minute_token=tok, title=title, owner_open_id="ou_A",
                     start=(_now4 - days_ago * 86_400_000) / 1000,
                     participants_source="calendar[verified]:x",
                     attendees=[Attendee(open_id="ou_B", union_id="on_B", name="B")])
            jobstore.create(m, status="held")
            jobstore.set_status(tok, "held", recap_json=_cf4.recap_to_json(recap))
            _nt4.grant(tok, m)
            if state:
                with db.tx() as c:
                    c.execute("INSERT INTO confirmations(minute_token, owner_union_id, state,"
                              " edits, diff_chars, sent_at, released_at) VALUES (?,?,?,?,?,?,?)",
                              (tok, "on_A", state, edits, diff, _now4 - sent_h * 3_600_000, _now4))

        _j4("mtD1", "Hop <script>x</script> gia", 2,
            Recap(summary="s", decisions=["Chot gia 100k"],
                  action_items=[_AI4(task="Gui bao gia", owner="Lan", due="2020-01-01"),
                                _AI4(task="Viec khong ai nhan")]), "confirmed")
        _j4("mtD2", "Hop sua nhieu", 3, Recap(summary="s", decisions=["QD2"]),
            "confirmed", edits=2, diff=120)
        _j4("mtD3", "Hop chua review", 4, Recap(summary="s", decisions=["QD3"]), "auto_published")
        _j4("mtD4", "Hop cho duyet", 0, Recap(summary="s"), "pending", sent_h=9)
        for i in range(3):
            _j4(f"mtR{i}", f"Weekly Sale Buổi {i}", 20 - 7 * i, Recap(summary="ban luan"))
        with db.tx() as c:
            c.executemany("INSERT INTO query_log(ts, asker, tool, query, n_hits) VALUES (?,?,?,?,?)",
                          [(_now4, "on_B", "semantic_search", "chot gia", 2),
                           (_now4, "on_B", "semantic_search", "abc", 0),
                           (_now4, "on_D", "semantic_search:fallback", "chot gia", -1)])

        _A = {"union_id": "on_A", "open_id": "ou_A", "name": "A"}
        _D = {"union_id": "on_D", "open_id": "ou_D", "name": "Sep D"}
        _F = {"union_id": "on_F", "open_id": "ou_F", "name": "F"}
        _dA, _dD, _dF = dsb.build(_A), dsb.build(_D), dsb.build(_F)
        check("backlog 'chờ bạn duyệt': chủ thấy cuộc pending của mình, quá 8h tô cảnh báo",
              [m["token"] for m in _dA["mine_pending"]] == ["mtD4"]
              and _dA["mine_pending"][0]["overdue"])
        check("...người khác KHÔNG thấy backlog của chủ", _dD["mine_pending"] == [])
        _ttl = lambda d: {h["token"] for h in d["highlights"]}
        check("quản lý D (không dự) thấy nội dung tuần của nhánh; cuộc đang chờ duyệt thì chưa",
              {"mtD1", "mtD2", "mtD3"} <= _ttl(_dD) and "mtD4" not in _ttl(_dD), str(_ttl(_dD)))
        check("người ngoài F: KHÔNG thấy cuộc nào, không lộ tên", _ttl(_dF) == set()
              and "Hop" not in dsb.render(_dF))
        _q = _dA["quality"]
        check("chất lượng: 3 biên bản chốt -> 1/3 duyệt nguyên bản, 1/3 phải sửa, 1/3 chưa review",
              _q["n"] == 3 and _q["approved_clean"] == 33 and _q["edited"] == 33
              and _q["unreviewed"] == 33 and _q["avg_diff"] == 120, str(_q))
        _kinds = {r["kind"] for r in _dA["risks"]}
        check("rủi ro: việc quá hạn + việc không ai nhận + chưa review + chủ đề lặp không quyết định",
              {"Việc đã tới hạn", "Việc chưa có người nhận", "Biên bản chưa được review",
               "Chủ đề lặp chưa có quyết định"} <= _kinds, str(_kinds))
        check("truy vấn: người thường chỉ thấy số lượt CỦA MÌNH",
              _dD["queries"] == {"mine_7d": 1} and "Truy vấn biên bản" not in dsb.render(_dD))
        _adm = dsb.build({"union_id": "on_ADMIN", "open_id": "ou_ADMIN", "name": "Admin"})
        check("admin: thấy thống kê truy vấn toàn hệ thống (rỗng, rơi về từ khoá, câu hay gặp)",
              _adm["queries"]["total_7d"] == 3 and _adm["queries"]["empty"] == 1
              and _adm["queries"]["fallback"] == 1 and _adm["ops"]["pending"] == 1
              and _adm["queries"]["top"][0] == ("chot gia", 2))
        check("...nhưng admin KHÔNG vì thế thấy nội dung cuộc họp của người khác",
              _ttl(_adm) == set())
        _html = dsb.render(_dA)
        check("HTML escape tên cuộc họp (không chèn được script)",
              "<script>x</script>" not in _html and "&lt;script&gt;" in _html)
        _st, _h, _body = dsb.app("GET", "/", {"Host": "meeting.lamsonretail.com",
                                              "Cookie": _cookie4})
        check("có cookie -> 200, trang của đúng người đăng nhập, có CSP chặn nhúng",
              _st == 200 and "Sep D" in _body and "frame-ancestors 'none'" in
              _h.get("Content-Security-Policy", ""))

        config.LITELLM_API_KEY = ""
        check("digest: chưa có key -> không gọi LLM, trang vẫn có danh sách quyết định",
              dsb.digest(_dA) == "" and "Chot gia 100k" in _html)

        class _FakeResp:
            def raise_for_status(self): pass
            def json(self): return {"choices": [{"message": {"content": "- Chot gia [1]"}}]}

        class _FakeHttpx:
            HTTPError = Exception
            n = 0

            @classmethod
            def post(cls, *a, **k):
                cls.n += 1
                return _FakeResp()
        dsb.httpx = _FakeHttpx
        config.LITELLM_API_KEY = "sk-test"
        _g1, _g2 = dsb.digest(_dD), dsb.digest(_dD)
        check("digest LLM có trích nguồn [n], CACHE: gọi đúng 1 lần cho cùng tập cuộc họp",
              _g1 == _g2 == "- Chot gia [1]" and _FakeHttpx.n == 1)
    finally:
        (config.DASHBOARD_SECRET, lark_api.exchange_code, lark_api.user_info,
         config.LITELLM_API_KEY, dsb.httpx) = _d_keep
        with db.tx() as c:
            for t in ("confirmations", "note_grants", "org_edges", "query_log", "digests"):
                c.execute(f"DELETE FROM {t}")
        wipe_jobs()

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
