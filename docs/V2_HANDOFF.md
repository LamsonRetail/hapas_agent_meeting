# V2 — Giao việc (chốt phiên 30/07/2026)

Đọc file này TRƯỚC khi sửa gì. Nó nói: đang ở đâu, cái gì đã kiểm chứng thật,
cái gì chỉ mới test mock, và việc kế tiếp làm theo thứ tự nào.

**Việc cần làm tiếp, đã chốt phạm vi + cách kiểm:** [V2_VIEC_CAN_LAM.md](V2_VIEC_CAN_LAM.md) — 5 việc hạng 2/hạng 3, giao được
cho một phiên mới làm ngay.

Vận hành hằng ngày: [V2_MAINTENANCE.md](V2_MAINTENANCE.md) (sổ tay chính).
Kiến trúc: [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md), [V2_LONGTERM.md](V2_LONGTERM.md).

---

## 1. Luồng hiện tại (sau khi bỏ cửa duyệt)

```
họp xong
  └─ scan_once()      polling minutes/search (nguồn sự thật) — chờ SETTLE_MINUTES
  └─ resolve_participants()  lịch của HOST -> người trong sự kiện đó
  └─ run_transcription()     tải media -> ffmpeg -> whisper CPU small -> recap LLM
  └─ deliver()               PHÁT NGAY: tóm tắt + file transcript cho TẤT CẢ
  └─ bitable.write_draft()   ghi record vào Base "Biên bản"
                             (cột Trạng thái = đã phát / phát hỏng / không có recap)

hỏi đáp (tách hẳn, tiến trình riêng)
  người hỏi trong Lark -> Hermes (adapter Feishu/Lark)
    -> hook pre_gateway_dispatch -> plugin v2-enroll-gate -> v2 gate
         chưa cấp quyền -> bot gửi link OAuth, tin KHÔNG tới agent
         đã cấp quyền   -> gói ChatGPT -> mcp_meetings_* -> V2 mcp_server -> Base

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
  trước khi gửi rồi trả lại nguyên trạng. Nên phần **dùng lại `file_key` cho
  người thứ hai vẫn CHƯA có bằng chứng thật** — vẫn chỉ mock. Lưu ý khi đọc DB:
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
2. Tên scope **có trong `OAUTH_SCOPES`** của `v2\.env`. Token luôn ra 196 scope
   kể cả khi chỉ xin 7, nên dễ tưởng "xin gì cũng vậy" — SAI: Console duyệt rồi
   mà không xin tường minh thì token vẫn thiếu đúng cái cần.
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
2. **Tạo Lark Task từ `action_items`.** Contract đã lấy sẵn:
   `POST /open-apis/task/v2/tasks?user_id_type=open_id`, body
   `{summary, description, due:{is_all_day,timestamp}, members:[{id,role:"assignee",type:"user"}], client_token}`.
   Scope `task:task:write` đã có. **NHƯNG** recap thật đang ra 0 quyết định,
   0 việc cần làm vì transcript quá nhiễu → xây xong cũng không có gì để tạo.
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
6. **Cửa vào tự phục vụ (§14 sổ tay) còn một khâu chưa đo:** một người THỨ HAI
   nhắn bot rồi nhận được link. Mọi mắt phía sau đã kiểm bằng dữ liệu thật.
   Kèm rủi ro đang mở: `FEISHU_ALLOW_ALL_USERS=true`, nên **plugin không nạp được
   là bot mở cho cả tenant** — sau mỗi lần update Hermes phải kiểm
   `hermes plugins list | findstr v2-enroll-gate`.

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
