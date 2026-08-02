# Luồng tự động Meeting Note — Lark × Whisper

Cuộc họp kết thúc → tự phát hiện → phiên âm → sinh recap → gửi biên bản
cho những người được mời. Không cần ai yêu cầu.

---

## Ở ĐÂU CÓ GÌ (đọc cái này trước)

Bốn phần chạy trên **cùng một máy**, nhưng nằm bốn chỗ. Đây là bản đồ:

| Phần                           | Đường dẫn                                   | Trong repo?               | Làm gì                                                                                                 |
| ------------------------------ | ------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------ |
| **V2** (đang dùng)             | `v2/` — `python -m v2`                      | ✅                        | não: quét họp → phiên âm → recap → phát → ghi Base                                                     |
| **V1** (bản cũ, để tham chiếu) | `v1/`                                       | ✅                        | bản đã chạy production trước V2. **So với nó trước khi tự nghĩ** — nhiều lỗi V2 là lỗi port sai từ đây |
| **Whisper server**             | `E:\whisper\` — `run-server.bat`, cổng 8000 | ❌                        | phiên âm audio → text (CPU, model `small`)                                                             |
| **Hermes** (bot + recap)       | `%LOCALAPPDATA%\hermes\`                    | ❌ (trừ plugin, xem dưới) | bot hỏi đáp trong Lark **và** sinh recap cho V2 qua cổng 8642                                          |
| Plugin cửa vào của Hermes      | `hermes/v2-enroll-gate/`                    | ✅ **bản gốc**            | chặn người chưa cấp quyền OAuth. Cài sang Hermes: `hermes\install-plugin.bat`                          |
| Hộp thư OAuth + dashboard      | `v2/vercel-oauth/` → Vercel                 | ✅                        | nhận code enroll, hiện trạng thái từ xa                                                                |

Các file `.bat` ở gốc repo là **cửa vào của V2**, đừng di chuyển (Hermes trỏ
đường dẫn tuyệt đối vào `mcp-meetings.bat` và `v2-gate.bat`):

| File                       | Ai gọi                                                            |
| -------------------------- | ----------------------------------------------------------------- |
| `start-v2.bat`             | người — bật whisper + doctor + vòng `run` (dry-run, có `pause`)   |
| `run-v2-auto.bat`          | máy — bản không tương tác, `--send`, tự bật lại (§16)             |
| `install-autostart-v2.bat` | người — đăng ký/gỡ tự chạy cho V2 (`/go` = gỡ, `/trangthai` = xem) |
| `hermes-watchdog.bat`      | máy — canh gateway Hermes, chết/đơ thì bật lại (§13)              |
| `mcp-meetings.bat`         | Hermes — MCP server dữ liệu họp                                   |
| `v2-gate.bat`              | plugin Hermes — cửa vào bot                                       |

⚠️ Mọi `.bat` ở đây phải **ASCII thuần + CRLF** (§16 bẫy 1). Kiểm:
`python -c "b=open('x.bat','rb').read(); print(sum(1 for c in b if c>127), b.count(b'\r\n'))"`

### Cần gì thì đọc file nào

| Muốn                               | Đọc                                                                                            |
| ---------------------------------- | ---------------------------------------------------------------------------------------------- |
| Việc cần làm tiếp (giao cho phiên mới) | **[docs/V2_VIEC_CAN_LAM.md](docs/V2_VIEC_CAN_LAM.md)** |
| **Sang máy mới / dựng lại từ đầu** | **[docs/MAY_MOI.md](docs/MAY_MOI.md)**                                                         |
| Đang ở đâu, việc kế tiếp là gì     | [docs/V2_HANDOFF.md](docs/V2_HANDOFF.md)                                                       |
| Chạy hằng ngày, hỏng thì tra ở đâu | [docs/V2_MAINTENANCE.md](docs/V2_MAINTENANCE.md)                                               |
| Vì sao thiết kế như vậy            | [docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md), [docs/V2_LONGTERM.md](docs/V2_LONGTERM.md) |

> ⚠️ Phần còn lại của file này là **hồ sơ của V1** (viết 27–28/07/2026), giữ lại
> làm tham chiếu. Đường dẫn trong đó nay đã chuyển vào `v1/`.

---

## Bối cảnh

|         |                                                 |
| ------- | ----------------------------------------------- |
| Công ty | CÔNG TY TNHH KINH DOANH THƯƠNG MẠI HTC VIỆT NAM |
| Thư mục | `E:\meetingxlark`                               |

**Yêu cầu gốc (27/07):** chuyển meeting note từ chế độ _chờ người yêu cầu_
sang _tự chạy khi họp xong_. Anh Thiện đã có sẵn bản on-demand qua MCP tool
`lark_meeting_note` (Whisper CUDA, large-v3). Phần mới cần làm là **cơ chế
tự động phát hiện và phân phối**.

### Mã định danh hay dùng

| Thứ                                  | Giá trị                               |
| ------------------------------------ | ------------------------------------- |
| open_id của Thẩm                     | `ou_1bc55b6d5b20ee06cbee1326d5b72715` |
| union_id của Thẩm                    | `on_1f34d05d9ce11997c545cf1ec136bc41` |
| union_id của anh Thiện               | `on_a505446a2f87c04350b07d91ef1475f2` |
| App "Meeting Agent CĐS" (của Thẩm)       | `cli_aae288361ef89eed`                |
| App "AI Agent Assistant" (anh Thiện) | `cli_a9bd0ff8d6619ed1`                |

---

## Luồng hoạt động

```
Họp xong (BẮT BUỘC có bấm ghi hình)
  ↓
[1] Poller quét Lark Minutes mỗi 5 phút
      thấy minute mới → ghi mốc, chờ 3 phút cho Lark xử lý xong
  ↓
[2] Dò lịch tìm cuộc họp tương ứng, lấy danh sách người được mời
  ↓
[3] Tải bản ghi về, tách âm thanh bằng ffmpeg (giảm ~92% dung lượng)
  ↓
[4] Whisper phiên âm  →  OpenAI sinh recap
  ↓
[5] Bot gửi recap (thẻ) + file transcript cho từng người
```

Toàn bộ chạy dưới **app riêng** (`cli_aae288361ef89eed`) qua profile
lark-cli tên `mine`. Không còn phụ thuộc app của anh Thiện.

---

## File trong dự án (V1 — nay nằm trong `v1/`)

> Từ 31/07/2026 toàn bộ V1 đã chuyển vào `v1/`. Các `.bat` đều `cd /d "%~dp0"`
> nên chạy nguyên như trước, chỉ khác chỗ bấm. Dữ liệu runtime (`jobs/`, `out/`,
> `minutes/`, `poller_state.json`) cũng đã chuyển theo và **thôi bị commit**.

| File                    | Vai trò                                                                                                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `setup.bat`             | Cài đặt cho máy mới. Chạy đầu tiên.                                                                                                                                             |
| `config.bat`            | **Cấu hình riêng** — key, secret, profile. ⚠️ File này đang bị **tracked** trong git (commit `985573f`, repo private) và chứa `OPENAI_API_KEY` + `SENDER_APP_SECRET` dạng trần. |
| `check_config.py`       | Kiểm tra toàn bộ cấu hình, gọi thử OpenAI và Lark thật. Chạy trước khi nghi ngờ code.                                                                                           |
| `run-meeting-note.bat`  | Khởi động poller. Tự bật Whisper server rồi chờ sẵn sàng.                                                                                                                       |
| `run-listener.bat`      | Khởi động event listener (chạy song song, tuỳ chọn).                                                                                                                            |
| `install-autostart.bat` | Đăng ký Task Scheduler. Menu cài/gỡ/xem trạng thái.                                                                                                                             |
| `meeting_poller.py`     | Bước 1–2: quét Minutes, dò lịch, tạo job.                                                                                                                                       |
| `meeting_delivery.py`   | Bước 3–5: transcript, recap, gửi.                                                                                                                                               |
| `whisper_worker.py`     | Client HTTP gọi Whisper server.                                                                                                                                                 |
| `lark_sender.py`        | Gửi tin bằng app riêng, gọi API trực tiếp.                                                                                                                                      |
| `event_listener.py`     | Nghe event Lark, kích hoạt quét ngay.                                                                                                                                           |
| `webhook_server.py`     | _Không dùng._ Viết cho chế độ webhook trước khi biết Lark có persistent connection.                                                                                             |
| `poller_state.json`     | Nhớ minute đã xử lý và mốc thấy lần đầu.                                                                                                                                        |
| `jobs/`, `jobs/done/`   | Job đang chờ và đã xong.                                                                                                                                                        |
| `out/`                  | Transcript, tên `Bien ban - <tên họp> <ngày giờ>.txt`                                                                                                                           |

---

## Cấu hình

Tất cả trong `config.bat`:

| Biến                                  | Ý nghĩa                                                |
| ------------------------------------- | ------------------------------------------------------ |
| `LARK_PROFILE`                        | `mine` = đọc bằng app riêng. Để trống = app anh Thiện. |
| `OPENAI_API_KEY`                      | Key sinh recap. Để trống thì chỉ gửi transcript.       |
| `LLM_MODEL`                           | Mặc định `gpt-4o-mini`                                 |
| `SENDER_APP_ID` / `SENDER_APP_SECRET` | App gửi tin. Để trống thì quay về lark-cli.            |
| `SENDER_ID_TYPE`                      | Mặc định `union_id`                                    |
| `EVENT_APP_SECRET`                    | Chỉ cho event listener                                 |
| `WHISPER_DIR`                         | Thư mục chứa `run-server.bat`. Điền thì tự bật server. |

Trong `run-meeting-note.bat`:

| Biến                | Ý nghĩa                                                 |
| ------------------- | ------------------------------------------------------- |
| `TRANSCRIPT_SOURCE` | `whisper` hoặc `lark` (transcript sẵn có, nhanh)        |
| `WHISPER_URL`       | Đổi sang server anh Thiện khi có endpoint               |
| `POLL_INTERVAL`     | Giây giữa hai lần quét (mặc định 300)                   |
| `SEND_MODE`         | `--send` hoặc `--dry-run`                               |
| `DELIVER_TO`        | Ép gửi cho 1 người. Thêm `REM` để gửi cho cả danh sách. |

**Lưu ý:** `.bat` chỉ đọc cấu hình lúc khởi động. Sửa `config.bat` xong
phải khởi động lại poller.

---

## Cách chạy

```
run-meeting-note.bat        poller (cửa sổ 1)
run-listener.bat            listener, tuỳ chọn (cửa sổ 2)
```

Kiểm tra trước khi nghi ngờ code:

```
cmd /c "call config.bat && python check_config.py"
```

Debug từng bước:

```
python meeting_poller.py --once --dry-run
python meeting_delivery.py --dry-run --to on_1f34d05d9ce11997c545cf1ec136bc41
python meeting_delivery.py --source lark --to on_...
python whisper_worker.py "minutes\<token>\<file>.mp4"
cmd /c "call config.bat && python lark_sender.py on_1f34d05d9ce11997c545cf1ec136bc41"
```

Chạy lại một cuộc họp: xoá token khỏi `seen_tokens` trong
`poller_state.json`, khởi động lại poller.

---

## Thiết lập lark-cli profile riêng

lark-cli chỉ gắn được **một app mỗi profile**, và profile mặc định là app
anh Thiện (dùng chung cho MCP của cả team). Tạo profile riêng:

```
call config.bat
echo %SENDER_APP_SECRET%| lark-cli profile add --name mine --app-id cli_aae288361ef89eed --app-secret-stdin --brand lark
lark-cli auth login --profile mine --domain all
```

**Tuyệt đối không chạy `lark-cli profile use`** — lệnh đó đổi profile mặc
định và sẽ làm hỏng MCP cả team đang dùng. Chỉ truyền `--profile mine`.

Kiểm tra:

```
lark-cli minutes +search --participant-ids me --start 2026-07-25 --profile mine
lark-cli calendar +agenda --profile mine
```

---

## Phát hiện kỹ thuật

Đều **đã kiểm chứng bằng dữ liệu thật**. Đây là phần tốn thời gian nhất.

### open_id gắn với từng app

Mã `ou_...` do app nào cấp thì chỉ app đó hiểu. Gửi mã của app A qua app B
sẽ nhận lỗi `open_id cross app` (code 99992361).

| Loại       | Phạm vi                        | Quyền cần thêm                      |
| ---------- | ------------------------------ | ----------------------------------- |
| `open_id`  | Riêng từng app                 | Không                               |
| `user_id`  | Toàn công ty                   | `contact:user.employee_id:readonly` |
| `union_id` | Chung giữa các app cùng tenant | **Không**                           |

→ Dùng `union_id`. Chạy xuyên app mà không tốn thêm vòng duyệt quyền.

### Quyền có hai tầng

Cấp quyền trong Console **chưa đủ**. Người dùng còn phải cấp quyền đó cho
app qua `auth login`. Thiếu tầng nào cũng báo `missing_scope`.

Ngoài ra Console có mục **Availability** riêng: app chỉ phát hành cho vài
người thì bot gửi tin cho người ngoài danh sách sẽ nhận lỗi
`Bot has NO availability to this user` (code 230013). Phải đổi sang toàn
công ty rồi tạo version mới.

### Lark trả thời gian không kèm múi giờ

Chuỗi `Start time: 2026.07.28 16:35:10` thực tế là **UTC+8**
(Asia/Shanghai) trong khi máy chạy UTC+7 — lệch đúng một tiếng.

Hệ quả ban đầu: cơ chế "chờ 3 phút sau khi họp xong" tính ra thời điểm kết
thúc nằm ở tương lai, điều kiện chờ luôn đúng → **kẹt vô hạn**.

Bản vá đầu (chống kẹt) lại gây lỗi khác: bỏ qua bước chờ nên hỏi
`vc +recording` quá sớm, lúc Lark chưa liên kết bản ghi với cuộc họp → trả
rỗng → không tra được người được mời.

→ Cách sửa đúng: **không dùng giờ Lark trả về nữa**. Poller tự ghi mốc
thời điểm mình thấy minute lần đầu (`first_seen` trong state) rồi đếm từ
đó. Miễn nhiễm với mọi vấn đề múi giờ.

### Các phần của Lark sẵn sàng ở thời điểm khác nhau

Minute xuất hiện trong `minutes +search` **trước**, nhưng liên kết trong
`vc +recording` đến sau vài phút. Hỏi sớm thì trả rỗng, không báo lỗi.

### Chuỗi lấy người được mời

```
calendar +agenda                    → sự kiện quanh giờ họp
calendar +meeting --event-ids X     → meeting_id
vc +recording --meeting-ids Y       → minute_token
   khớp minute_token?               → đúng sự kiện
calendar event.attendees list       → danh sách union_id + open_id
```

Ưu tiên sự kiện **trùng tên** với minute, rồi tới **gần giờ nhất**. Giới
hạn 12 sự kiện mỗi lần vì mỗi sự kiện tốn 3 lệnh gọi API.

Hướng qua `vc +search` **không dùng được** — trả rỗng với họp thường, và
không nhận alias `me`, phải truyền open_id đầy đủ.

**Ràng buộc:** cuộc họp phải được đặt qua **Calendar**. Họp mở trực tiếp
không có sự kiện lịch → không tra được → rơi về gửi cho chủ tài khoản.

### Token tự gia hạn theo cửa sổ trượt

`refreshExpiresAt` = **lần refresh gần nhất + 7 ngày**, không tính từ lúc
cấp. Poller chạy mỗi 5 phút nên token tự gia hạn vô thời hạn. Chỉ khi
không gọi API nào suốt 7 ngày liền mới phải `lark-cli auth login`.

### Bẫy Windows

- **`lark-cli` là file `.cmd`** (npm). `subprocess.run(["lark-cli"])` dính
  `FileNotFoundError [WinError 2]`. Phải dùng `shutil.which()`.
- **`im +messages-send --file` từ chối đường dẫn tuyệt đối.** Transcript
  phải ghi vào `out/` (tương đối).
- **File `.bat` không được có dấu tiếng Việt.** cmd.exe đọc lệch byte với
  UTF-8, nuốt ký tự đầu dòng (`TRANSCRIPT_SOURCE` → `RANSCRIPT_SOURCE`).
- **Sửa `.bat` bằng script phải dùng CRLF.** Thay chuỗi nhiều dòng bằng
  `\n` sẽ không khớp và **fail âm thầm**, tạo ra file lai nửa cũ nửa mới.
- **PowerShell không có `call` và `cd /d`.** Dùng cmd, hoặc
  `cmd /c "call config.bat && python ..."`.
- **Truyền cấu hình quan trọng bằng tham số dòng lệnh**, không qua biến
  môi trường — đã có lần `.bat` và Python báo hai chế độ mâu thuẫn nhau.

### `minutes +download` chỉ trả mp4

Không chọn được định dạng. Tách âm thanh bằng ffmpeg sau khi tải:
`-vn -ac 1 -ar 16000 -c:a pcm_s16le` (WAV PCM lossless). Whisper vốn chuyển
về 16kHz mono nên bỏ hình + gộp mono + hạ sample-rate không mất gì.

**Không nén xuống MP3 32k.** Bản đầu dùng `-b:a 32k` thêm một tầng lossy
thứ hai ở bitrate rất thấp; Whisper giải mã audio đã hỏng đó sinh ra ảo
giác, lặp câu, nuốt chữ — nặng nhất ở đoạn nói nhỏ và âm tiết có dấu. Câu
"16kHz mono không mất gì" chỉ đúng với PCM lossless, sai ngay khi thêm nén
bitrate thấp. Audio chỉ POST sang Whisper qua `localhost:8000` nên **không**
dính giới hạn 30MB của Lark IM — không có lý do gì phải crush bitrate.

| Độ dài họp | mp4 gốc | WAV 16k mono | Whisper CPU |
| ---------- | ------- | ------------ | ----------- |
| 30 phút    | ~87 MB  | ~55 MB       | 0,9 giờ     |
| 1 tiếng    | ~174 MB | ~110 MB      | 1,7 giờ     |
| 2 tiếng    | ~348 MB | ~220 MB      | 3,4 giờ     |
| 4 tiếng    | ~696 MB | ~440 MB      | **6,9 giờ** |

Cần nhỏ hơn thì dùng **FLAC** (lossless, ~nửa WAV) hoặc **Opus 24–32k**
(tốt hơn hẳn MP3 cùng bitrate) — đừng quay lại MP3 32k.

Giới hạn gửi file qua Lark IM (chỉ áp cho transcript `.txt` gửi người
dùng, không áp cho audio): **30 MB**.

---

## Số liệu đo được

**Whisper medium trên CPU:** audio 10 phút 57 giây → **18 phút 52 giây**,
khoảng **0,58× realtime**. Audio ngắn có khoảng lặng nhiều thì còn chậm
hơn (1 phút → 4 phút).

→ Server chính **bắt buộc phải có GPU**. Đây là giới hạn của máy local,
không phải của kiến trúc.

**So sánh chất lượng** (cùng cuộc họp "Web scraper"):

| Nguồn          | Số từ | Ghi chú                                  |
| -------------- | ----- | ---------------------------------------- |
| Lark Minutes   | 1396  | Có nhãn người nói, gộp thành đoạn        |
| Whisper medium | 1245  | Timestamp từng câu, không tách người nói |

Whisper chạy kèm `initial_prompt` nhồi glossary (MCP, Claude, Node.js,
Lark, connector, authorize, terminal, macOS, Windows, restart). Bản test
không có glossary thì ra "mờ cp", "cây lau", "green" thay vì MCP, Claude,
Win.

---

## Event callback

**Chế độ:** Lark có hai lựa chọn. Chọn **persistent connection** thay vì
webhook — không cần địa chỉ công khai, không cần ngrok/cloudflared, không
phải publish lại mỗi lần đổi URL. Cần SDK `lark-oapi`.

**Event dùng được:** `minutes.minute.generated_v1` — bắn đúng lúc minute
được sinh ra, thay thế một-đổi-một cho việc quét `minutes +search`.

**Event không dùng được:** `vc.meeting.recording_ready_v1` chỉ bắn với cuộc
họp **đặt qua Open API**, không áp dụng cho họp tạo từ calendar UI.

**Event chỉ để quan sát:** `vc.meeting.all_meeting_ended_v1` — cấp tenant,
nhận được thông báo toàn công ty nhưng không đọc được nội dung.

**Thiết kế:** event chỉ là **chuông báo**, gọi `run_once()` của poller thay
vì tự xử lý payload. Dùng lại toàn bộ logic sẵn có, không phải đoán cấu
trúc payload, chống trùng tự động. Nhiều event dồn dập chỉ kích hoạt một
vòng quét (chờ 30 giây).

**Vẫn giữ polling.** Event rớt là mất luôn — máy tắt, mạng chập chờn,
listener chết. Persistent connection không phát lại event cũ. Polling hỏi
_trạng thái hiện tại_ nên không quan tâm đã bỏ lỡ gì, cộng `LOOKBACK_DAYS
= 2` nên máy tắt hai ngày bật lên vẫn hứng đủ.

---

## Vì sao chưa phủ được toàn công ty

| Scope                             | Loại token | Phạm vi              |
| --------------------------------- | ---------- | -------------------- |
| `vc:meeting.all_meeting:readonly` | **Tenant** | Toàn công ty         |
| `minutes:minutes.basic:read`      | **User**   | User-specific access |
| `vc:recording:read`               | **User**   | User-specific access |

App **nhận được** thông báo mọi cuộc họp trong công ty kết thúc, nhưng
**không đọc được** minute của cuộc họp mình không dự.

Hai đường đi tiếp:

1. **OAuth từng người** — mỗi nhân viên tự authorize, server lưu refresh
   token riêng, poller lặp qua từng người. Chắc chắn được nhưng nặng.
2. **Cho bot tham gia cuộc họp** — scope `vc:meeting.bot.join:write` đã
   cấp. Nếu bot là người dự thì nó đọc được minute, và rào cản trên có thể
   biến mất mà không cần OAuth. **Chưa kiểm chứng, đáng thử trước.**

Mặt tích cực của giới hạn hiện tại: ranh giới kỹ thuật trùng với ranh giới
nên có. Không ai đọc được cuộc họp mình không dự, kể cả người dựng hệ
thống.

---

## Hạn chế đã biết

**Phải có người bấm ghi hình.** Không record thì không sinh Minutes, luồng
đứng im và không báo lỗi.

**Cuộc họp phải đặt qua Calendar** mới lấy được danh sách người được mời.

**Whisper không tách người nói.** Cần diarization thì ghép WhisperX hoặc
pyannote. Lark Minutes có sẵn nhãn `Speaker 1`.

**Máy phải mở.** Poller và Whisper server chạy trên máy cá nhân. Máy tắt
thì dừng, nhưng quét lùi 2 ngày nên bật lên sẽ bắt lại.

**Xử lý tuần tự.** Một cuộc họp dài chặn cả hàng đợi.

**Job của Whisper server lưu trong RAM**, tắt server là mất.

---

## Việc tiếp theo

### Rẻ, nên làm trước

- **Cảnh báo khi hỏng** — mỗi tối bot gửi tổng kết: xử lý mấy cuộc, lỗi
  gì. Im lặng nhiều ngày = dấu hiệu hỏng. (~30 phút)
- **Lùi dần khi lỗi** — đếm số lần thử, giãn dần, quá 5 lần thì bỏ và báo.
  Đã làm một phần: gửi hỏng một người không còn làm hỏng cả job.
- **LLM dọn transcript** — cho gpt-4o-mini sửa thuật ngữ trên toàn văn.
  Transcript thô là thứ giao tận tay người đọc nên đáng đầu tư. (~30 phút)

### Nặng hơn

- Tách người nói (WhisperX) — khoảng cách lớn nhất so với Lark Minutes
- Gửi vào nhóm chat thay vì DM từng người
- Hàng đợi xử lý song song

### Dài hạn

- Lên server GPU của anh Thiện (đổi `WHISPER_URL`; nếu chuyển cả poller thì
  vướng chuyện đăng nhập lark-cli trên máy không màn hình)
- Thử hướng bot tham gia cuộc họp trước khi làm OAuth từng người

---

## Còn treo — cần hỏi anh Thiện

- Endpoint server chính: HTTP hay queue, format payload, API key, trả kết
  quả kiểu poll hay callback, có được sửa server không
- Model cho luồng auto: medium + faster-whisper hay large-v3
- Chu kỳ polling: 5 hay 15 phút
- Có làm giai đoạn 2 (phủ toàn công ty) không, và theo hướng nào

---

## Liên kết

- Task trong Lark:
  https://applink.larksuite.com/client/todo/detail?guid=1c9436d0-8fee-4f2e-acdb-238661ed4b02
- Doc kế hoạch:
  https://o4pvcegwn6b.sg.larksuite.com/docx/O94yd2SjGohBD6xfB16lDMXlgFh
- Developer Console: https://open.larksuite.com/app/cli_aae288361ef89eed
- Whisper server local: http://localhost:8000 — `/docs` có giao diện thử API
