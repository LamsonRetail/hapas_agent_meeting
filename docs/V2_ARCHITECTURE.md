# V2 — Luồng meeting transcript tự động, không phụ thuộc MCP

Thiết kế lại từ đầu: bỏ `lark-cli`, bỏ Lark MCP, tự gọi Open API. Nhân viên
không cài gì trên máy. Bảo trì qua web portal giống cách MCP đang làm.

**Trạng thái:** bản thiết kế, chưa code. Đọc `README.md` (V1) và
`docs/MULTI_USER.md` để hiểu luồng cũ trước.

---

## 1. Quyết định đã chốt

| Câu hỏi | Chốt |
|---|---|
| Hạ tầng | Web portal trên Vercel + **một máy local duy nhất**. Không VPS. |
| Phân phối | Chủ phòng duyệt trong card rồi mới phát |
| Phiên âm | Whisper local trên GPU hiện tại (GTX 1660 SUPER, large-v3) |
| Phụ thuộc | Không MCP, không lark-cli. Gọi thẳng Lark Open API |

---

## 2. Vì sao bỏ được MCP mà không mất gì

MCP và `lark-cli` trong V1 đang gánh hai việc: **giữ phiên đăng nhập** và
**bọc API Lark thành tool**. Việc thứ hai chỉ có ý nghĩa khi người gọi là một
LLM tự quyết định gọi gì. Luồng meeting note là một pipeline cố định —
biết trước phải gọi API nào, theo thứ tự nào. Nên lớp tool là phí.

Việc thứ nhất thì phải tự làm lại. Đó là chi phí thật của quyết định này, xem
mục 5.

Đổi lại, V2 bỏ được toàn bộ phần phát hành `mcp.zip`, Vercel Blob,
`/api/generate`, và việc mỗi nhân sự phải cài file về máy.

---

## 3. Hai thành phần

Bỏ VPS được, vì **chỉ đúng một thứ trong toàn hệ thống cần địa chỉ công khai**:

| Việc | Chiều kết nối | Cần public URL? |
|---|---|---|
| Nhận event Lark (`im.message`, `vc.*`) | WebSocket outbound | Không |
| Nhận card action (nút Duyệt) | Cùng WebSocket đó | Không |
| Gửi tin, tạo task | Bot API outbound | Không |
| Tải mp4, đọc minutes, đọc lịch | HTTPS outbound | Không |
| **OAuth `redirect_uri`** | Lark **gọi vào** | **Có** |

Và cái duy nhất đó chỉ là một `GET` mang theo `code`, một lần cho mỗi nhân viên
trong đời. Không đáng để dựng cả một VPS.

```
                    ┌──────────────────────────────────┐
                    │  Vercel — web portal              │
                    │  · Trang admin (bảo trì)          │
                    │  · Đọc/ghi Bitable                │
                    │  · /oauth/callback — hộp thư code │
                    └──────────┬───────────────────────┘
                               ▲ heartbeat + trạng thái
                               │ poll code đang chờ (5s)
                               │        ── cả hai đều outbound ──
  Lark  ◄──WebSocket event──►  ┌──────────────────────────────────┐
        ◄──Bot API───────────► │  MỘT máy local, MỘT process       │
        ◄──tải mp4───────────► │  · Lark WS: event + card action   │
                               │  · token store SQLite (mã hoá)    │
                               │  · queue + chống trùng            │
                               │  · ffmpeg → transcribe_server     │
                               │  · recap + phân phối              │
                               └──────────────────────────────────┘
```

Bỏ VPS còn xoá luôn được cả sự chia tách orchestrator/worker: giờ chúng ở cùng
máy nên gộp thành **một process**. Ba thành phần xuống còn hai.

### Nhận `code` mà không mở cổng nào — Vercel làm hộp thư

`redirect_uri` trỏ về `https://<portal>.vercel.app/oauth/callback`. Nhưng
**Vercel không đổi code lấy token** — nó chỉ giữ hộ:

```
Lark → GET /oauth/callback?code=X&state=nonce
         Vercel: mã hoá X bằng PUBLIC KEY của máy local
                 ghi vào bảng pending, trả trang "Đang kết nối..."

Máy local → GET /api/oauth/pending  (mỗi 5 giây, kèm shared secret)
         nhận code đã mã hoá
         giải bằng PRIVATE KEY (chỉ có ở local)
         tự đổi code lấy token
         lưu SQLite
         bot nhắn "Đã kết nối"
```

Public key nằm trong env của Vercel, private key **chỉ ở máy local**. Vercel
cầm `code` nhưng không giải được, và `code` vốn dùng một lần, hết hạn ~5 phút.
**Token không bao giờ chạm Vercel.**

Máy local tắt lúc ai đó bấm duyệt → `code` hết hạn → portal hiện "chưa kết nối
được, thử lại". Người đó bấm lại link là xong. Không mất mát gì.

### Phương án thay thế: Cloudflare Tunnel

Named tunnel (free) cho hostname cố định, `redirect_uri` trỏ thẳng về máy local.
Đơn giản hơn về code, nhưng mở một đường vào máy local và thêm một daemon phải
trông. Hộp thư qua Vercel không mở gì cả — **nên chọn nó trước**. Giữ tunnel làm
dự phòng nếu polling tỏ ra phiền.

---

## 4. Luồng đầy đủ

### [0] Enroll — thay cho việc cài file

```
Nhân viên nhắn bot lần đầu
  → im.message.receive_v1 qua WebSocket → máy local
  → local sinh nonce, lưu (nonce → open_id, TTL 10 phút), đẩy nonce lên portal
  → Bot gửi card: "Kết nối tài khoản của bạn" + nút
       https://open.larksuite.com/open-apis/authen/v1/authorize
         ?app_id=...&redirect_uri=https://<portal>.vercel.app/oauth/callback
         &scope=minutes,calendar,vc,contact&state=<nonce>
  → Bấm trong Lark → webview → Đồng ý
  → Vercel nhận code, mã hoá bằng public key, ghi bảng pending
  → local poll thấy, giải mã, đổi code lấy access + refresh token
       · mã hoá, lưu SQLite trên máy local
       · ghi 1 dòng Users vào Bitable (KHÔNG có token)
  → Bot nhắn "Đã kết nối. Từ giờ biên bản sẽ tự về."
```

`state` phải là nonce một lần dùng, **không phải open_id trần**. Dùng open_id
trần thì bất kỳ ai đoán được open_id đều gọi được callback.

Ai chưa enroll thì portal có nút **Gửi lại link** — bot chủ động nhắn.

### [1] Phát hiện họp xong

Hai đường, chạy song song:

| Đường | Cách | Độ trễ |
|---|---|---|
| A | Event `vc.meeting.recording_ready_v1` qua WebSocket | vài giây |
| B | Cron 5 phút, quét Minutes của từng user đã enroll | ≤ 5 phút |

Đường A nhanh hơn hẳn nhưng **chưa chắc bắn cho họp thường** — xem mục 8.
Đường B là lưới an toàn, chính là logic `find_new_minutes` của V1 viết lại
không qua lark-cli.

**Chống trùng.** Một cuộc họp 5 người sẽ xuất hiện trong Minutes của cả 5.
V1 dùng `poller_state.json` — không an toàn khi có nhiều luồng. V2 dùng
`INSERT ... ON CONFLICT DO NOTHING` trên cột `minute_token UNIQUE` trong
SQLite. Ai insert được thì xử lý, còn lại bỏ qua. Đây là khoá thật.

### [2] Dựng ngữ cảnh

Từ minute → dò calendar event trùng khung giờ (±3 tiếng, như V1) → lấy
`organizer` và `attendees`. Ghi dòng `Meetings` trên Bitable với
`status = queued`. Không tra được lịch thì rơi về chủ tài khoản, giữ nguyên
`FALLBACK_TO_OWNER` của V1.

### [3] Phiên âm

Cùng máy, cùng process — không còn claim/pull qua mạng:

```
lấy job đầu hàng đợi (SQLite)
  → lấy access token của một người có dự (refresh nếu hết hạn)
  → tải mp4 từ Lark
  → ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 out.mp3     # giảm ~92% dung lượng
  → POST localhost:8502/transcribe → job_id
  → poll /queue/status đến khi xong
  → ghi transcript, status = recapping
  → xoá file tạm
```

`transcribe_server` giữ nguyên, không sửa. Nó đã có queue, `job_id`,
`/queue/status`, ETA. Chỉ bỏ phần tự gọi Lark Bot API — giờ orchestrator lo
phân phối.

Chạy phiên âm **một job tại một thời điểm**. GPU 6GB không chạy song song
large-v3 được, và hàng đợi tuần tự dễ đoán hơn.

### [4] Sinh recap

Orchestrator gọi LLM: recap, danh sách quyết định, action item (kèm người phụ trách nếu
đoán được). Giai đoạn đầu dùng OpenAI như V1. Xem mục 7 về Hermes.

### [5] Chủ phòng duyệt

```
Bot gửi card cho organizer:
  ┌────────────────────────────────────────┐
  │ Biên bản: <tên họp>                     │
  │ 14:00–15:20 · 6 người được mời          │
  │                                         │
  │ <recap 5 gạch đầu dòng>                 │
  │ Action item: 3                          │
  │                                         │
  │ [Phát cho 6 người]  [Chỉ mình tôi]      │
  │ [Xem transcript]    [Bỏ]                │
  └────────────────────────────────────────┘
```

- `card.action.trigger` → về qua WebSocket. **Phải trả lời trong 3 giây** → trả toast ngay,
  xử lý phát tán ở background.
- Chưa bấm sau 24h → nhắc một lần.
- Chưa bấm sau 7 ngày → tự đóng, `status = expired`, **không phát**.
- Admin phát tay được từ portal khi chủ phòng nghỉ.

### [6] Phát

| Người | Nhận |
|---|---|
| Chủ phòng | Recap + file transcript đầy đủ |
| Người được mời (kể cả vắng) | Recap + action item của họ |

Tuỳ chọn trong card: tick "Tạo task" → gọi Lark Task API tạo task cho từng
action item, giao cho người phụ trách.

Ghi từng lần gửi vào bảng `Deliveries` để truy được "ai đã nhận gì, lúc nào".

---

## 5. Lưu trữ — chỗ nào để gì

**Nguyên tắc: Bitable giữ thứ admin cần nhìn. SQLite trên máy local giữ secret và khoá.**

Bitable là tài liệu chia sẻ — ai được cấp quyền là đọc được, và nó không có
mã hoá ở tầng ứng dụng. **Không để refresh token ở đó.**

### Bitable (portal đọc/ghi)

**`Users`**

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `open_id` | Text | khoá |
| `union_id` | Text | |
| `name` / `department` | Text | |
| `enrolled_at` | Number | ms |
| `token_expires_at` | Number | ms — để portal hiện "còn N ngày" |
| `status` | Text | `active` / `expired` / `revoked` |
| `last_used` | Number | |

**`Meetings`**

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `minute_token` | Text | khoá, unique |
| `title` | Text | |
| `start` / `end` | Number | |
| `owner_open_id` | Text | |
| `invitee_count` | Number | |
| `status` | Text | xem máy trạng thái dưới |
| `queued_at` / `transcribed_at` / `delivered_at` | Number | đo được từng chặng |
| `audio_seconds` / `whisper_seconds` | Number | tính tỉ lệ realtime |
| `error` | Text | |

Máy trạng thái:

```
detected → queued → claimed → transcribing → recapping
  → awaiting_approval → delivered
                     ↘ owner_only
                     ↘ expired      (7 ngày không duyệt)
                     ↘ failed       (retry được từ portal)
```

**`Deliveries`** — `minute_token`, `recipient_open_id`, `kind` (full/recap),
`sent_at`, `ok`, `error`.

**`Config`** — key/value. Sửa từ portal, máy local đọc lại mỗi 60 giây. Không phải
deploy lại để đổi tham số:

| Key | Ví dụ |
|---|---|
| `enabled_departments` | `*` hoặc danh sách |
| `whisper_model` | `large-v3` |
| `approval_timeout_hours` | `168` |
| `transcript_retention_days` | `90` |
| `paused` | `false` — công tắc dừng khẩn |

**`Audit`** — giữ nguyên bảng đang có của MCP portal.

### SQLite trên máy local (không ai nhìn thấy)

```sql
tokens(open_id PK, access_enc, refresh_enc, access_exp, refresh_exp, updated_at)
minutes_lock(minute_token PK, claimed_at)     -- chống trùng
oauth_nonce(nonce PK, open_id, expires_at)
jobs(id PK, minute_token, status, attempts, error)
```

Mã hoá bằng Fernet, key trong biến môi trường của máy. Cùng máy với private key
giải `code` OAuth.

**Backup là bắt buộc, không phải nice-to-have.** Bỏ VPS nghĩa là tất cả nằm trên
một ổ đĩa. Mất file `.db` = **toàn bộ công ty phải enroll lại**. Cron hàng ngày:
copy `.db` (đã mã hoá sẵn) lên Lark Drive hoặc ổ ngoài. Key backup ở chỗ khác
với file.

---

## 5b. Cái giá của việc bỏ VPS

Tiền không phải lý do — VPS chỉ ~120k/tháng. Lý do đúng để chạy local là **dữ
liệu họp không rời công ty** và **ít một thứ phải bảo trì**. Đổi lại có bốn hệ
quả, đều xử lý được nhưng phải xử lý có chủ đích:

**1. Polling trở thành bắt buộc, không còn là dự phòng.**
Event WebSocket của Lark **không phát lại** những gì đã bỏ lỡ lúc mất kết nối.
Máy tắt 2 tiếng = mất sạch event trong 2 tiếng đó. Nên vòng quét Minutes mỗi 5
phút (đường B ở mục 4.1) là **cơ chế chính đảm bảo không sót họp**; event chỉ
là đường nhanh khi máy đang sống. Quét lùi 2 ngày để bắt bù sau khi bật lại.

**2. Nút Duyệt hỏng khi máy tắt.**
Chủ phòng bấm lúc offline → Lark báo lỗi. Cần hai lối thoát:
- Bot nhận lệnh text: nhắn `duyệt <mã>` — xử lý khi máy sống lại.
- Admin phát tay từ portal.

Nới `approval_timeout_hours` lên 7 ngày cũng chính là để chịu được chuyện này.

**3. Máy phải thực sự luôn bật.**
- Task Scheduler tự khởi động (đã có `install-autostart.bat` từ V1, dùng lại).
- Watchdog: process chết thì bật lại.
- Tắt Windows Update tự khởi động lại, hoặc đặt giờ ngoài giờ làm.
- Heartbeat lên portal mỗi 60 giây. Quá 10 phút không thấy → portal hiện đỏ.
- Cân nhắc UPS nếu điện hay chập chờn.

**4. Không có ai gác đêm.**
Máy chết lúc 22h thứ Sáu thì đến thứ Hai mới ai đó nhận ra. Cho portal gửi
cảnh báo vào một group chat khi mất heartbeat quá 15 phút.

### Khi nào nên quay lại VPS

Không phải bây giờ. Nhưng đặt sẵn ngưỡng để biết lúc nào cần đổi:

- Quá ~30 người dùng thật, hoặc
- Có người phàn nàn về việc biên bản đến muộn quá một ngày làm việc, hoặc
- Downtime vượt ~2 lần/tháng

Thiết kế này giữ được đường lùi: toàn bộ code orchestrator không phụ thuộc gì
vào việc nó chạy ở đâu. Chuyển sang VPS sau này là copy thư mục + đổi
`redirect_uri`, không phải viết lại.

---

## 6. Bảo trì qua portal — cụ thể có gì

Đây là phần trả lời câu "giống MCP". Mở rộng `web-portal` đang có (~1.100 dòng,
thêm khoảng 600).

| Trang | Nội dung | Hành động |
|---|---|---|
| **Nhân sự** | Ai đã enroll, token còn bao lâu, lần dùng cuối | Gửi lại link enroll · Thu hồi · Tạm tắt |
| **Cuộc họp** | Hàng đợi realtime theo trạng thái, thời gian từng chặng | Retry job lỗi · Phát tay · Huỷ |
| **Sức khoẻ** | Heartbeat máy local lần cuối · độ dài queue · ETA · tỉ lệ realtime GPU · số code OAuth đang kẹt chờ | Dừng khẩn (`paused`) |
| **Cấu hình** | Bảng `Config` | Sửa trực tiếp, không deploy |
| **Audit** | Log hành động | Lọc, xuất |

Cái **biến mất** so với portal MCP: `/api/generate`, `template/mcp.zip`,
Vercel Blob, trang tải file. Không ai cài gì nữa.

Cái **còn lại cần update**: chỉ một process trên một máy. Portal expose
`version.json`, máy local tự so sánh lúc heartbeat và báo "có bản mới" lên
dashboard. Một máy, không phải mười.

---

## 7. Chỗ để Hermes vào (nếu vẫn muốn)

Đính chính: Hermes **có** adapter Feishu/Lark đầy đủ — WebSocket mode, card
action, media, ACL per-group. Lượt trước mình nói phải viết adapter là sai.

Nhưng vẫn **không nên** để Hermes làm orchestrator, vì:

- Hermes là agent một-người-dùng, không có khái niệm "gọi API bằng token của
  nhân viên X". Phần multi-user OAuth vẫn phải tự viết.
- Doc của chính Hermes ghi: một app_id chỉ một instance Hermes dùng được. Nếu
  Hermes chiếm app_id thì bot meeting phải là app thứ hai.
- Transcript là input không tin cậy. Hermes có shell, subagent, tự ghi memory
  và tự tạo skill → prompt injection qua lời nói trong cuộc họp có thể tồn tại
  lâu dài.

Chỗ dùng Hermes đúng là **thay tầng recap ở [4]**, chạy container isolation,
tắt toolset shell. Và làm bot Q&A riêng "hỏi về các cuộc họp của tôi" — tách
app, tách quyền, không đụng vào pipeline.

Đề xuất: **đừng cắm Hermes ở v1**. Dùng OpenAI cho recap. Khi pipeline chạy ổn
định 2 tuần rồi mới thay tầng recap. Cắm sớm thì không biết lỗi từ đâu.

---

## 8. Phải kiểm chứng trước khi viết dòng code nào

| # | Giả định | Vì sao rủi ro | Cách kiểm |
|---|---|---|---|
| 1 | `vc.meeting.recording_ready_v1` bắn cho họp thường | Doc Lark ghi *"for meetings booked by Open API only"* — có thể **không** bắn cho họp mở tay hoặc từ lịch | Bật event, họp thử 2 phút có ghi hình, xem log |
| 2 | Card action chạy trong WebSocket mode | Thiếu bước cấu hình → lỗi **200340** lúc bấm nút, mà lúc *gửi* card vẫn thành công nên rất dễ bỏ sót | Bật Interactive Card + subscribe `card.action.trigger` + gửi card thử rồi bấm |
| 3 | Admin tenant duyệt scope | `minutes`, `vc:record:readonly`, `calendar`, `contact` + publish cho toàn công ty. **Đây là rào lớn nhất và không phải việc kỹ thuật** | Hỏi admin TRƯỚC |
| 4 | Refresh token 30 ngày, xoay vòng mỗi lần refresh | Lưu nhầm bản cũ = mất phiên của người đó | Viết test refresh chạy 3 vòng liên tiếp |
| 5 | Không bấm ghi hình = không có gì | Giới hạn của V1 còn nguyên | Cân nhắc: bot nhắc chủ phòng bật ghi khi lịch có tag |
| 6 | 1 GPU đủ tải | 10 cuộc họp tan lúc 17h, large-v3 ~0.1–0.2× realtime → người cuối chờ ~2 tiếng | Đo tỉ lệ realtime thật, hiện ETA trong card "đang xử lý" |

Kiểm 1, 2, 3 xong mới bắt đầu. Riêng #3 mà không được duyệt thì cả thiết kế
này vô nghĩa.

---

## 9. Khối lượng ước tính

| Phần | Dòng | Thời gian |
|---|---|---|
| Orchestrator local (event, OAuth, token, queue, phiên âm, card, phân phối) | 1.200–1.600 | 2–3 tuần |
| Mở rộng web portal (+ hộp thư OAuth) | ~700 | 4–6 ngày |
| Kiểm chứng + chờ admin duyệt | — | ~1 tuần |

Nặng nhất là token store và OAuth, không phải phần meeting. Đó là cái giá của
việc bỏ `lark-cli`.

### Thứ tự làm

1. Kiểm chứng mục 8 (#1, #2, #3)
2. WebSocket event + hộp thư OAuth trên portal + token store. Enroll được 1
   người là mốc đầu.
3. Queue + phiên âm. Xong 1 cuộc họp đầu-cuối là mốc hai.
4. Card duyệt + phân phối. Chạy đủ vòng cho 1 phòng ban.
5. Portal admin.
6. Mở rộng toàn công ty.

Đừng làm portal trước — nó là thứ dễ nhất và không chứng minh được gì.

---

## 10. Những gì tái dùng được từ V1

| Từ V1 | Dùng lại thế nào |
|---|---|
| `transcribe_server/` | Giữ nguyên, không sửa |
| `meeting_poller.py` — logic dò lịch, cửa sổ ±3h, `resolve_participants` | Port sang gọi API trực tiếp |
| `meeting_delivery.py` — ffmpeg, prompt recap | Port gần như nguyên |
| `web-portal` — layout, Bitable helper, admin auth | Mở rộng |
| `poller_state.json` | Bỏ, thay bằng SQLite unique key |
| `lark-cli`, `enroll_user.py`, `users.json` | Bỏ |
| `mcp.zip`, `/api/generate`, Vercel Blob | Bỏ |
| `install-autostart.bat` — Task Scheduler | Dùng lại, giờ là **bắt buộc** chứ không tuỳ chọn |
| `run-multi-poller.bat`, `webhook_server.py` | Bỏ |
