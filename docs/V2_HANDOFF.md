# V2 — Giao việc (chốt phiên 30/07/2026)

Đọc file này TRƯỚC khi sửa gì. Nó nói: đang ở đâu, cái gì đã kiểm chứng thật,
cái gì chỉ mới test mock, và việc kế tiếp làm theo thứ tự nào.

**Việc cần làm tiếp, đã chốt phạm vi + cách kiểm:** [V2_VIEC_CAN_LAM.md](V2_VIEC_CAN_LAM.md) — 5 việc hạng 2/hạng 3, giao được
cho một phiên mới làm ngay.

> **Chạy `python -m v2 selftest` trước khi sửa gì và trước khi commit.**
> 180 phép kiểm, ~15 giây, không mạng, không đụng `state.db` thật
> (V2_MAINTENANCE §26). Đây là phép kiểm tự động DUY NHẤT của repo — trước
> 02/08/2026 không có cái nào, và mọi lỗi đều tìm bằng tay sau khi đã hỏng thật.
> Sửa xong một lỗi thì thêm một `check()` chặn đúng nó.

Vận hành hằng ngày: [V2_MAINTENANCE.md](V2_MAINTENANCE.md) (sổ tay chính).
Kiến trúc: [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md), [V2_LONGTERM.md](V2_LONGTERM.md).

---

## 1. Luồng hiện tại (sau khi bỏ cửa duyệt)

```
họp xong
  └─ scan_once()      polling minutes/search (nguồn sự thật) — chờ SETTLE_MINUTES
  └─ resolve_participants()  lịch của HOST -> người trong sự kiện đó
  └─ run_transcription()     tải media -> ffmpeg -> phiên âm  (bước ĐẮT NHẤT)
  └─ run_recap()             LLM -> tóm tắt/quyết định/việc cần làm
                             hỏng thì HOÃN (không phát bản trống), tối đa
                             RECAP_MAX_TRIES vòng, và KHÔNG phiên âm lại — §19
  └─ deliver()               PHÁT NGAY: tóm tắt + file transcript, CHỈ cho
                             người dự ĐÃ ENROLL (§24 — chưa cấp quyền = không
                             nhận gì; không ai cấp quyền thì vẫn giữ biên bản,
                             Base ghi `chưa ai cấp quyền`)
  └─ bitable.write_draft()   ghi record vào Base "Biên bản"
                             (cột Trạng thái = đã phát / phát hỏng / không có recap)
                             hỏng thì retry_missing_records() vá ở vòng sau — §19

hỏi đáp (tách hẳn, tiến trình riêng)
  người hỏi trong Lark -> Hermes (adapter Feishu/Lark)
    -> hook pre_gateway_dispatch -> plugin v2-enroll-gate -> v2 gate
         chưa cấp quyền -> bot gửi link OAuth, tin KHÔNG tới agent
         đã cấp quyền   -> gate cấp VÉ PHIÊN, plugin chèn `[V2-ASKER: …]` vào tin
                        -> gói ChatGPT -> mcp_meetings_* (kèm asker_token)
                        -> V2 mcp_server -> qa LỌC theo người dự -> Base
                           (thiếu vé = không trả gì — §20)

enroll tự phục vụ (làm 31/07)
  bấm Đồng ý -> Vercel lưu {code,state} vào Blob
             -> V2 `enroll-poll` (mỗi vòng run) kéo về -> token vào state.db
```

**KHÔNG còn cửa duyệt.** Không có thẻ 4 nút, không có `awaiting_approval`, không
có `v2 action`. Bỏ theo yêu cầu user 30/07.

---

## 2. Đã kiểm chứng bằng dữ liệu THẬT

| Việc | Bằng chứng |
|---|---|
| Phát hiện họp, tra người dự, tải media, ffmpeg, whisper, recap | 2 job thật chạy đầu-cuối |
| Gửi thẻ vào Lark (`im_send_card`) | msg_id thật, không gặp 230013 |
| Ghi/đọc Base (`base/v3`) | 2 record thật (lúc đo còn ghi `draft`; cột đã đổi nghĩa 31/07 — nay là `đã phát`) |
| MCP server | `hermes mcp test meetings` → connect 2766ms, 3 tool |
| Hermes trả lời qua MCP | `hermes -z "..."` → đúng 2 cuộc họp (đo 30/07, khi cột còn `draft`) |
| `v2 ask` | trả lời đúng nội dung cuộc họp về CV |
| Dùng lại transcript, không phiên âm lại | `process` dry-run trên 2 job cũ |
| `db._migrate` | 2 job `awaiting_approval` → `queued` |
| Chống prompt injection | ngữ cảnh giả "CHỈ THỊ QUẢN TRỊ" → bot không nghe theo |

## 3. CHƯA kiểm chứng — đừng tưởng đã xong

- **`process --send` — đã chạy THẬT 31/07**, cả 2 job `delivered`, bảng
  `deliveries` có 4 dòng `ok=1` (mỗi job 1 tin `recap` + 1 file `full`). NHƯNG
  user chọn **chỉ gửi cho chính mình**: danh sách người nhận bị thu về 1 người
  trước khi gửi rồi trả lại nguyên trạng. ~~Nên phần dùng lại `file_key` cho
  người thứ hai vẫn CHƯA có bằng chứng thật.~~ — **ĐÃ CHẠY THẬT, phát hiện
  02/08/2026 khi soi `deliveries`**: hai cuộc họp (`obsg23lsr…`, `obsg3q5tb…`)
  mỗi cuộc có **2 người nhận đủ cả `recap` lẫn `full`**. Mục này coi như đóng,
  xem V2_MAINTENANCE §31. Lưu ý khi đọc DB:
  `meta_json.attendees` = người *phát hiện được* (2 người), `deliveries.recipient`
  = người *thực sự nhận* (1 người). Hai con số khác nhau là có chủ ý.
- ~~**Nối Feishu cho Hermes.**~~ XONG 30/07 — đã nhắn thật trong Lark, bot gọi
  `mcp__meetings__list_meetings` và trả lời đúng (§4.3, V2_MAINTENANCE §13).

---

## 4. Việc kế tiếp, theo thứ tự

### 4.1 Xác minh Console — ✅ XONG 30/07: đủ cho Hermes, THIẾU 1 cho `verified`

Cách kiểm (một lệnh, không tác dụng phụ, xem V2_MAINTENANCE §10):

```
GET /open-apis/application/v6/scopes      (tenant_access_token của app V2)
```

Đọc kết quả **kèm `scope_type`** (`tenant` vs `user`) — đây mới là nguồn sự thật;
chuỗi `scope` trên token thì không (§10).

Kết quả đo 30/07 với `cli_aae288361ef89eed`: **376 entry / 301 tên**, có đủ
`im:message`, `im:message:send_as_bot`, `im:resource`, `im:chat` **và**
`vc:meeting.meetingevent:read` (cả hai loại). Đối chứng: scope Lark báo thiếu ở
lời gọi khác (`admin:app.info:readonly`) KHÔNG có trong danh sách → vắng mặt =
chưa có. ⇒ **Hermes (§4.3) không thiếu quyền gì.**

**Thiếu đúng một cái, và không phải cái ghi trong bản cũ:** `vc:meeting:readonly`
ở danh tính **người dùng** (app chỉ có ở `tenant`). Đây mới là lý do chưa đạt
`calendar[verified]` — xem §4.2 và V2_MAINTENANCE §10 để biết bằng chứng.

Đã đo thêm bằng cách gọi API với tham số cố ý sai (không gửi gì thật):
`im/v1/chats` → `code=0`; gửi tin với `receive_id` sai → `99992351`; đọc lịch sử
tin với `chat_id` sai → `230001`; tải file với `file_key` sai → `234008`. Toàn bộ
là lỗi **tham số**, tức đã qua cửa quyền.

Hai cách KHÔNG dùng được (đã đo, đừng lặp): `lark-cli auth status` là app khác
(`cli_a9bd0ff8d6619ed1`); và gọi `/authen/v1/authorize` theo từng scope — scope
bịa cũng nhận HTTP 302 y như scope thật.

**Còn một thứ scope API không nói được: event `im.message.receive_v1`** (Console
tab Events) — không có endpoint đọc. Chỉ biết bằng cách chạy thật ở §4.3/§4.4:
nếu chưa bật, Hermes nối được nhưng **không nhận tin nào và không báo lỗi**.

### 4.2 `calendar[verified]` — đang chờ Console, KHÔNG phải chờ enroll

Đã làm 30/07: thêm `vc:meeting.meetingevent:read` vào `OAUTH_SCOPES` (`v2\.env`,
`config.py`, `.env.example`) rồi **enroll lại thật** (`complete` OK). Kết quả đo
sau đó: `mget_instance_relation_info` vẫn **0/12** entry có `meeting_id` →
**enroll lại không giải quyết gì**, và scope đó không phải nguyên nhân.

Nguyên nhân thật (bằng chứng ở V2_MAINTENANCE §10): app thiếu
**`vc:meeting:readonly` ở danh tính người dùng**. Thiếu nó thì Lark lặng lẽ bỏ
trường `meeting_id` — không lỗi, không cảnh báo.

**BA điều kiện, thiếu một là không có scope** (đo 31/07, tôi từng chỉ nói 2):
1. Console duyệt scope đó cho app, đúng **danh tính user** (không phải tenant).
2. Tên scope **có trong `OAUTH_SCOPES`** của `v2\.env`. ~~Token luôn ra 196 scope
   kể cả khi chỉ xin 7~~ — câu đó chỉ đúng với **Thẩm**, người đã bấm Đồng ý
   nhiều lần lúc phát triển nên Lark cộng dồn thành 197. Người MỚI nhận **đúng
   những gì xin**: Chi enroll 31/07 chỉ được **9 scope**. Console duyệt rồi mà
   không xin tường minh thì token vẫn thiếu đúng cái cần (V2_MAINTENANCE §10).
3. **Enroll lại** (token cũ không tự có scope mới).

Việc cần người làm: Console app `cli_aae288361ef89eed` → thêm
`vc:meeting:readonly` (tick **cả** danh tính người dùng; có thể xin kèm
`vc:meeting.meetingid:read`) → Create version → admin duyệt. Xong thì enroll lại
cho chắc rồi đo lại bằng đúng phép đo trên (kỳ vọng: có `meeting_id`).

```bash
python -m v2 enroll-url
python -m v2 complete --code <CODE> --state <STATE>
python -m v2 doctor
```

Chưa xin thì hành vi hiện tại vẫn đúng và fail-closed (ghép theo tên/giờ), chỉ là
yếu hơn — đáng làm vì không còn cửa duyệt để chặn gửi sai người (§10).

### 4.3 Nối Feishu cho Hermes — ✅ XONG 30/07, đã hỏi đáp thật trong Lark

Chi tiết + 4 cái bẫy đã trả giá: **V2_MAINTENANCE §13**. Tóm tắt:

- Cấu hình đặt bằng tay (KHÔNG qua `hermes setup gateway`): `FEISHU_*` trong
  `%LOCALAPPDATA%\hermes\.env`, và `platform_toolsets.feishu` trong `config.yaml`.
- `FEISHU_ALLOWED_USERS` phải có **cả `user_id`**, không chỉ open_id/union_id —
  tenant này định danh người gửi bằng `user_id`. Sai thì tin vào tới nhưng bị
  chặn, và trong Lark thì im lặng hoàn toàn.
- Phải `pip install "lark-oapi==1.6.8" "qrcode==7.4.2"` vào venv của Hermes.
- Đã đo: surface Feishu chỉ còn `clarify, memory, session_search, todo, meetings`
  (+ `feishu_doc`/`feishu_drive`/`kanban` Hermes tự khôi phục). Không còn
  `terminal`/`file`/`execute_code`/`cronjob`/`browser`/`web`.
- Event `im.message.receive_v1` đã bật (chứng minh bằng chạy thật, không có API
  đọc). `hermes status` báo "Feishu ✗ not configured" là **dương tính giả**.

Chạy: `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe gateway run`
(foreground; muốn bền thì `hermes gateway install`). **Đừng chạy `v2 run --ws`
song song** — §5.1, và giờ đã thấy bằng mắt trong log Hermes.

### 4.4 Chạy thật — ✅ `process --send` ĐÃ chạy thật 31/07

```bash
python -m v2 doctor            # cần whisper: E:\whisper\run-server.bat (cổng 8000)
python -m v2 process           # dry-run: xem sẽ gửi cho AI trước đã
python -m v2 process --send    # gửi thật
python -m v2 run --send        # vòng lặp, KHÔNG có --ws (xem §5)
```

Kết quả 31/07: 2 job `delivered`, 4 dòng `deliveries` `ok=1`. Giới hạn của phép
đo này: **chỉ gửi cho 1 người** (user chọn vậy) nên phần dùng lại `file_key` cho
người thứ hai vẫn chưa được kiểm bằng dữ liệu thật — xem §3.

⚠️ **Dry-run KHÔNG in tên người nhận, chỉ in số lượng.** Muốn biết ai trước khi
gửi thì đọc `meta_json.attendees` trong `jobs` rồi tra tên bằng
`GET /open-apis/contact/v3/users/batch?user_id_type=open_id`. Với 2 job cũ, người
nhận là đồng nghiệp thật (BA và Team Leader) — đừng gửi biên bản họp test cho họ.

### 4.5 Còn lại (chưa làm)

1. ~~**Task cho leader review → approve** → flip Base `draft`→`final`~~ —
   **USER BỎ TÍNH NĂNG NÀY (31/07/2026): "còn 3 thì bỏ đi ko cần".**
   Đừng xây lại. Hệ quả "record ở `draft` mãi mãi" **đã dọn xong 31/07** (Việc
   5b, user chọn phương án (a)): cột `Trạng thái` nay nói về VIỆC PHÁT —
   `đã phát` / `phát hỏng` / `không có recap`, tính từ bảng `deliveries`.
   `mark_final()` và lệnh `v2 base-final` đã bỏ. Xem V2_MAINTENANCE §11.
   (Thiết kế cũ, để lại làm tham chiếu nếu đổi ý: tín hiệu approve là leader
   **hoàn thành Lark Task**, nghe qua event `task.task.updated` — không cần nút,
   không cần Console tab Callbacks.)
2. **Tạo Lark Task** — **LÀM XONG 01/08/2026 theo hướng khác**: không sinh tự
   động từ `action_items` (recap thật vẫn ra 0 việc cần làm), mà **người dùng
   bảo bot tạo** qua tool MCP `create_task`. Bốn ràng buộc cứng trong
   `v2/tasks.py` + ba cái bẫy về HẠN (Lark trả `code=0` rồi lưu sai ngày):
   V2_MAINTENANCE §21. Đường tự động vẫn để mở nếu sau này recap khá hơn.
3. ~~**`MAX_ATTEMPTS` đếm sai loại lỗi**~~ — **SỬA XONG 31/07/2026**, hai phần:
   - **dry-run không tiêu quota.** Trước đó `bump_attempts` chạy TRƯỚC khi biết
     dry-run hay không: hai lần `process` chẩn đoán + một lần `--send` = +3, đủ
     để job đang ở 3 bị `failed` **trước khi kịp gửi lần nào**. Đã mất một job
     thật đúng như vậy.
   - **lỗi hạ tầng không tiêu quota.** `transcribe.TranscribeUnavailable` (whisper
     tắt / treo quá hạn) được bắt riêng, `jobstore.unbump_attempts` trả lại lần
     thử. Không có bước này thì vòng `run` 5 phút/lần đốt hết 5 lần trong ~25
     phút và **mất vĩnh viễn biên bản của cuộc họp đó, im lặng**.
     Đo thật: job giả lập whisper tắt → `attempts` giữ nguyên 0, log in
     `whisper KHÔNG dùng được — KHÔNG tính lần thử`.
   - Còn tính vào quota (ĐÚNG như vậy): whisper trả `status=error` cho nội dung
     này, server không trả `job_id`, lỗi tải media/ffmpeg. Đó là lỗi của job.
   - ~~Hệ quả còn lại, chấp nhận: whisper chết giữa lúc chạy thì job **nằm chờ**
     trong `queued` (không mất, nhưng cũng không ai báo).~~ — **ĐÃ CÓ NGƯỜI BÁO
     từ 31/07/2026** (V2_MAINTENANCE §17): mỗi vòng `run` gọi `/health`, hỏng
     thì in log mỗi vòng và DM một lần sau `ALERT_WHISPER_AFTER_MIN` phút. Vẫn
     **không tự bật lại whisper** — `run-v2-auto.bat` chỉ bật lúc khởi động, và
     V2 cố ý không spawn cửa sổ Windows từ vòng lặp.
4. ~~**Không có cảnh báo nào**~~ — **XONG 31/07/2026**, `v2\alerts.py`: job
   `failed` / whisper chết / token còn ≤2 ngày → DM cho `ALERT_UNION_IDS`, mỗi
   tình huống **một lần** cho tới khi tình trạng đổi (mốc bền ở bảng
   `alert_state`). Đo thật, có `message_id` — bảng kết quả ở §17 sổ tay.
   ⚠️ Tiến trình `run` phải **khởi động lại** mới nạp phần này.
5. Domain riêng trước khi enroll người thứ 2 (V2_LONGTERM §3.1).
5b. ~~**Không có sao lưu nào tồn tại**~~ — **XONG 02/08/2026**, `v2\backup.py` +
   V2_MAINTENANCE **§6**. Vòng `run` tự sao lưu `state.db` mỗi 24h bằng
   `VACUUM INTO` (không phải copy file — WAL 3.2 MB sẽ bị bỏ lại), giữ 14 bản,
   khóa Fernet ghi ra chỗ RIÊNG ngoài repo. `LOOKBACK_DAYS` 2 → 7 cùng lúc: 2
   ngày không sống nổi một kỳ nghỉ, mà quá cửa sổ đó là cuộc họp mất vĩnh viễn
   và im lặng.
   ⚠️ `C:` `D:` `E:` là ba phân vùng của MỘT ổ vật lý (Disk #0) — mặc định
   KHÔNG chống được chết ổ, chỉ chống xoá nhầm / DB hỏng. `doctor` nói rõ câu
   đó. Đưa ra ngoài máy là quyết định về dữ liệu (bản sao chứa recap nội dung
   họp ở dạng đọc được), không phải về kỹ thuật.
7. ~~**LLM hỏng = mất tóm tắt vĩnh viễn / ghi Base hỏng = không có đường vá /
   `alerts` gửi link dài**~~ — **SỬA XONG 31/07/2026**, chi tiết + bảng đo ở
   **V2_MAINTENANCE §19**. Ba cái cùng một họ: bước phụ hỏng, hệ thống vẫn báo
   thành công, không gì chạy lại. Cột mới `jobs.recap_fails`, config mới
   `RECAP_MAX_TRIES=3`, hàm mới `bitable.retry_missing_records()` +
   `oauth.start_or_reuse()`. `_reuse` nay chấp nhận "có transcript, chưa có
   recap" nên làm lại recap KHÔNG kéo theo phiên âm lại.
8. ~~**Hỏi đáp không lọc theo người hỏi**~~ — **SỬA XONG 31/07/2026**, chi tiết
   + bảng đo ở **V2_MAINTENANCE §20**. Mỗi người chỉ đọc biên bản cuộc họp mình
   DỰ (hoặc mình là chủ); `QA_ADMIN_UNION_IDS` thấy hết; không biết ai hỏi thì
   không trả gì. Cơ chế: `gate` cấp **vé phiên**, plugin chèn vào tin bằng
   `action:"rewrite"`, agent truyền lại qua `asker_token` — vì MCP server dùng
   chung một tiến trình nên lời gọi tool không mang danh tính. Module mới
   `v2/askers.py`, bảng mới `qa_sessions`, lệnh mới `v2 ask --as <id>`.
   ⚠️ Phải `hermes gateway restart` mới ăn (plugin + MCP server).
9. **Link enroll không ràng buộc người — USER CHỌN GIỮ NGUYÊN (31/07/2026).**
   `gate` lưu `union_id ↔ nonce` trong `enroll_invites` nhưng `complete()` không
   so, nên ai cầm link cũng enroll được chính họ. Chấp nhận được **vì mục 8 đã
   xong**: tự enroll giờ chỉ cho thấy biên bản của chính họ, tức đúng bằng thứ
   họ vốn xem được trong Lark Minutes. Nếu sau này bỏ bộ lọc ở mục 8 thì rủi ro
   này quay lại NGAY — hai việc dính nhau.
10. ~~**Bot trả lời rộng, scope xin thừa**~~ — **XONG 01/08/2026**, V2_MAINTENANCE
   **§21** (bot chỉ hỏi đáp biên bản + tạo task; `platform_hints.feishu` +
   toolset còn `clarify`+`meetings`; `create_task` với 4 ràng buộc cứng) và
   **§22** (OAUTH_SCOPES 124 → 18, URL 3.132 → 731 ký tự).
   ⚠️ §22 có một giới hạn phải đọc: `v2 scopes` chạy bằng token đã cộng dồn
   125–257 scope nên chỉ chứng minh endpoint còn chạy, KHÔNG chứng minh 18 là
   đủ cho người mới. Phép thử thật là **người thứ tư enroll**; hỏng thì
   `_warn_if_missing_scopes` DM cho admin ngay lúc đó.
11. ~~**Siết `attendees`**~~ — **(a) XONG 02/08/2026**, chi tiết + bảng đo ở
   **V2_MAINTENANCE §23**. Đã lọc `rsvp_status=decline` và gộp người **thật sự
   vào phòng họp** từ VC (`lark_api.vc_meeting_participants`). Nguyên nhân gốc
   của "3/5 job chỉ có 1 người" hoá ra KHÔNG phải họp mở tay mà là **mời bằng
   group chat**: `event_attendees` trả `type={'chat':1,'resource':1,'user':1}`
   trong khi 31 người ngồi trong phòng. Đo thật: Workforce Weekly **1 → 30
   người**. Kèm hai vá: `_meeting_ids_via_no` thử MỌI ứng viên thay vì cái đầu,
   và không ghép cặp union/open khi hai lời gọi lệch số lượng.
   ⚠️ Đọc §23 mục "quy công cho đúng chỗ": `verified` 0/5→3/5 là nhờ **enroll
   lại có scope**, không phải nhờ code. Nhánh loại người `decline` CHƯA có mẫu
   thật để kiểm.
   **Tiếp theo, cùng ngày: chỉ gửi cho người ĐÃ ENROLL — V2_MAINTENANCE §24.**
   Bảng mới `minute_viewers` (ai đã enroll mà Lark báo có dự), người nhận =
   người dự ∩ đã enroll ∪ viewers. Workforce 30 người dự → 3 người nhận. Tách
   rõ "ai được XEM" (rộng, nuôi `qa.viewers_index`) khỏi "ai được GỬI" (hẹp) —
   nên phần gộp VC ở trên vẫn có giá trị dù không còn phát rộng.
   Hệ quả: đường dùng lại `file_key` cho người thứ 2+ vẫn sẽ chạy thật ở cuộc
   họp đầu tiên có ≥2 người enroll cùng dự (§3), chỉ là quy mô nhỏ hơn nhiều.
   ~~**(b) CÒN MỞ:** vẫn ghép `union_id`/`open_id` theo THỨ TỰ… và
   `event_attendees` chưa phân trang.~~ — **CẢ HAI SỬA XONG 02/08/2026**,
   V2_MAINTENANCE **§31.6** và **§31.5**. Ghép bằng `attendee_id` (đo thật:
   cùng một người có `attendee_id` giống hệt ở cả hai lời gọi, chỉ `user_id`
   đổi dạng); `event_attendees` nay phân trang thật.
6. ~~**Cửa vào tự phục vụ (§14) còn một khâu chưa đo:** một người THỨ HAI nhắn
   bot rồi nhận được link.~~ — **ĐÃ XẢY RA THẬT 31/07/2026 16:15**: Nguyễn Thùy
   Chi enroll thành công qua cửa tự phục vụ. Luồng chạy đúng.
   **Và nó lộ ra một lỗi thật mà một-người-dùng không bao giờ thấy được:** token
   Chi chỉ có **9 scope** (đúng cái `OAUTH_SCOPES` xin) trong khi Thẩm có **197**
   (tích luỹ qua nhiều lần cấp quyền lúc phát triển) → `minutes_search` của Chi
   trả `99991679`, tức V2 **không phát hiện được cuộc họp nào của cô ấy**. Đã
   thêm 3 scope minutes vào `OAUTH_SCOPES`; Chi phải **enroll lại** thì mới ăn.
   Chi tiết + cách chẩn: V2_MAINTENANCE §10.
   Kèm rủi ro đang mở: `FEISHU_ALLOW_ALL_USERS=true`, nên **plugin không nạp được
   là bot mở cho cả tenant** — sau mỗi lần update Hermes phải kiểm
   `hermes plugins list | findstr v2-enroll-gate`.

---

12. **Ràng buộc mới biết, ảnh hưởng cả cách triển khai: V2 chỉ làm được biên
   bản khi CHỦ BẢN GHI đã cấp quyền.** Người dự cấp quyền là chưa đủ — đo
   02/08/2026 (ma trận 5 minute × 3 người, tất cả đều có scope
   `minutes:minutes.media:export`): chủ bản ghi luôn tải được, người dự khác hầu
   hết bị `2091005`. Không phải quy tắc "chỉ chủ" (Chi tải được bản ghi của
   Thiện, Thẩm thì không) nên **phải thử từng người**. Đây là ràng buộc của
   Lark, không sửa bằng scope được. Chi tiết + ba lỗi lộ ra từ đó:
   **V2_MAINTENANCE §25**. Khi mời người mới dùng hệ thống, ưu tiên người hay
   ĐỨNG RA MỞ họp.

---

13. **Ba lỗ "không ai được báo" — VÁ 02/08/2026, chi tiết + số đo ở
   V2_MAINTENANCE §28.** Cùng họ với §19 nhưng ở tầng theo dõi:
   - **Base là cửa THỨ HAI vào cùng dữ liệu.** Đo thật: `link_share_entity =
     tenant_readable` + `external_access_entity = open` — cả công ty có link là
     đọc được **nguyên văn transcript** của mọi cuộc họp, đi vòng qua toàn bộ
     §20 + §24. `doctor` nay soi và báo. **USER CHỌN GIỮ NGUYÊN (02/08/2026)** —
     dòng `[!] Base: ai có link cũng đọc được` là trạng thái ĐÃ CHẤP NHẬN, không
     phải việc còn tồn; đừng tự đóng, đừng báo lại như phát hiện mới. Hệ quả
     phải nhớ: §20 + §24 chỉ còn là hàng rào cho đường BOT, không phải cho dữ
     liệu — nội dung không được để cả công ty đọc thì phải chặn TRƯỚC khi lên
     Base, siết `qa.py` lúc đó là vô nghĩa (§28.1).
   - **Cảnh báo nằm bên trong thứ nó canh.** `run` chết = mọi DM chết theo. Nay
     có heartbeat + Scheduled Task `V2_Alerts` (15 phút/lần) chạy từ NGOÀI.
     Kèm một sự thật mới đo được: **refresh token Lark sống 7 NGÀY, không phải
     30, và trượt theo chính vòng `run`** — tắt quá 7 ngày là mọi người enroll
     lại.
   - **LLM chết ~15 phút = mất tóm tắt vĩnh viễn.** Nay có cảnh báo riêng cho
     LLM và `_backfill_recaps` tự làm lại tóm tắt khi LLM sống lại (KHÔNG gửi
     lại thẻ cho người dự — chỉ sửa DB + Base).

   ⚠️ **Phải khởi động lại `run`** mới nạp heartbeat + backfill. Chưa restart
   thì `V2_Alerts` vẫn chạy nhưng mục "run đã chết" im (fail-safe).

---

14. **Có HAI repo, và chúng có lịch sử khác nhau vĩnh viễn — V2_MAINTENANCE §29.**
   `origin` = `tientham2005/MeetingxLark` (repo làm việc, history CÓ app secret
   đang dùng thật + 35 MB bản ghi họp). `lamson` = `LamsonRetail/meetingxlark`
   (bản cho công ty, lập ra SẠCH có chủ ý). `git push lamson main` sẽ bị từ chối
   và **`--force` là điều tuyệt đối không được làm** — nó đẩy secret sống vào
   repo công ty, không lùi lại được. Đồng bộ bằng quy trình worktree ở §29 (đã
   chạy thật 02/08 → `91add5d`).

15. **Hai lỗ còn lại từ vòng rà — VÁ 02/08/2026, V2_MAINTENANCE §30.**
   - **Bot chỉ trả lời chat 1-1** (`gate._refuse_group` + plugin). Lý do KHÔNG
     phải "agent lẫn vé của hai người" — Hermes để `group_sessions_per_user:
     true` nên chuyện đó đã bị chặn sẵn; lý do thật là bộ lọc cấp quyền cho
     **người hỏi** còn câu trả lời thì **cả phòng đọc**.
   - **`lark_api` nay có retry** 429/5xx — **CHỈ cho lời gọi ĐỌC**. POST không
     thử lại (gồm `im_send_card`: phát trùng biên bản cho cả phòng họp).
   - **`meta` không còn đông cứng**: `_maybe_reresolve` tra lại người dự ngay
     trước khi phát, chạy trên BẢN SAO để tra hỏng không xoá trắng danh sách
     đang có.

   ⚠️ Cần `hermes\install-plugin.bat` + `hermes gateway restart` + khởi động
   lại `run`. `selftest` 142 → **180**.

---

16. **Vòng gỡ lỗi tối 02/08/2026 — bảy phát hiện, chi tiết ở V2_MAINTENANCE §31.**
   Vòng này bắt đầu từ **log của hệ thống đang chạy**, không phải từ đọc code:
   - `timeout /t` trong `.bat` chạy nền **không chờ giây nào** (đo: 0,098s) —
     `run-v2-auto` đã bật lại orchestrator 17 lần trong 0,93 giây. Đổi sang
     `ping`. §31.1
   - Nguyên nhân các lần chết: **máy tự ngủ** (Kernel-Power ID 42 khớp từng
     giây với mã thoát `-1073741205`). **User chọn KHÔNG đổi cấu hình nguồn**,
     để lúc đổi hạ tầng máy. §31.2
   - **Transcript rỗng đi qua như thành công** — job `delivered`, `error=NULL`,
     nhưng file 0 byte và không ai được báo. Thêm `EmptyTranscript`. §31.3
   - **Gửi hỏng một phần thì không ai thử lại** — thêm `_backfill_deliveries`,
     và nửa còn lại: người **nhận được thẻ mà file hỏng** đi đường
     `pipeline.deliver_file` (gửi riêng file, không gửi lại thẻ trùng). §31.4
   - `event_attendees` **cắt im lặng ở người thứ 100**. §31.5
   - Ghép `union_id`/`open_id` theo thứ tự → nay theo `attendee_id`. §31.6
   - Một người token chập **làm mù cả vòng quét**. §31.7
   - **`selftest` đã gọi MẠNG THẬT** (quên stub một hàm) — nay `lark_api._http`
     bị thay bằng hàm ném lỗi, quên stub là FAIL ngay. §31.8
   - **Hộp thư enroll (Vercel) hỏng làm chết cả vòng quét (Lark)** — tách khối
     `try` riêng. §31.9

   `selftest` **180 → 215**. ⚠️ Phải khởi động lại `run` mới nạp.

---

17. **Hạn mức Vercel Blob suýt tự khoá cửa enroll — V2_MAINTENANCE §32.**
   Thư Vercel 02/08: đã dùng **75%** của **2.000 thao tác/tháng** (gói free).
   Đo từ log: `POST /api/status` 3 thao tác × 109 lần/ngày + hộp thư OAuth 1 ×
   100 = **~427/ngày**, cạn trong ~4,7 ngày. Cạn = **hộp thư OAuth chết = không
   ai enroll được**, chỉ để lại một dòng log.
   Đã: (1) chỉ đọc hộp thư khi có nonce còn sống; (2) `STATUS_PUSH_EVERY`
   0 → 1800; (3) `api/status.js` ghi đè một pathname (3 → 1 thao tác).
   ⇒ ~427 → **~18 ops/ngày**.
   ⚠️ **(3) cần deploy Vercel mới ăn** — chưa deploy thì đang ở ~54/ngày.

---

## 5. Ba điều KHÔNG được làm sai

1. **Đừng chạy `run --ws` khi Hermes đang chạy.** Hermes dùng CÙNG `app_id`.
   Lark **không** từ chối kết nối WebSocket thứ hai (đã đo: cả hai `connected`),
   nên **không có lỗi nào báo** — chỉ là Hermes lặng lẽ mất tin nhắn. V2 không
   cần `--ws`: polling là nguồn sự thật, listener được phép chết.
2. **Đừng đổi `V2_FERNET_KEY`** khi đã có người enroll = mọi token thành rác.
   Backup `state.db` và key cùng nhau nhưng để tách chỗ (§6 sổ tay).
3. **Đừng thêm hàm ghi/xoá vào `qa.py`.** Nó chỉ đọc, có chủ ý: dữ liệu Base
   bắt nguồn từ lời người ta nói trong họp và chảy thẳng vào prompt của agent.

## 6. Bài học phương pháp từ phiên này

Phiên 30/07 tôi kết luận sai **ba lần** về Hermes/Lark, cả ba đều do tra một chỗ
rồi kết luận. Ghi lại để không lặp:

| Kết luận sai | Thực tế | Sai ở đâu |
|---|---|---|
| "Hermes không có kết nối Lark" | có `plugins/platforms/feishu/`, hỗ trợ Lark quốc tế | grep `.env.example` ở gốc repo; plugin nền tảng khai env trong `plugin.yaml` của nó |
| "ChatGPT Plus không dùng được" | dùng được qua Codex OAuth, "no API key required" | không tra `skills/.../codex/` và `docs/.../codex-app-server-runtime` |
| "một `app_id` chỉ giữ được một WebSocket" | Lark nhận cả hai kết nối | thử 2 client trong CÙNG tiến trình → lỗi event loop của SDK, tưởng là giới hạn của Lark |
| "app thiếu `vc:meeting.meetingevent:read` nên không ra `meeting_id`" (ghi trong config.py + §10, sống qua nhiều phiên) | app có sẵn cả `tenant` lẫn `user`; token cũ cũng đã có. Thiếu thật là `vc:meeting:readonly` **[user]** | thấy lark-cli ra `meeting_id` mà V2 không, rồi đoán tên scope theo nghĩa chữ ("meeting event"). Đúng cách: `application/v6/scopes` của **cả hai** app rồi so theo `(tên, scope_type)`; và gọi API cố ý sai để Lark tự đọc tên scope còn thiếu |

Cách tra đúng: `gh api "search/code?q=repo:OWNER/REPO+<từ khoá>"` rồi đọc
`website/docs/`; và đo bằng **hai tiến trình riêng**, đừng đo trong một tiến trình.

Còn hai công cụ đã dùng nhiều lần và vẫn nên dùng đầu tiên:
- `lark-cli <cmd> --dry-run` in ra đúng method + URL + body của Open API — cách
  lấy contract khi endpoint không có tài liệu. Đã dùng cho `task +create`,
  `base +record-list`, `im +messages-reply`, `vc meeting get`.
- So với V1 `meeting_poller.py` (đã chạy production) TRƯỚC khi tự nghĩ.
