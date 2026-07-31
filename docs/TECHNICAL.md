# Tài liệu kỹ thuật — Luồng tự động Meeting Note (Lark × Whisper)

Tài liệu này giải thích **toàn bộ kỹ thuật** đằng sau hệ thống: từng quyết định
thiết kế, từng cạm bẫy đã gặp và cách xử lý, kèm số liệu đo thật. Đọc `README.md`
trước để nắm cách chạy; đọc file này khi muốn hiểu *vì sao* mọi thứ được làm như vậy.

Tất cả phát hiện dưới đây đều **đã kiểm chứng bằng dữ liệu thật** trên tenant
`hapas.vn`, không phải suy đoán từ tài liệu.

---

## 1. Kiến trúc tổng thể

Hệ thống là một pipeline sự-kiện-kéo (event-pull) gồm hai tiến trình Python chạy
song song, dùng chung một thư mục trạng thái.

```
                    ┌─────────────────────────────────────────┐
   Lark tenant      │  meeting_poller.py   (vòng lặp 5 phút)   │
   (Minutes,        │    B1  quét Minutes  → thấy minute mới   │
    Calendar,       │    B2  dò lịch → danh sách người được mời │
    VC recording)   │        → ghi 1 job JSON vào jobs/         │
        │           └───────────────────┬─────────────────────┘
        │                               │  (file job)
        │           ┌───────────────────▼─────────────────────┐
        │           │  meeting_delivery.py                     │
        └──────────►│    B3  tải mp4 → tách WAV (ffmpeg)        │
                    │    B4  Whisper phiên âm → OpenAI recap    │
                    │    B5  gửi recap + transcript cho từng    │
                    │        người (lark_sender / lark-cli)     │
                    └──────────────────────────────────────────┘

   event_listener.py  (tùy chọn, chạy song song)
     nghe minutes.minute.generated_v1 qua persistent connection
       → chỉ gọi poller.run_once()  (đóng vai "chuông báo")
```

Nguyên tắc thiết kế cốt lõi: **poller là nguồn sự thật duy nhất**. Event listener
không tự xử lý payload — nó chỉ kích hoạt một vòng quét. Nhờ vậy toàn bộ logic
nằm một chỗ, không phải đoán cấu trúc payload, và chống trùng tự động (xem §12).

---

## 2. Mô hình định danh của Lark

Đây là phần dễ sai nhất khi làm việc với API Lark. Lark có **ba loại ID** cho một
người, phạm vi khác nhau:

| Loại | Phạm vi | Quyền cần thêm | Dùng khi |
|---|---|---|---|
| `open_id` (`ou_...`) | **Riêng từng app** | Không | Trong nội bộ một app |
| `user_id` | Toàn công ty | `contact:user.employee_id:readonly` | Ít dùng |
| `union_id` (`on_...`) | **Chung giữa các app cùng tenant** | **Không** | Gửi/tra xuyên app |

### Cạm bẫy: open_id gắn cứng với app cấp ra nó

Mã `ou_...` do app A cấp thì **chỉ app A hiểu**. Đưa open_id của app A cho app B
gọi API sẽ nhận lỗi:

```
code 99992361  "open_id cross app"
```

Đây là lỗi có thật đã gặp: poller đọc danh bạ bằng app anh Thiện (`cli_a9bd0ff8d6619ed1`),
nhưng bot gửi tin lại chạy dưới app riêng (`cli_aae288361ef89eed`). open_id của
người dự do app anh Thiện cấp không dùng được để gửi qua app riêng.

**→ Giải pháp: luôn dùng `union_id` cho mọi thao tác xuyên app.** union_id chung
cho mọi app trong cùng tenant, không tốn thêm vòng duyệt quyền, và không dính lỗi
cross-app. Đây là lý do `SENDER_ID_TYPE` mặc định là `union_id`.

Lưu ý thực tế: `contact_search_user` (tra danh bạ) **chỉ trả `open_id`**, không
trả `union_id`. Nên nếu lấy người dự từ danh bạ để gửi, phải có thêm một bước
đổi `open_id → union_id`. Đường lấy người dự chuẩn (§6) trả thẳng cả hai.

---

## 3. Quyền có hai tầng + Availability

Cấp quyền trong Developer Console **chưa đủ** để gọi được API. Có ba lớp phải
khớp đồng thời:

1. **Console scope** — bật quyền trong Console (VD `minutes:minutes.basic:read`).
2. **User grant** — người dùng phải cấp đúng quyền đó cho app qua `auth login`.
   Thiếu lớp này báo `missing_scope` dù Console đã bật.
3. **Availability** — Console có mục riêng quy định app phát hành cho *ai*. Nếu
   app chỉ phát cho vài người, bot gửi tin cho người ngoài danh sách sẽ nhận:
   ```
   code 230013  "Bot has NO availability to this user"
   ```
   Phải đổi Availability sang **toàn công ty** rồi tạo **version mới** (chỉ đổi
   không tạo version thì chưa có hiệu lực).

Bài học: khi gặp lỗi quyền, kiểm tra đủ **cả ba lớp**, không chỉ Console.

---

## 4. Múi giờ: Lark trả thời gian không kèm offset

Chuỗi Lark trả về dạng `Start time: 2026.07.28 16:35:10` thực chất là **UTC+8**
(Asia/Shanghai), trong khi máy chạy **UTC+7** (Asia/Ho_Chi_Minh) — lệch đúng 1 giờ,
và **không có ký hiệu múi giờ** trong chuỗi.

### Chuỗi lỗi dây chuyền đã gặp

1. Cơ chế "chờ 3 phút sau khi họp xong" lấy giờ kết thúc từ Lark (UTC+8), so với
   `now()` của máy (UTC+7). Giờ kết thúc bị hiểu lệch +1h thành **tương lai** →
   điều kiện "đã qua 3 phút" **không bao giờ đúng** → **kẹt vô hạn**.
2. Bản vá đầu bỏ hẳn bước chờ để chống kẹt → lại hỏi `vc +recording` **quá sớm**,
   lúc Lark chưa liên kết bản ghi với cuộc họp → trả rỗng → không tra được người dự.

### Cách sửa đúng: không tin giờ của Lark nữa

Poller **tự ghi mốc** thời điểm mình *thấy minute lần đầu* (`first_seen` trong
`poller_state.json`), rồi đếm từ đó. Mốc này sinh bằng đồng hồ máy, không dính
múi giờ của Lark → **miễn nhiễm** với mọi vấn đề offset.

Bài học tổng quát: khi một API trả timestamp không rõ múi giờ, đừng đoán — hãy
tự sinh mốc thời gian ở phía mình cho những phép so tương đối.

---

## 5. Các thành phần Lark sẵn sàng ở thời điểm khác nhau

Một cuộc họp không "xong" đồng loạt trên mọi API. Quan sát thực tế:

- Minute xuất hiện trong `minutes +search` **trước**.
- Liên kết trong `vc +recording` đến **sau vài phút**.
- Hỏi `vc +recording` sớm thì **trả rỗng, không báo lỗi** (im lặng, dễ tưởng
  không có bản ghi).

**→ Thiết kế phòng thủ:** sau khi thấy minute mới, poller **chờ 3 phút** (đếm từ
`first_seen`, §4) rồi mới bắt đầu dò `vc +recording`. Đây là lý do có bước chờ,
và lý do bước chờ phải dựa trên mốc tự sinh chứ không phải giờ Lark.

---

## 6. Chuỗi API lấy danh sách người được mời

Không có một API "cho tôi người dự của minute X". Phải nối bốn lệnh:

```
calendar +agenda                    → các sự kiện quanh giờ họp
calendar +meeting --event-ids X     → meeting_id
vc +recording --meeting-ids Y       → minute_token
   so khớp minute_token?            → đúng sự kiện chưa
calendar event.attendees list       → danh sách union_id + open_id
```

Quy tắc chọn sự kiện: ưu tiên sự kiện **trùng tên** với minute, rồi tới **gần
giờ nhất**. Giới hạn **12 sự kiện mỗi lần** vì mỗi sự kiện tốn 3 lời gọi API —
quét rộng hơn thì chậm và tốn quota.

### Đường không dùng được

- `vc +search` trả rỗng với họp thường, và **không nhận alias `me`** — phải
  truyền `open_id` đầy đủ. Đã thử, bỏ.

### Ràng buộc cứng

Cuộc họp **phải được đặt qua Calendar** mới có sự kiện lịch để tra người dự.
Họp mở trực tiếp (không qua lịch) → không có attendees → hệ thống **rơi về gửi
cho chủ tài khoản** thay vì cả danh sách.

---

## 7. Token tự gia hạn theo cửa sổ trượt

`refreshExpiresAt` = **lần refresh gần nhất + 7 ngày**, *không* tính từ lúc cấp
ban đầu. Vì poller gọi API mỗi 5 phút nên token được refresh liên tục → hạn 7
ngày bị đẩy lùi liên tục → **token tự gia hạn vô thời hạn**.

Chỉ khi không gọi API nào suốt **7 ngày liền** (máy tắt dài ngày) mới phải chạy
lại `lark-cli auth login`.

---

## 8. Xử lý audio: mp4 → WAV (KHÔNG dùng MP3 32k)

`minutes +download` **chỉ trả mp4**, không chọn được định dạng. Vì Whisper vốn
chuyển mọi input về **16kHz mono**, ta tách audio và bỏ hình để giảm dung lượng
truyền tải.

### Sai lầm ban đầu và cách sửa

Bản đầu tách sang **MP3 mono 16kHz 32 kbps** (`-b:a 32k`) với lập luận "Whisper
về 16kHz mono nên không mất gì". Lập luận này **sai một nửa**: hạ sample-rate +
gộp mono thì vô hại, nhưng **nén xuống 32 kbps là lossy** — thêm một tầng mất mát
thứ hai trên nền audio vốn đã nén (AAC 96k trong mp4).

Audio lossy bitrate thấp là tác nhân kinh điển khiến Whisper **ảo giác, lặp câu,
nuốt chữ** — nặng nhất ở đoạn nói nhỏ, khoảng lặng, và âm tiết tiếng Việt có dấu.
Đây chính là lý do "để nguyên mp4 lại ít lỗi hơn": khi đưa mp4, Whisper giải mã
trực tiếp AAC 96k → 16kHz PCM (một lần mất mát, sạch); khi tách mp3 32k trước,
Whisper giải mã cái đã hỏng hai lần.

### Đo thực tế (file `Web scraper.mp4`, ~11 phút)

| Nguồn | Codec | Sample rate | Bitrate | Dung lượng |
|---|---|---|---|---|
| Audio gốc trong mp4 | AAC | 44.1 kHz stereo | 96 kbps | — |
| MP3 32k (bản cũ, sai) | MP3 | 16 kHz mono | 32 kbps | 2.6 MB |
| **WAV PCM (bản đúng)** | pcm_s16le | 16 kHz mono | lossless | 21 MB |

Đo năng lượng dải cao (>3.5kHz): bản MP3 32k có **nhiễu cao hơn** WAV sạch
(mean −44.6 dB vs −47.4 dB) — đó là nhiễu lượng tử do nén, không phải tín hiệu.

### Lệnh đúng

```bash
ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le -y output.wav
```

`-vn` bỏ hình · `-ac 1` gộp mono · `-ar 16000` đúng sample-rate Whisper ·
`-c:a pcm_s16le` **PCM lossless** (không tầng lossy nào).

### Vì sao giới hạn 30MB không liên quan

Giới hạn **30 MB** là của **Lark IM khi gửi file cho người dùng** — mà thứ gửi
cho người dùng là transcript `.txt` (vài KB). Audio thì chỉ **POST sang Whisper
server qua `localhost:8000`**, không đi qua Lark IM, nên **không dính giới hạn nào**.
Vì thế không có lý do gì phải crush bitrate. Nếu sau này audio phải đi qua kênh
có giới hạn, dùng **FLAC** (lossless, ~½ WAV) hoặc **Opus 24–32k** (tốt hơn hẳn
MP3 cùng bitrate) — tuyệt đối không quay lại MP3 32k.

### Ước lượng dung lượng WAV 16kHz mono

~1.8 MB/phút → 30' ≈ 55 MB, 1h ≈ 110 MB, 2h ≈ 220 MB, 4h ≈ 440 MB. Lớn hơn mp3
cũ nhưng vẫn nhỏ hơn nhiều so với mp4 gốc (hàng trăm MB → GB).

---

## 9. Whisper: hiệu năng và chất lượng

### Hiệu năng CPU (đo thật)

- Whisper **medium trên CPU**: audio 10'57" → **18'52"** xử lý (~**0.58× realtime**).
- Audio ngắn nhiều khoảng lặng còn chậm hơn (1 phút → 4 phút).

| Độ dài họp | Whisper CPU (ước) |
|---|---|
| 30 phút | ~0.9 giờ |
| 1 tiếng | ~1.7 giờ |
| 2 tiếng | ~3.4 giờ |
| 4 tiếng | ~6.9 giờ |

**→ Server chính bắt buộc phải có GPU.** Đây là giới hạn của máy local, không phải
của kiến trúc — client (`whisper_worker.py`) chỉ cần đổi `WHISPER_URL` là chuyển
sang server GPU, không sửa gì thêm.

### Glossary qua `initial_prompt`

Whisper chạy kèm `initial_prompt` nhồi thuật ngữ hay gặp (MCP, Claude, Node.js,
Lark, connector, authorize, terminal, macOS, Windows, restart). Không có glossary
thì ra "mờ cp", "cây lau", "green" thay vì MCP, Claude, Win. Với tiếng Việt +
thuật ngữ tiếng Anh xen kẽ, bước này bắt buộc.

### So sánh với Lark Minutes (cùng cuộc "Web scraper")

| Nguồn | Số từ | Đặc điểm |
|---|---|---|
| Lark Minutes | 1396 | Có nhãn người nói, gộp thành đoạn |
| Whisper medium | 1245 | Timestamp từng câu, **không** tách người nói |

Whisper thua Lark Minutes ở **tách người nói** (diarization). Cần thì ghép
**WhisperX** hoặc **pyannote** — đây là khoảng cách lớn nhất còn lại.

---

## 10. Event callback

### Chọn persistent connection, không dùng webhook

Lark cho hai chế độ. Chọn **persistent connection** vì: không cần địa chỉ công
khai, không cần ngrok/cloudflared, không phải publish lại mỗi lần đổi URL. Cần
SDK `lark-oapi`.

### Event nào dùng được

| Event | Trạng thái | Ghi chú |
|---|---|---|
| `minutes.minute.generated_v1` | **Dùng được** | Bắn đúng lúc minute sinh ra, thay 1-đổi-1 cho việc quét `minutes +search` |
| `vc.meeting.recording_ready_v1` | Không dùng được | Chỉ bắn với họp đặt qua **Open API**, không áp dụng họp tạo từ calendar UI |
| `vc.meeting.all_meeting_ended_v1` | Chỉ để quan sát | Cấp tenant, biết mọi cuộc họp kết thúc nhưng **không đọc được nội dung** |

### Thiết kế: event chỉ là "chuông báo"

Event **không** tự xử lý payload — nó gọi `run_once()` của poller. Lợi ích: dùng
lại toàn bộ logic sẵn có, không phải đoán cấu trúc payload, chống trùng tự động.
Nhiều event dồn dập chỉ kích hoạt **một** vòng quét (chờ 30 giây gom lại).

### Vẫn giữ polling song song

Event rớt là **mất luôn** — máy tắt, mạng chập chờn, listener chết; persistent
connection **không phát lại** event cũ. Polling hỏi *trạng thái hiện tại* nên
không quan tâm đã bỏ lỡ gì, cộng `LOOKBACK_DAYS = 2` nên máy tắt hai ngày bật
lên vẫn hứng đủ. Event để *nhanh*, polling để *chắc*.

---

## 11. Vì sao chưa phủ được toàn công ty

| Scope | Loại token | Phạm vi |
|---|---|---|
| `vc:meeting.all_meeting:readonly` | **Tenant** | Toàn công ty |
| `minutes:minutes.basic:read` | **User** | User-specific |
| `vc:recording:read` | **User** | User-specific |

App **nhận được** thông báo mọi cuộc họp trong công ty kết thúc (scope tenant),
nhưng **không đọc được** minute của cuộc mình không dự (scope user). Đây là rào
**cố ý của Lark**: muốn đọc minute của ai thì phải có sự đồng ý của người đó.
Không có scope tenant nào cho đọc minute cả công ty.

Hai hướng mở rộng, **cả hai đều giữ sự đồng ý**:

1. **OAuth từng người** — mỗi nhân viên tự authorize một lần, server lưu refresh
   token riêng, poller lặp qua từng người. Chắc chắn được nhưng nặng; bước
   authorize chính là bước đồng ý.
2. **Cho bot tham gia cuộc họp** — scope `vc:meeting.bot.join:write` đã cấp. Nếu
   bot là người dự thì đọc được minute một cách chính danh, không mượn token của
   ai. **Đáng thử trước** vì nhẹ và sạch hơn OAuth.

> **Lưu ý bảo mật (quan trọng).** Không dựng cơ chế gom *user token* của toàn bộ
> nhân sự để đọc họp thay họ. Đó là thu thập credential hàng loạt, lách đúng cái
> rào user-level mà Lark đặt ra, và tạo một kho danh tính rủi ro. Mặt tích cực
> của giới hạn hiện tại: ranh giới kỹ thuật trùng với ranh giới *nên có* — không
> ai đọc được cuộc họp mình không dự, kể cả người dựng hệ thống.

---

## 12. Thiết lập lark-cli profile riêng

lark-cli chỉ gắn được **một app mỗi profile**, và profile mặc định là app anh
Thiện (dùng chung cho MCP cả team). Tạo profile riêng để không đụng vào đó:

```bash
call config.bat
echo %SENDER_APP_SECRET%| lark-cli profile add --name mine ^
     --app-id cli_aae288361ef89eed --app-secret-stdin --brand lark
lark-cli auth login --profile mine --domain all
```

> **Tuyệt đối không chạy `lark-cli profile use`** — lệnh đó đổi profile mặc định
> và làm hỏng MCP cả team đang dùng. Chỉ truyền `--profile mine` mỗi lần gọi.

---

## 13. Các cạm bẫy trên Windows

| Cạm bẫy | Triệu chứng | Cách xử lý |
|---|---|---|
| `lark-cli` là file `.cmd` (npm) | `subprocess.run(["lark-cli"])` → `FileNotFoundError [WinError 2]` | Dùng `shutil.which("lark-cli")` lấy đường dẫn đầy đủ |
| `im +messages-send --file` từ chối đường dẫn tuyệt đối | Gửi file lỗi | Ghi transcript vào `out/` (đường dẫn **tương đối**) |
| File `.bat` có dấu tiếng Việt | cmd.exe đọc lệch byte UTF-8, **nuốt ký tự đầu dòng** (`TRANSCRIPT_SOURCE` → `RANSCRIPT_SOURCE`) | `.bat` chỉ dùng ASCII không dấu |
| Sửa `.bat` bằng script với `\n` | Không khớp CRLF → **fail âm thầm**, tạo file lai nửa cũ nửa mới | Sửa `.bat` phải dùng **CRLF** |
| PowerShell không có `call` và `cd /d` | Lệnh cấu hình không chạy | Dùng cmd, hoặc `cmd /c "call config.bat && python ..."` |
| Truyền cấu hình qua biến môi trường | `.bat` và Python từng báo hai chế độ mâu thuẫn | Truyền cấu hình quan trọng bằng **tham số dòng lệnh** |

---

## 14. Thiết kế poller: polling + event

- **Poller là nguồn sự thật.** Hỏi *trạng thái hiện tại*, nên không quan tâm đã
  bỏ lỡ event nào. `LOOKBACK_DAYS = 2` để bắt lại sau khi máy tắt.
- **Event là gia tốc, không phải điều kiện.** Listener chỉ gọi `run_once()`.
  Không có listener thì hệ thống vẫn chạy đúng, chỉ trễ tối đa `POLL_INTERVAL`.
- **Chống trùng bằng state.** `poller_state.json` nhớ `seen_tokens` và
  `first_seen`. Minute đã xử lý không xử lý lại. Chạy lại một cuộc = xoá token
  khỏi `seen_tokens` rồi khởi động lại poller.
- **Job là file, không phải RAM.** `jobs/` (đang chờ) và `jobs/done/` (đã xong)
  giúp khôi phục sau khi tiến trình chết giữa chừng. (Lưu ý: job của *Whisper
  server* lại lưu trong RAM — tắt server là mất, đây là hạn chế còn tồn tại.)

---

## Phụ lục — Mã định danh hay dùng

| Thứ | Giá trị |
|---|---|
| App "Agent meeting" (gửi tin) | `cli_aae288361ef89eed` |
| App "AI Agent Assistant" (MCP team) | `cli_a9bd0ff8d6619ed1` |
| Whisper server local | `http://localhost:8000` (`/docs` có UI thử API) |

Không đưa `open_id`/`union_id` cá nhân và các secret vào tài liệu này — chúng
nằm trong `config.bat` (đã gitignore) và trạng thái runtime.
