# Tài liệu kỹ thuật — Luồng tự động Meeting Note V2 (Lark × Whisper)

> Cập nhật mới nhất: **12/08/2026**<br>
> Repo: `D:\MeetingxLark`<br>
> Tài liệu này mô tả toàn bộ kiến trúc V2, máy trạng thái job, chuỗi xác minh quyền tham dự, hạ tầng OAuth Cloudflare, luồng hỏi đáp QA và các trần quyền API của Lark. Mọi chi tiết đều đã được kiểm chứng bằng dữ liệu thật và selftest (**730 PASS / 0 FAIL**).

---

## 0. Bảng tổng hợp toàn bộ luồng V2 hệ thống

| Tình huống / Sự kiện | Điều kiện kích hoạt | Xử lý kỹ thuật nội bộ | Kết quả / Người nhận | Quy tắc ACL & Bảo mật |
|---|---|---|---|---|
| **Phát hiện cuộc họp mới** | `scan_once()` quét `minutes_list()`, minute chưa có trong DB | Tự sinh mốc `first_seen`, chờ 3 phút (settle), gọi `try_claim_minute()` atomic | Tạo job `status=queued, priority=1` | Nguồn `calendar[near...]` bị loại hoàn toàn (fail-closed) |
| **Gửi thẻ DM "Họp xong"** | Cuộc họp mới (`notify=True`) | Giao `attendees ∩ enrolled_users`. Gọi `larktext.fetch()` thử thang token (`owner`→`attendees`→`viewers`) | Gửi thẻ DM cho người dự đã enroll. Có tóm tắt nếu chủ đã enroll; nghèo (link) nếu chủ chưa enroll | Người chưa enroll bị **bỏ qua** (skipped, 0 DM, 0 auth link) |
| **Chủ phòng chưa Enroll** | Minute được tạo bởi người dùng chưa nhắn bot | API Lark `minutes_transcript` trả 403 `2091005` cho token người dự | Job chuyển sang `waiting_auth` (parked), không retry nóng | Không dùng `minute_viewers` để cấp quyền |
| **Người dùng nhắn bot lần đầu** | Nhắn 1-1 ("Alo") và chưa có token trong DB | `gate.check()` trả `invite`. Sinh nonce TTL 24h, tạo short link OAuth | Gửi 1 thẻ chứa link OAuth 1-1 | Không tự gửi link auth chủ động cho bất kỳ ai khác |
| **Người dùng hoàn tất OAuth** | Đồng ý trên màn hình Lark | Cloudflare Worker đẩy callback vào Queue. `complete()` lưu token, gọi `reverify_after_enroll()`, `release_waiting_auth()`, `welcome_and_backlog()` | Đánh thức job `waiting_auth` → `queued (priority=0)`. Gửi thẻ chào 7 ngày | Backlog 90 ngày nạp im lặng (`notify=False`) |
| **Xử lý phiên âm nền (Whisper)** | Job ở `status=queued` trong `process_queue()` | Tải MP4 → ffmpeg ra WAV 16kHz PCM → Whisper medium (CPU) + glossary prompt → LLM recap | Lưu transcript `.json` & `.docx`, ghi Bitable Base, job sang `held` | Mô hình "Kéo" (Pull): **Không tự gửi file vào chat** |
| **Hỏi đáp danh sách cuộc họp** | Người dùng nhắn "Liệt kê cuộc họp" | MCP gọi `qa.list_meetings()`. Lọc hiển thị qua `_may_see()`. Đóng gói mốc `SEND_MARK` | Trả bảng danh sách (tối đa 12 dòng / 3.500 ký tự) | Chốt cứng 1 câu hỏi = 1 tin nhắn |
| **Hỏi chi tiết cuộc họp** | Người dùng hỏi "Cuộc họp X bàn gì?" | MCP gọi `qa.get_meeting()`. Lấy bản Lark/Hapas. Trả mốc `CONTEXT_MARK` & `OFFER_MARK` | Bot trả lời bằng bản Lark/Hapas + mời lấy file Word bản Hapas | Cổng `_claims_data` chặn agent bịa dữ liệu nếu không gọi tool |
| **Yêu cầu gửi file transcript** | Người dùng đồng ý ("Có", "Gửi đi") sau lời mời | Plugin nhận diện `_is_yes()`, mở cổng write-tool. Gọi `sendfile.send_transcript_file()` | Ghi `db.request_transcript()`, nâng `priority=2` | Bot tự gửi file `.docx` ngay khi Whisper xử lý xong |


### Sơ đồ Mermaid tổng quát toàn bộ luồng hệ thống (4 giai đoạn)

```mermaid
flowchart TD
    subgraph STAGE1["1️⃣ HỌP XONG & PHÁT HIỆN (Discovery & Verification)"]
        A["🎬 Cuộc họp kết thúc trên Lark"] --> B["Lark tự sinh bản chép (Minute)"]
        B --> C["Bot quét & chờ 3 phút (Settle)\nĐối chiếu sự kiện lịch + phòng VC"]
        C --> D["Lập danh sách Người tham dự (Attendees)\nxác minh qua chuỗi Calendar ↔ VC"]
    end

    subgraph STAGE2["2️⃣ PHÂN LUỒNG THÔNG BÁO TỰ ĐỘNG (Auto DM Notice)"]
        D --> E{"Người tham dự\nĐÃ ENROLL (OAuth)?"}
        E -- "Chưa Enroll" --> F["🚫 Bỏ qua (Skipped)\nKhông gửi DM, không gửi link auth"]
        E -- "Đã Enroll" --> G{"Chủ phòng (Owner)\nĐÃ ENROLL?"}

        G -- "Chủ phòng ĐÃ Enroll" --> H["✅ Bot mượn token Chủ đọc bản chép Lark\n→ LLM local tóm tắt nội dung"]
        G -- "Chủ phòng CHƯA Enroll" --> I["⚠️ Bot không đọc được text từ API\n(Trần quyền 403 của Lark)"]

        H --> J["📩 DM THẺ 'HỌP XONG' ĐẦY ĐỦ:\nTóm tắt + Decision + Action + Link + Lời mời"]
        I --> K["📩 DM THẺ 'HỌP XONG' NGHÈO:\nChỉ Tiêu đề + Link họp (không có tóm tắt)"]
    end

    subgraph STAGE3["3️⃣ PHIÊN ÂM WHISPER CHẠY NỀN (Background Processing — Pull Model)"]
        C --> L["🎧 Tải MP4 → ffmpeg ra WAV 16kHz PCM Lossless"]
        L --> M["🤖 Whisper Medium phiên âm → LLM recap tóm tắt"]
        M --> N["💾 Lưu vào SQLite & Bitable Base\nTrạng thái: HELD (chờ người dùng hỏi)"]
    end

    subgraph STAGE4["4️⃣ BƯỚC NGƯỜI DÙNG CHAT VỚI BOT (User Q&A & File Delivery)"]
        U["👤 Người dùng nhắn bot (Chat 1-1)"] --> GATE{"Đã Enroll?"}
        GATE -- "Chưa" --> INVITE["📋 Gửi 1 link OAuth"]
        GATE -- "Rồi" --> QA["🤖 Thư Ký tra cứu dữ liệu (qa.py)"]

        QA --> REPO{"Dữ liệu hiện có?"}
        REPO -- "Đã có bản Hapas" --> RESP1["Đưa bản Lark trả lời ngay\n+ Mời nhận LUÔN bản Hapas"]
        REPO -- "Đang chờ Whisper" --> RESP2["Đưa bản Lark trả lời ngay\n+ Mời nhận bản Hapas (~X phút)"]
        REPO -- "Không có bản nào" --> RESP3["Báo tình trạng thật + bước tiếp theo"]

        RESP1 & RESP2 --> ASK{"Người dùng đáp 'Có'?"}
        ASK -- "Có" --> SEND_FILE["⚡ Nâng ưu tiên (Priority 2)\nTự động gửi file .docx khi xong"]
    end

    style STAGE1 fill:#e3f2fd,stroke:#1565c0
    style STAGE2 fill:#e8f5e9,stroke:#2e7d32
    style STAGE3 fill:#fff9c4,stroke:#f9a825
    style STAGE4 fill:#f3e5f5,stroke:#7b1fa2
    style J fill:#c8e6c9,stroke:#1b5e20
    style K fill:#ffe0b2,stroke:#e65100
    style F fill:#ffcdd2,stroke:#b71c1c
```

---

## 1. Kiến trúc tổng thể V2

Hệ thống V2 là một orchestrator đơn tiến trình (single-process) kết hợp cùng **SQLite mã hoá** (Fernet key), **Hermes Gateway** (kênh WebSocket chat 1-1) và **Cloudflare Worker + Queue** (xử lý OAuth callback).

### Sơ đồ ASCII (Xem trực tiếp trong bất kỳ trình đọc text nào)

```text
 +-----------------------------------------------------------------------------------+
 |                                 ☁️ LARK CLOUD                                      |
 |  [Minutes API]  [Calendar API]  [VC Recording API]  [IM Send API]  [Bitable Base] |
 +----------------------------------------+------------------------------------------+
                                          |
 +----------------------------------------v------------------------------------------+
 |                            ☁️ CLOUDFLARE INFRASTRUCTURE                           |
 |  Worker Callback (OAuth)  --->  Cloudflare Queue: meetingxlark-oauth              |
 +----------------------------------------+------------------------------------------+
                                          |
 +----------------------------------------v------------------------------------------+
 |                      🖥️ MÁY LOCAL (D:\MeetingxLark)                               |
 |                                                                                   |
 |  +-----------------------------------------------------------------------------+  |
 |  | ORCHESTRATOR (1 tiến trình duy nhất)                                        |  |
 |  | • scan_once()          : Quét Minutes, Calendar, VC                         |  |
 |  | • process_queue()      : Tải MP4 -> ffmpeg (WAV) -> Whisper (CPU) -> Recap |  |
 |  | • oauth.poll_pending() : Kéo Queue Cloudflare, lưu Token                      |  |
 |  | • alerts & backup      : Heartbeat 60s, Auto backup SQLite                  |  |
 |  +-------------------------------------+---------------------------------------+  |
 |                                        |                                          |
 |  +-------------------------------------+---------------------------------------+  |
 |  | DỮ LIỆU LOCAL                                                               |  |
 |  | • SQLite mã hoá (Fernet) : jobs, tokens, oauth_nonce, chat_memory             |  |
 |  | • Disk File System       : transcripts/*.docx, lark-*.txt                     |  |
 |  +-------------------------------------+---------------------------------------+  |
 |                                        |                                          |
 |  +-------------------------------------+---------------------------------------+  |
 |  | HERMES GATEWAY (Chat 1-1 qua WebSocket)                                     |  |
 |  | • v2-enroll-gate plugin  : Kiểm tra danh tính & cấp ticket                  |  |
 |  | • MCP Server (qa, send)  : Tra cứu dữ liệu họp & phát file .docx            |  |
 |  +-----------------------------------------------------------------------------+  |
 +-----------------------------------------------------------------------------------+
```

### Sơ đồ Mermaid đồ hoạ

```mermaid
graph TB
    subgraph LARK["☁️ Lark Cloud"]
        LARK_MIN["Minutes API"]
        LARK_CAL["Calendar API"]
        LARK_VC["VC Recording API"]
        LARK_IM["IM API (gửi tin)"]
        LARK_BASE["Bitable Base"]
        LARK_OAUTH["OAuth Server"]
    end

    subgraph CF["☁️ Cloudflare"]
        CF_WORKER["Worker callback"]
        CF_QUEUE["Queue: meetingxlark-oauth"]
    end

    subgraph LOCAL["🖥️ Máy local (D:\MeetingxLark)"]
        subgraph ORCH["Orchestrator (1 tiến trình)"]
            SCAN["scan_once()"]
            QUEUE["process_queue()"]
            POLL_OAUTH["oauth.poll_pending()"]
            HEARTBEAT["heartbeat + alerts"]
            BACKUP["auto backup"]
        end

        subgraph PIPELINE["Pipeline (trong orchestrator)"]
            DL["Tải MP4"]
            FFMPEG["ffmpeg: MP4 → WAV 16kHz"]
            WHISPER["Whisper medium (CPU)"]
            LLM_RECAP["LLM recap (local)"]
        end

        DB[("SQLite mã hoá\njobs, tokens,\noauth_nonce,\nchat_memory")]
        DISK["📁 transcripts/\n*.json, *.docx,\nlark-*.txt"]

        subgraph HERMES["Hermes Gateway"]
            WS["WebSocket ↔ Lark"]
            PLUGIN["Plugin v2-enroll-gate"]
            MCP["MCP Server (qa, sendfile)"]
        end

        GATE["gate.py\n(quyết định cho/chặn)"]
    end

    subgraph USER["👤 Người dùng"]
        CHAT["Chat 1-1 với bot"]
        OAUTH_CONSENT["Đồng ý OAuth"]
    end

    %% Luồng phát hiện cuộc họp
    SCAN -->|"minutes_list()"| LARK_MIN
    SCAN -->|"calendar_events()"| LARK_CAL
    SCAN -->|"vc_recordings()"| LARK_VC
    SCAN -->|"tạo job"| DB

    %% Luồng xử lý
    QUEUE -->|"đọc job"| DB
    QUEUE --> DL
    DL -->|"minutes_media()"| LARK_VC
    DL --> FFMPEG --> WHISPER --> LLM_RECAP
    LLM_RECAP -->|"lưu transcript"| DISK
    LLM_RECAP -->|"cập nhật status"| DB
    QUEUE -->|"ghi Base record"| LARK_BASE

    %% Luồng thông báo
    SCAN -->|"thẻ Họp xong"| LARK_IM

    %% Luồng OAuth
    OAUTH_CONSENT --> LARK_OAUTH --> CF_WORKER --> CF_QUEUE
    POLL_OAUTH -->|"pull + ACK"| CF_QUEUE
    POLL_OAUTH -->|"lưu token"| DB

    %% Luồng chat
    CHAT --> WS
    WS --> PLUGIN
    PLUGIN --> GATE
    GATE -->|"kiểm token"| DB
    PLUGIN --> MCP
    MCP -->|"qa / sendfile"| DB
    MCP -->|"đọc transcript"| DISK
    MCP -->|"trả lời"| WS --> CHAT

    %% Luồng gửi file
    MCP -->|"gửi .docx"| LARK_IM --> CHAT

    style LARK fill:#e3f2fd,stroke:#1565c0
    style CF fill:#fff3e0,stroke:#e65100
    style LOCAL fill:#f1f8e9,stroke:#33691e
    style USER fill:#fce4ec,stroke:#b71c1c
```

---

## 2. Vòng lặp chính Orchestrator

```text
 [Khởi động] --> db.init() --> thread Heartbeat (60s)
      |
      v
 +--> [Đầu vòng lặp while True]
 |     - อ่าน .env (công tắc PAUSED)
 |     - oauth.poll_pending() (kéo Queue Cloudflare)
 |
 |    {PAUSED = True?} --Có--> alerts.check_all() --+
 |           |                                      |
 |          Không                                   |
 |           v                                      |
 |     - whisper_supervisor.ensure_running()        |
 |     - scan_once() (quét Minutes 7 ngày)          |
 |     - _backfill_base()                           |
 |     - process_queue() (xử lý hàng đợi job)       |
 |           |                                      |
 |           +--------------------------------------+
 |           v
 |     - alerts.check_all() & backup.maybe_backup()
 |     - _sleep_with_fast_enroll(300s) (ngủ 5 phút, nhưng poll mỗi 5s nếu có ai enroll)
 +-----------+
```

```mermaid
flowchart TD
    START(["🟢 Khởi động"])
    INIT["db.init() + config.ensure_dirs()"]
    HB["Bắt đầu thread heartbeat\n(mỗi 60s ghi mốc sống)"]

    LOOP_START{"while True"}
    RELOAD["config.reload_switches()\n(đọc PAUSED từ .env)"]
    OAUTH_POLL["oauth.poll_pending()\nKéo hộp thư Cloudflare Queue"]

    PAUSED_CHECK{"PAUSED?"}
    WHISPER_CHECK["whisper_supervisor.ensure_running()"]
    SCAN["scan_once()\nQuét Minutes cho mọi user enrolled"]
    BACKFILL_BASE["_backfill_base()\nBổ sung record Base còn thiếu"]
    PROCESS["process_queue()\nXử lý hàng đợi job"]

    ALERTS["alerts.check_all()\nĐánh giá sức khoẻ hệ thống"]
    BACKUP["backup.maybe_backup()\nSao lưu DB nếu tới lịch"]
    PUSH["status_push.heartbeat()\n(mỗi 2 tiếng)"]

    SLEEP["_sleep_with_fast_enroll(300s)\nNgủ 5 phút, nhưng poll OAuth\nmỗi 5s nếu có người đang enroll"]

    START --> INIT --> HB --> LOOP_START
    LOOP_START --> RELOAD --> OAUTH_POLL --> PAUSED_CHECK

    PAUSED_CHECK -- "Không" --> WHISPER_CHECK --> SCAN --> BACKFILL_BASE --> PROCESS
    PAUSED_CHECK -- "Có (khẩn cấp)" --> ALERTS

    PROCESS --> ALERTS --> BACKUP --> PUSH --> SLEEP --> LOOP_START
```

---

## 3. Luồng phát hiện cuộc họp → Tạo job → Thông báo

```text
  scan_once()
    |
    v
  [Quét Minutes 7 ngày của User Enrolled]
    |
    +--> Minute đã tồn tại? --Có--> Ghi viewer kỹ thuật (nếu đúng attendee)
    |
    +--> Minute MỚI ---> Chờ đủ 3 phút (settle)
                            |
                            v
                         gọi try_claim_minute() (khoá atomic)
                            |
                            v
                         meetings.resolve_participants() (xác minh Calendar/VC)
                            |
                            v
                         jobstore.create(status=queued, priority=1)
                            |
                            v
                         _notify_minute() (larktext.fetch -> LLM tóm tắt)
                            |
                            v
                         Gửi thẻ DM "Họp xong" cho người dự ĐÃ ENROLL
```

```mermaid
flowchart TD
    SCAN_START(["scan_once()"])
    GET_USERS["Lấy danh sách user enrolled\ntokenstore.list_users(active_only=True)"]
    LOOP_USER{"Lặp mỗi user"}

    MINUTES_LIST["lark_api.minutes_list()\nQuét Minutes 7 ngày gần nhất"]

    LOOP_MINUTE{"Lặp mỗi minute"}
    ALREADY{"Đã có\ntrong DB?"}
    NOTE_VIEWER["_note_verified_viewer()\nGhi nhận viewer KỸ THUẬT\n(nếu là owner/attendee thật)"]

    SETTLE{"Đã chờ\n≥ 3 phút?"}
    NOTE_SEEN["db.note_seen()\nGhi mốc thấy lần đầu"]

    CLAIM["db.try_claim_minute()\n(khoá atomic)"]
    BUILD_META["meetings.build_meta()\nĐọc metadata cuộc họp"]
    RESOLVE["meetings.resolve_participants()\nXác minh người tham dự"]

    HAS_ATTENDEES{"Có attendee?"}
    FALLBACK{"owner_open_id\n== reader?"}
    FALLBACK_OWNER["Dùng chủ bản ghi\nlàm attendee duy nhất"]
    NO_ATTENDEE["Không có attendee\n(cuộc không qua Calendar)"]

    CREATE_JOB["jobstore.create()\nstatus=queued, priority=1"]

    NOTIFY{"notify=True?"}
    NOTIFY_FLOW["_notify_minute()"]
    GET_LARK["larktext.fetch()\nThử thang ứng viên:\nchủ → dự → viewer"]
    RECAP_LLM["summarize.recap_from_text()\nLLM tóm tắt bản Lark"]
    BUILD_CARD["cards.minute_notice_card()\nThẻ: tóm tắt + link + lời mời"]

    RECIPIENTS["_recipients()\nGiao attendees ∩ enrolled users"]
    SEND_CARD["lark_api.im_send_card()\nGửi thẻ cho từng recipient"]

    SCAN_START --> GET_USERS --> LOOP_USER
    LOOP_USER --> MINUTES_LIST --> LOOP_MINUTE
    LOOP_MINUTE --> ALREADY

    ALREADY -- "Đã có" --> NOTE_VIEWER --> LOOP_MINUTE
    ALREADY -- "Chưa" --> SETTLE

    SETTLE -- "Chưa đủ 3 phút" --> NOTE_SEEN --> LOOP_MINUTE
    SETTLE -- "Đã đủ" --> CLAIM --> BUILD_META --> RESOLVE

    RESOLVE --> HAS_ATTENDEES
    HAS_ATTENDEES -- "Có" --> CREATE_JOB
    HAS_ATTENDEES -- "Không" --> FALLBACK
    FALLBACK -- "Đúng owner" --> FALLBACK_OWNER --> CREATE_JOB
    FALLBACK -- "Không rõ" --> NO_ATTENDEE --> CREATE_JOB

    CREATE_JOB --> NOTIFY
    NOTIFY -- "Có (cuộc mới)" --> NOTIFY_FLOW
    NOTIFY -- "Không (backlog)" --> LOOP_MINUTE

    NOTIFY_FLOW --> RECIPIENTS --> GET_LARK --> RECAP_LLM --> BUILD_CARD --> SEND_CARD --> LOOP_MINUTE

    LOOP_MINUTE -- "Hết minute" --> LOOP_USER
    LOOP_USER -- "Hết user" --> END(["Xong scan"])

    style NOTIFY_FLOW fill:#e8f5e9,stroke:#2e7d32
    style SEND_CARD fill:#c8e6c9,stroke:#1b5e20
```

---

## 4. Máy trạng thái Job (Job State Machine)

```text
 [ enqueue_minute() ]
          |
          v
      ( queued ) <-----------------------+
        /   |  \                         |
       /    |   +---> ( waiting_auth ) --+ (release_waiting_auth khi có OAuth)
      /     v
     /  [ transcribing ]
    /     /      \
   /     v        v
  |  (failed)   [ recapping ]
  |                 /     \
  v                v       v
 (failed)       (held)   (delivered)
              (pull Q&A) (đã phát file)
```

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue_minute()

    queued --> transcribing: pipeline.run_transcription()
    queued --> waiting_auth: WaitingForAuth\n(không ai tải được media)
    queued --> failed: attempts > 5

    transcribing --> recapping: Whisper xong\n(lưu transcript_path)
    transcribing --> failed: MediaDenied\n(chủ enrolled nhưng bị từ chối)
    transcribing --> failed: EmptyTranscript\n(0 chữ, audio im lặng)
    transcribing --> queued: TranscribeUnavailable\n(Whisper tạm chết, thử lại)

    recapping --> held: Pull model\n(chờ người dùng hỏi)
    recapping --> delivered: priority ≥ 2\n(người dùng đã yêu cầu)
    recapping --> queued: LLM recap hỏng\n(recap_fails < 3, thử lại)

    waiting_auth --> queued: release_waiting_auth()\n(owner/attendee vừa OAuth)

    held --> delivered: Người dùng gõ\n"gửi bản dịch ..."

    state held {
        [*] --> Trên_đĩa: transcript .json + .docx
        Trên_đĩa --> Trên_Base: Base record đã ghi
        Trên_Base --> Chờ_hỏi: Sẵn sàng phục vụ Q&A
    }
```

---

## 5. Chuỗi xác minh người tham dự

```text
                      [ resolve_participants() ]
                                   |
                                   v
             [ Lấy các sự kiện lịch quanh ±3 giờ họp ]
                                   |
                                   v
            +---------------------------------------------+
            | Mức 1: VERIFIED (Mạnh nhất)                 |
            | VC recording có minute_token trùng chính xác |
            | --> Lấy danh sách mời + VC joiners (+vcN)    |
            +----------------------+----------------------+
                                   | (Nếu không khớp VC)
                                   v
            +---------------------------------------------+
            | Mức 2: TITLE (Vừa)                          |
            | Tên sự kiện lịch khớp CHÍNH XÁC tên minute  |
            | --> Lấy danh sách người được mời            |
            +----------------------+----------------------+
                                   | (Nếu chỉ gần giờ)
                                   v
            +---------------------------------------------+
            | Mức 3: REJECTED (Chặn)                      |
            | Chỉ gần giờ, tên lệch, VC lệch              |
            | --> BỎ QUA HOÀN TOÀN (Fail-closed)           |
            +---------------------------------------------+
```

```mermaid
flowchart TD
    START(["resolve_participants()"])
    NO_START{"meta.start\ncó giá trị?"}
    NO_TIME["Ghi 'no_start_time'\nKhông có attendee"]

    CAL_PRIMARY["lark_api.calendar_primary()\nLấy lịch chính của user"]
    CAL_EVENTS["lark_api.calendar_events()\nQuét sự kiện ± 3 giờ quanh\nthời điểm bắt đầu cuộc họp"]

    RANK["Xếp hạng sự kiện:\n1. Trùng tên chính xác\n2. Khoảng cách thời gian nhỏ nhất"]

    LOOP_EVENT{"Lặp từng\nsự kiện ứng viên"}

    GET_MID["lark_api.event_meeting_ids()\nLấy meeting_id từ sự kiện lịch"]

    MATCH_REC{"_recording_matches()\nVC recording có\nminute_token trùng?"}

    VERIFIED["✅ how = 'verified'\ncalendar[verified]:tên_cuộc_họp"]
    TITLE_CHECK{"Tên sự kiện\nkhớp chính xác\ntên cuộc họp?"}
    TITLE_MATCH["⚠️ how = 'title'\ncalendar[title]:tên_cuộc_họp"]
    NEAR_ONLY["❌ Chỉ gần giờ\n→ BỎ QUA sự kiện này\n(fail-closed)"]

    GET_ATTENDEES["lark_api.event_attendees()\nLấy danh sách người được mời"]
    FILTER_RSVP["Lọc bỏ RSVP = 'decline'"]
    MAP_IDS["_attendee_map()\nGhép union_id + open_id\ntheo attendee_id (KHÔNG theo index)"]

    VC_JOINERS{"how == 'verified'?"}
    GET_VC_PARTICIPANTS["lark_api.vc_meeting_participants()\nLấy người thật sự tham gia VC"]
    MERGE_VC["Gộp VC joiners chưa có\ntrong danh sách mời (+vcN)"]

    SET_META["Ghi meta.attendees\nGhi meta.participants_source"]

    NO_START -- "Không" --> NO_TIME
    START --> NO_START
    NO_START -- "Có" --> CAL_PRIMARY --> CAL_EVENTS --> RANK --> LOOP_EVENT
    LOOP_EVENT --> GET_MID --> MATCH_REC

    MATCH_REC -- "Khớp ✅" --> VERIFIED --> GET_ATTENDEES
    MATCH_REC -- "Không khớp" --> TITLE_CHECK
    TITLE_CHECK -- "Khớp tên" --> TITLE_MATCH --> GET_ATTENDEES
    TITLE_CHECK -- "Chỉ gần giờ" --> NEAR_ONLY --> LOOP_EVENT

    GET_ATTENDEES --> FILTER_RSVP --> MAP_IDS --> VC_JOINERS
    VC_JOINERS -- "Verified" --> GET_VC_PARTICIPANTS --> MERGE_VC --> SET_META
    VC_JOINERS -- "Title only" --> SET_META

    LOOP_EVENT -- "Hết sự kiện\nkhông khớp" --> NO_TIME

    style VERIFIED fill:#c8e6c9,stroke:#2e7d32
    style TITLE_MATCH fill:#fff9c4,stroke:#f9a825
    style NEAR_ONLY fill:#ffcdd2,stroke:#c62828
```

---

## 6. Mô hình định danh của Lark (union_id vs open_id vs user_id)

| Loại ID | Định dạng | Phạm vi (Scope) | Sử dụng trong hệ thống |
|---|---|---|---|
| `union_id` | `on_...` | **Toàn tenant (xuyên app)** | **Định danh chính cho người dùng** (IM delivery, token mapping, ACL) |
| `open_id` | `ou_...` | Chỉ trong 1 app cụ thể | Gọi API cấp app (Minute/Calendar scope) |
| `user_id` | Chuỗi mã NV | Toàn công ty | Không dùng (yêu cầu quyền Danh bạ dư thừa) |

---

## 7. Luồng OAuth đăng ký (Cloudflare Worker + Queue)

```text
 User nhắn bot ("Alo")
   |
   v
 gate.check() ---> Chưa enroll ---> Thẻ đăng ký (chứa Short Link OAuth)
                                            |
                                            v
                                User bấm đồng ý trên Lark
                                            |
                                            v
                              Cloudflare Worker Callback
                                            |
                                            v
                              Cloudflare Queue (meetingxlark-oauth)
                                            |
                                            v
                              Orchestrator poll_pending() & complete()
                                            |
                                            v
                              Lưu token + reverify_after_enroll()
                              + release_waiting_auth()
                              + welcome_and_backlog() (Thẻ chào 7 ngày)
```

```mermaid
sequenceDiagram
    actor User as 👤 Người dùng
    participant Bot as 🤖 Lark Bot (Hermes)
    participant Gate as gate.py
    participant OAuth as oauth.py
    participant CF as ☁️ Cloudflare Worker + Queue
    participant Lark as ☁️ Lark OAuth
    participant Orch as Orchestrator
    participant DB as 💾 SQLite

    Note over User,DB: Bước 1 — Người dùng nhắn bot lần đầu
    User->>Bot: "Alo" (chat 1-1)
    Bot->>Gate: check(union_id)
    Gate->>DB: _enrolled(union_id)?
    DB-->>Gate: Chưa có token
    Gate->>OAuth: poll_pending() (kiểm lần cuối)
    OAuth-->>Gate: Không có OAuth mới
    Gate->>OAuth: start()
    OAuth->>DB: Tạo nonce (TTL 24h)
    OAuth-->>Gate: link OAuth + nonce
    Gate->>Bot: decision="invite"
    Bot->>User: 📋 Thẻ đăng ký (có link OAuth)

    Note over User,DB: Bước 2 — Người dùng đồng ý
    User->>Lark: Bấm link → đồng ý cấp quyền
    Lark->>CF: Redirect callback(code, state)
    CF->>CF: Worker đẩy {code, state} vào Queue

    Note over User,DB: Bước 3 — Orchestrator kéo Queue
    Orch->>CF: poll_pending() → pull messages
    CF-->>Orch: {code, state}
    Orch->>OAuth: complete(code, state)
    OAuth->>DB: Kiểm nonce hợp lệ
    OAuth->>Lark: exchange_code(code) → access_token + refresh_token
    OAuth->>DB: tokenstore.enroll() (mã hoá + lưu)

    Note over User,DB: Bước 4 — Tái xác minh + đánh thức
    OAuth->>Orch: reverify_after_enroll(info)
    Note right of Orch: Kiểm lại job cũ:<br/>1. Minute trong search của user?<br/>2. Recording khớp minute_token?<br/>3. User có trong VC attendees?<br/>→ Chỉ update nếu CẢ BA đúng

    OAuth->>DB: release_waiting_auth(open_id, union_id)
    Note right of DB: Chỉ đánh thức job mà<br/>user là owner HOẶC attendee.<br/>KHÔNG dùng minute_viewers.

    Note over User,DB: Bước 5 — Chào mừng + backlog
    Orch->>Orch: welcome_and_backlog(info)
    Orch->>DB: Quét 90 ngày → tạo backlog (priority=0, notify=False)
    Orch->>User: 📋 Thẻ chào (liệt kê cuộc họp 7 ngày)

    Note over User,DB: Bước 6 — Tin nhắn tiếp theo
    User->>Bot: "Liệt kê cuộc họp của tôi"
    Bot->>Gate: check(union_id)
    Gate->>DB: _enrolled(union_id)?
    DB-->>Gate: ✅ Có token
    Gate-->>Bot: decision="allow" + asker_token + profile + memory
    Bot->>User: Danh sách cuộc họp (qua MCP → qa.list_meetings)
```

---

## 8. Luồng hỏi đáp: Gate → Plugin → QA

```mermaid
flowchart TD
    MSG(["👤 Người dùng gửi tin nhắn"])
    WS["Hermes nhận qua WebSocket"]
    PLUGIN_PRE["Plugin: pre_tool_call"]

    GATE["gate.check(union_id)"]
    ENROLLED{"Đã enroll?"}

    INVITE["Gửi link OAuth\ndecision = invite"]
    WAIT["Nhắc chờ\ndecision = wait"]
    ALLOW["decision = allow\n+ asker_token\n+ profile (Thư Ký)\n+ memory (3 cuộc gần nhất)"]

    AGENT["Agent (LLM) xử lý"]
    TOOL_CALL{"Agent gọi\ntool nào?"}

    LIST["list_meetings(who)"]
    GET_MEETING["get_meeting(who, query)"]
    GET_TRANSCRIPT["get_transcript(who, query)"]
    SEND_FILE["send_transcript_file(who, query)"]
    SEARCH["search_meetings(who, query)"]
    NO_TOOL["Không gọi tool\n(chào hỏi, hỏi về bot)"]

    ACL{"_may_see()?\nuser ∈ owner ∪ attendees?"}
    BLOCKED["Ẩn — không có quyền\n(không lộ title/nội dung)"]
    VISIBLE["Cho xem"]

    SRC{"source_of(job)?"}
    SRC_HAPAS["SRC_HAPAS\nCó bản Whisper"]
    SRC_LARK["SRC_LARK\nCó bản chép Lark"]
    SRC_NONE["SRC_NONE\nChưa có bản nào"]

    RESPONSE_DATA["Kênh DỮ LIỆU\nAgent đọc rồi trả lời bằng lời"]
    RESPONSE_SEND["Kênh GỬI NGUYÊN VĂN\nPlugin lấy làm tin nhắn"]
    OFFER["Kênh NỘI BỘ\nLời mời bản chuẩn Hapas + ước tính"]

    TRANSFORM["transform_llm_output()\nNếu có khối GỬI NGUYÊN VĂN\n→ thay thế câu trả lời agent"]
    CLAIMS{"_claims_data()?\nCâu TRẢ LỜI có\nkhẳng định dữ liệu\nmà không gọi tool?"}
    BLOCK_CLAIM["Thay bằng _NO_TOOL_REPLY\n(cổng chống bịa)"]
    PASS["Cho qua"]

    FINAL(["📤 Gửi tin nhắn cho người dùng"])

    MSG --> WS --> GATE
    GATE --> ENROLLED
    ENROLLED -- "Chưa" --> INVITE --> FINAL
    ENROLLED -- "Chờ" --> WAIT --> FINAL
    ENROLLED -- "Rồi" --> ALLOW --> AGENT

    AGENT --> TOOL_CALL
    TOOL_CALL --> LIST & GET_MEETING & GET_TRANSCRIPT & SEND_FILE & SEARCH & NO_TOOL

    LIST & GET_MEETING & GET_TRANSCRIPT & SEND_FILE & SEARCH --> ACL
    ACL -- "Không" --> BLOCKED
    ACL -- "Có" --> VISIBLE --> SRC

    SRC --> SRC_HAPAS & SRC_LARK & SRC_NONE

    SRC_HAPAS --> RESPONSE_DATA
    SRC_LARK --> RESPONSE_DATA
    SRC_LARK -.->|"Kèm lời mời"| OFFER
    SRC_NONE --> RESPONSE_DATA

    LIST --> RESPONSE_SEND
    GET_MEETING & GET_TRANSCRIPT & SEARCH --> RESPONSE_DATA

    RESPONSE_DATA --> PLUGIN_PRE --> AGENT
    RESPONSE_SEND --> TRANSFORM --> FINAL
    NO_TOOL --> CLAIMS
    CLAIMS -- "Có bịa" --> BLOCK_CLAIM --> FINAL
    CLAIMS -- "Không" --> PASS --> FINAL

    style BLOCKED fill:#ffcdd2,stroke:#c62828
    style VISIBLE fill:#c8e6c9,stroke:#2e7d32
    style OFFER fill:#fff9c4,stroke:#f9a825
    style BLOCK_CLAIM fill:#ffcdd2,stroke:#c62828
```

---

## 9. Trần quyền Lark Minutes API

```text
                    +------------------------------------+
                    |        LARK MINUTES API V1         |
                    +-----------------+------------------+
                                      |
              +-----------------------+-----------------------+
              |                                               |
              v                                               v
    [ CHỦ BẢN GHI (Owner) ]                       [ NGƯỜI DỰ (Attendee) ]
    • Metadata    : ✅ 200 OK                          • Metadata    : ✅ 200 OK
    • Transcript  : ✅ 200 OK                          • Transcript  : ❌ 403 (2091005)
    • Download MP4: ✅ 200 OK                          • Download MP4: ❌ 403 (2091005)
    • Summary API : ❌ 404 (Không tồn tại)            • Summary API : ❌ 404 (Không tồn tại)
```

```mermaid
graph LR
    subgraph LARK_API["Lark Minutes v1 API"]
        META["minutes/{token}\n📋 Metadata\n(title, time, owner, link)"]
        STATS["minutes/{token}/statistics\n📊 Lượt xem"]
        TRANS["minutes/{token}/transcript\n📝 Bản chép nguyên văn"]
        MEDIA["minutes/{token}/media\n🎬 Link tải bản ghi"]
        SUMMARY["minutes/{token}/summary\n❌ KHÔNG TỒN TẠI (404)"]
    end

    OWNER["👑 Chủ bản ghi"]
    ATTENDEE["👤 Người tham dự"]
    ANYONE["🚫 Người ngoài"]

    OWNER -->|"✅ 200"| META
    OWNER -->|"✅ 200"| STATS
    OWNER -->|"✅ 200 (68K+ ký tự)"| TRANS
    OWNER -->|"✅ 200 (download_url)"| MEDIA
    OWNER -->|"❌ 404"| SUMMARY

    ATTENDEE -->|"✅ 200 (y hệt)"| META
    ATTENDEE -->|"✅ 200 (y hệt)"| STATS
    ATTENDEE -->|"❌ 403 (2091005)"| TRANS
    ATTENDEE -->|"❌ 403 (2091005)"| MEDIA
    ATTENDEE -->|"❌ 404"| SUMMARY

    ANYONE -->|"❌"| META
    ANYONE -->|"❌"| STATS
    ANYONE -->|"❌"| TRANS
    ANYONE -->|"❌"| MEDIA
    ANYONE -->|"❌"| SUMMARY

    style TRANS fill:#fff9c4,stroke:#f9a825
    style MEDIA fill:#fff9c4,stroke:#f9a825
    style SUMMARY fill:#ffcdd2,stroke:#c62828
```

---

## 10. Luồng gửi bản dịch (Pull Model) & Phân loại nguồn

```mermaid
sequenceDiagram
    actor User as 👤 Người dùng
    participant Bot as 🤖 Bot (Hermes + Plugin)
    participant QA as qa.py (MCP)
    participant LT as larktext.py
    participant DB as 💾 SQLite
    participant Orch as Orchestrator
    participant Pipeline as pipeline.py

    Note over User,Pipeline: Tình huống 1: Hỏi về cuộc họp (bản Hapas chưa xong)
    User->>Bot: "Cuộc họp Workforce bàn gì?"
    Bot->>QA: get_meeting(who, "Workforce")
    QA->>DB: _may_see() → ✅ owner/attendee
    QA->>LT: larktext.get(row)
    LT->>DB: Kiểm cache (lark_chars, lark_tried_at)
    LT-->>QA: Bản chép Lark (351 ký tự)
    QA-->>Bot: Kênh DỮ LIỆU: nội dung bản Lark<br/>Kênh NỘI BỘ: "[V2-OFFER: transcript]<br/>Hỏi: có cần bản chuẩn Hapas không?<br/>Ước tính: ~10 phút"
    Bot->>User: "Cuộc họp Workforce bàn về...<br/>Bạn cần bản dịch chuẩn từ Hapas không?<br/>Ước tính khoảng 10 phút."

    Note over User,Pipeline: Tình huống 2: Người dùng đồng ý
    User->>Bot: "Có"
    Bot->>Bot: Plugin: _is_yes() + _offered_recently() → ✅
    Bot->>QA: send_transcript_file(who, "Workforce")
    QA->>DB: request_transcript(token, union_id)<br/>priority = 2 (cực cao)
    QA-->>Bot: "Mình sẽ tự gửi file khi xong, ~10 phút"
    Bot->>User: "Mình sẽ tự gửi file khi xong"

    Note over User,Pipeline: Tình huống 3: Orchestrator xử lý (nền)
    Orch->>DB: process_queue() → thấy priority=2
    Orch->>Pipeline: run_transcription(meta)
    Pipeline->>Pipeline: Tải MP4 → ffmpeg → Whisper → LLM recap
    Pipeline-->>DB: status = "recapping"
    Orch->>Orch: _deliver_requested(meta, t)
    Orch->>Pipeline: deliver_file(meta, t, [union_id])
    Pipeline->>Pipeline: write_doc(t, meta) → file .docx
    Pipeline->>User: 📎 Gửi file .docx qua Lark IM
    Orch->>DB: status = "delivered"

    Note over User,Pipeline: Tình huống 4: Bản Hapas ĐÃ sẵn sàng
    User->>Bot: "Cuộc họp hôm qua bàn gì?"
    Bot->>QA: get_meeting(who, query)
    QA->>DB: source_of(job) → SRC_HAPAS
    QA->>LT: Vẫn đọc bản Lark trước
    QA-->>Bot: Kênh DỮ LIỆU: nội dung Lark<br/>Kênh NỘI BỘ: "Bản Hapas ĐÃ SẴN SÀNG<br/>— muốn nhận luôn không?"
    Note right of Bot: TUYỆT ĐỐI không hứa<br/>"chờ phiên âm" khi<br/>file đã nằm sẵn trên đĩa
    Bot->>User: "Cuộc họp bàn về...<br/>Bản dịch chuẩn từ Hapas **đã sẵn sàng** —<br/>muốn nhận luôn không?"
```

---

## 11. Xử lý audio: MP4 → WAV 16kHz Mono PCM Lossless

- `minutes_media` chỉ trả video MP4 (AAC audio 44.1kHz/96kbps).
- **Tuyệt đối KHÔNG dùng MP3 32kbps**: Nén lossy làm mất dải tần âm thanh tiếng Việt có dấu, tạo ra nhiễu làm Whisper bị hiện tượng hallucination (lặp từ, nuốt chữ).
- **Lệnh nén chuẩn**:
  ```bash
  ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le -y output.wav
  ```
  - `-vn`: Bỏ video track.
  - `-ac 1`: Chuyển sang Mono.
  - `-ar 16000`: Resample về 16kHz (đúng chuẩn input Whisper).
  - `-c:a pcm_s16le`: Lossless PCM 16-bit (~1.8MB / phút).

---

## 12. Whisper & LLM Local

- Model: `whisper medium` chạy CPU local (server chính khuyên dùng GPU).
- Hiệu năng CPU: Tốc độ xử lý realtime ~0.58x (cuộc 10 phút mất ~18 phút xử lý).
- **Biasing qua Glossary**: Truyền danh sách thuật ngữ chuyên ngành (MCP, Claude, Node.js, Lark, terminal...) vào `initial_prompt` của Whisper để ngăn phiên âm sai ("cây lau", "mờ cp").

---

## 13. Cạm bẫy Windows & Lưu ý vận hành

1. **`lark-cli` trên Windows**: Là file batch `.cmd`, phải lấy path qua `shutil.which("lark-cli")` trong Python subprocess.
2. **Encoding UTF-8 & CRLF**: File `.bat` không được chứa ký tự Unicode có dấu để tránh bị nuốt chuỗi lệnh dòng đầu (`TRANSCRIPT_SOURCE` → `RANSCRIPT_SOURCE`). Sửa script tự động phải đảm bảo kết thúc bằng `\r\n`.
3. **Quyền riêng tư & Bảo mật**: Không gom user token hàng loạt. ACL local trong SQLite kiểm soát nghiêm ngặt `who` theo đúng attendee/owner đã xác minh.
