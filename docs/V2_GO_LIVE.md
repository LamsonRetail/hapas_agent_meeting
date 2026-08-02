# V2 — Runbook vào vận hành thật (từng bước)

Đưa orchestrator `v2/` từ "đã dựng, test dry-run" sang "chạy thật với Lark".
Làm **tuần tự**, mỗi bước có mốc **nghiệm thu** — chưa đạt thì đừng qua bước sau.

Ký hiệu: 🖥️ = làm trên **máy** (lệnh). 🌐 = làm trên **Lark Developer Console**
(chỉ bạn làm được, không tự động hóa). ✅ = nghiệm thu.

Bối cảnh mặc định của runbook này: **một máy admin**, người đầu tiên enroll
chính là bạn, OAuth callback qua **hộp thư Vercel dùng domain mặc định
`*.vercel.app`** (không mua domain). Mở rộng nhiều người ở Phần 8.

---

## Phần 0 — Chuẩn bị máy (🖥️)

1. Python 3.11+ và ffmpeg trong PATH:
   ```bash
   python --version
   ffmpeg -version
   ```
   Thiếu ffmpeg → cài (choco/scoop hoặc tải bản build, thêm vào PATH).

2. Cài phụ thuộc V2:
   ```bash
   pip install -r v2/requirements.txt
   ```

3. Sinh khóa mã hóa token:
   ```bash
   python -m v2 genkey
   ```
   Chép chuỗi in ra, sẽ dán vào `V2_FERNET_KEY` ở Phần 2.

4. **transcribe_server phải chạy được** (đây là engine phiên âm, giữ nguyên
   từ V1, nằm ở `E:\lark-mcp-new\lark-cli-new-update\transcribe_server\`):
   ```bash
   cd E:\lark-mcp-new\lark-cli-new-update\transcribe_server
   pip install -r requirements.txt
   python server.py
   ```
   Mở máy khác/cửa sổ khác kiểm tra: `curl http://localhost:8502/health`

✅ **Nghiệm thu Phần 0:** `/health` trả JSON có `"status":"ok"`, `device` là
`cuda` (GPU) hoặc `cpu`. GPU thì tốt; CPU chỉ để test luồng, chậm (README §9).

---

## Phần 1 — Cấu hình Lark Developer Console (🌐)

Đây là phần nặng nhất và **không có việc gì code thay được**. Mở app tại
<https://open.larksuite.com/app> → chọn app (mặc định "Meeting Agent CĐS"
`cli_aae288361ef89eed`, hoặc tạo app mới nếu muốn tách khỏi V1).

### 1.1 Bật Bot (để gửi tin)
- **Add Features → Bot → Add**. (V1 đã bật thì bỏ qua.)

### 1.2 Thêm quyền (Permissions & Scopes)
Thêm các scope sau. **Quan trọng:** tên hiển thị trong Console khác với
*chuỗi scope* dùng cho OAuth. Bấm vào từng scope xem **API identifier** thật
rồi ghi lại — đây chính là thứ điền vào `OAUTH_SCOPES` (Phần 2).

| Cần cho | Quyền (tên gần đúng — xác nhận identifier trong Console) |
|---|---|
| Đọc/tải minute | View and download minutes (`minutes:minutes:readonly` hoặc tương đương) |
| Dò người được mời | Read calendar events + attendees (`calendar:calendar:readonly`) |
| Nối meeting→minute | Read VC recording (`vc:record:readonly`) |
| Phân giải tên/phòng ban | Read user basic info (`contact:user.base:readonly`) |
| Tạo task từ action item | Create task (`task:task:write`) — xin luôn dù v1 chưa dùng (V2_LONGTERM §3.2) |
| Bot gửi tin | Send messages (`im:message` — thường là **tenant** scope, không qua OAuth) |

### 1.3 Bật OAuth + đăng ký redirect_uri
- Vào **Security Settings** (hoặc **OAuth 2.0 / Redirect URLs**).
- Thêm **Redirect URL** = URL Vercel (lấy ở Phần 1.7):
  `https://<project>.vercel.app/oauth/callback`
  - Phải khớp **từng ký tự** với `OAUTH_REDIRECT_URI` trong `.env`.
  - (Thay thế: `http://localhost:8080/oauth/callback` nếu chỉ enroll trên
    chính máy admin — khi đó dùng `python -m v2 enroll` thay vì luồng Vercel.)

### 1.4 Đăng ký event qua Long Connection (cho WebSocket — có thể làm sau)
- **Event Subscription → chọn "Long Connection" (Persistent connection)**,
  KHÔNG chọn webhook URL.
- Subscribe event: **`minutes.minute.generated_v1`**.
- (Đường nhanh, không bắt buộc để chạy. Bỏ qua nếu chỉ muốn polling trước.)

### 1.5 Bật card callback (cho nút Duyệt — nếu dùng REQUIRE_APPROVAL)
- **Bot → Message Card / Interactive** → bật callback qua **Long Connection**.
- Đây là chỗ hay quên gây **lỗi 200340** khi bấm nút (gửi card thì vẫn thành
  công nên rất dễ sót — V2_ARCHITECTURE §8 #2).

### 1.6 Availability + Publish
- **Availability**: đổi sang **toàn công ty** (không thì gửi cho người ngoài
  danh sách nhận **lỗi 230013**).
- **Version Management & Release → Create Version → Publish**. Chờ admin
  tenant duyệt. **Chỉ đổi cấu hình mà không tạo version mới thì chưa hiệu lực.**

### 1.7 Deploy hộp thư OAuth lên Vercel (🖥️, để lấy URL cho 1.3)
Hàm nhận callback của Lark. Không cần domain riêng — dùng luôn
`*.vercel.app`.
```bash
cd v2/vercel-oauth
npx vercel            # lần đầu: đăng nhập + tạo/link project
npx vercel --prod     # in ra URL: https://<project>.vercel.app
```
Ghi lại URL đó; quay lại 1.3 dán `https://<project>.vercel.app/oauth/callback`.
Kiểm tra: mở `https://<project>.vercel.app/oauth/callback` trên trình duyệt —
phải thấy trang "Thiếu tham số" (đúng, vì chưa có code). Chi tiết:
`v2/vercel-oauth/README.md`.

✅ **Nghiệm thu Phần 1:** version app đã phát hành; hộp thư Vercel truy cập
được; bạn đã ghi lại `App ID`, `App Secret`, **identifier thật của từng scope**,
và **URL Vercel**.

> Nếu bạn không phải admin tenant: bước Publish/duyệt scope phải nhờ admin.
> Đây là rào tổ chức lớn nhất (V2_ARCHITECTURE §8 #3) — xử lý sớm.
> Bạn có quyền admin nên bước này chủ động được.

---

## Phần 2 — Điền cấu hình (🖥️)

```bash
cp v2/.env.example v2/.env
```
Mở `v2/.env`, điền:

- `LARK_APP_SECRET=` app secret (Phần 1).
- `V2_FERNET_KEY=` khóa từ Phần 0.
- `OAUTH_REDIRECT_URI=https://<project>.vercel.app/oauth/callback` (khớp
  **chính xác** cái đăng ký ở 1.3).
- `OAUTH_SCOPES=` **dán identifier thật** từ 1.2, cách nhau bằng dấu cách.
- `LLM_API_KEY=` key OpenAI (để trống thì gửi transcript, không recap).
- `TRANSCRIBE_URL=http://localhost:8502`.
- Giữ `SEND_MODE=0` (dry-run) và `REQUIRE_APPROVAL=1` cho giai đoạn đầu.

Kiểm tra:
```bash
python -m v2 status
```

✅ **Nghiệm thu Phần 2:** `status` in `app_secret` không còn "(trống)",
`fernet=có`, `redirect_uri` hiện đúng, `transcribe=...:8502`.

---

## Phần 3 — Enroll chính bạn qua Vercel (🖥️ + 🌐 bấm Đồng ý)

Luồng: máy local in link → bạn bấm Đồng ý → Vercel hiện `code`+`state` →
máy local `complete`. Token chỉ sinh ở máy local.

1. In link OAuth (nonce lưu ở máy local, sống ~15 phút):
   ```bash
   python -m v2 enroll-url
   ```
2. Mở link trên trình duyệt **đã đăng nhập Lark của bạn** → bấm **Đồng ý**.
3. Vercel hiện trang "Đã nhận mã ✓" với `code`, `state` và **lệnh sẵn để copy**.
4. Chạy lệnh đó ở máy local (hoặc copy code+state vào):
   ```bash
   python -m v2 complete --code <CODE> --state <STATE>
   ```
   Terminal in `✓ Đã enroll: <tên>`.
5. Kiểm tra:
   ```bash
   python -m v2 status
   ```

✅ **Nghiệm thu Phần 3:** mục "Đã enroll" hiện tên bạn, `refresh còn ~30 ngày`,
`[OK]`. Đây là lần chứng minh cả chuỗi Vercel + OAuth (authorize → callback →
đổi token) chạy thật.

> Lỗi thường gặp ở đây:
> - `redirect_uri_mismatch`: URL ở Console (1.3) và `.env` khác nhau dù chỉ 1
>   ký tự (http/https, dấu `/` cuối).
> - `state không hợp lệ`: nonce hết hạn (quá 15 phút) hoặc chạy `complete` ở
>   máy khác máy đã chạy `enroll-url`. Chạy lại `enroll-url`.
> - Scope sai → màn hình Đồng ý báo lỗi. Đối chiếu 1.2.

---

## Phần 4 — Kiểm chứng các endpoint đọc `[VERIFY]` (🖥️ + họp thật)

Đây là bước xác minh những endpoint Open API mà `lark_api.py` đánh dấu
`[VERIFY]` — chúng có thể khác nhau theo tenant.

### 4.1 Tạo dữ liệu thật
🌐 Tạo **một cuộc họp qua Calendar** (tự mời mình cũng được), **bấm Ghi hình**,
nói vài câu (≥ 30 giây để có transcript), kết thúc, **dừng record**. Chờ ~5
phút cho Lark sinh Minutes và liên kết bản ghi (TECHNICAL §5).

### 4.2 Lấy minute_token
Mở minute vừa sinh trên Lark, copy token từ URL (đoạn sau `/minutes/`), hoặc
thử đường polling:
```bash
python -m v2 scan
```
- Nếu `scan` in ra job mới → `minutes_list` **dùng được**, polling chạy. 🎉
- Nếu `scan` báo 0 job và log có "minutes_list chưa dùng được" → endpoint list
  chưa đúng với tenant này. **Không sao** — dùng đường nạp tay để test tiếp
  phần còn lại, và dựa vào WebSocket (Phần 6) làm đường chính:

```bash
python -m v2 enqueue --token <minute_token>
```

✅ **Nghiệm thu 4.2:** `status` cho thấy 1 job ở trạng thái `queued`.

### 4.3 Chạy pipeline (dry-run — chưa gửi)
```bash
python -m v2 process
```
Theo dõi log lần lượt: `tải bản ghi` → `ffmpeg ... giảm %` → (chờ Whisper) →
`transcript N từ` → in recap dry-run.

✅ **Nghiệm thu 4.3:** thấy đủ chuỗi tải → ffmpeg → transcript → recap. File
transcript `.json` (có segments) nằm ở `v2/data/transcripts/`. Đây là lúc
`minutes_media_url`, tải mp4, seam transcribe, seam summarize đều được xác
minh thật.

> Hỏng ở "tải bản ghi" = `minutes_media_url` cần chỉnh (xem response thật,
> sửa trong `lark_api.py`). Hỏng ở "tra người được mời" (job có 0 người) =
> `calendar_*`/`vc_recording` cần chỉnh — nhưng fallback owner vẫn gửi được
> cho bạn nên không chặn.

---

## Phần 5 — Phát thật lần đầu (🖥️)

Vẫn cuộc họp test đó. Bật gửi thật:
```bash
python -m v2 process --send
```
- `REQUIRE_APPROVAL=1`: bot gửi **thẻ duyệt** cho bạn (chủ phòng). Bấm
  **"Phát cho N người"** trên thẻ.
  - Nếu chưa bật card callback (1.5) hoặc WS (Phần 6) thì nút chưa hoạt động —
    duyệt tay bằng lệnh:
    ```bash
    python -m v2 action --token <minute_token> --act deliver_all
    ```
- Người nhận (bạn) nhận **thẻ recap** + **file transcript**.

✅ **Nghiệm thu Phần 5:** bạn nhận được recap và transcript trong Lark. Job
chuyển trạng thái `delivered` (`python -m v2 status`). **Đây là mốc "1 cuộc
họp chạy đầu-cuối thật".**

---

## Phần 6 — WebSocket đường nhanh (🌐 1.4/1.5 + 🖥️) — tùy chọn

Sau khi 1.4 và 1.5 đã bật:
```bash
python -m v2 run --ws --send
```
- Vòng lặp chính chạy (polling mỗi `POLL_INTERVAL`) **kèm** WebSocket.
- Họp mới xong → event `minutes.minute.generated_v1` kích hoạt xử lý ngay,
  không chờ hết chu kỳ poll.
- Nút Duyệt trên thẻ hoạt động (card.action.trigger về qua WS).

✅ **Nghiệm thu Phần 6:** họp thử mới → thấy log `[ws] minute.generated ...`
trong vài giây; bấm nút trên thẻ thấy toast phản hồi và job được phát.

> Bấm nút báo **lỗi 200340** = card callback (1.5) chưa cấu hình đúng.
> Event không bắn cho họp thường = xác nhận lại 1.4 (một số tenant chỉ bắn cho
> họp đặt qua Open API — khi đó dựa vào polling/enqueue).

---

## Phần 7 — Giữ máy chạy liên tục (🖥️)

Máy tắt = dừng phát hiện; polling `LOOKBACK_DAYS=2` bắt bù khi bật lại, nhưng
nên tự khởi động:

1. **Tự chạy khi bật máy** — tạo `.bat` mỏng gọi hai tiến trình
   (transcribe_server + `python -m v2 run --ws --send`) rồi đăng ký Task
   Scheduler (V1 đã có `install-autostart.bat` làm mẫu).
2. **Backup `v2/data/state.db` hàng ngày** — mất file này = mọi người enroll
   lại (V2_LONGTERM §7). Copy sang ổ ngoài/Lark Drive; để **key backup tách
   khỏi file** (key ở `.env`, đừng backup chung chỗ).
3. Tắt Windows Update tự khởi động lại trong giờ làm.

✅ **Nghiệm thu Phần 7:** khởi động lại máy → cả transcribe_server lẫn
orchestrator tự lên; có một bản `state.db` backup của hôm nay.

---

## Phần 8 — Mở rộng sang người thứ hai (làm sau, đọc kỹ)

Enroll người ở xa với luồng Vercel rất tiện — **không cần domain riêng**:
```bash
python -m v2 enroll-url            # gửi link in ra cho họ
# họ bấm Đồng ý -> trang Vercel hiện code+state -> họ gửi lại cho bạn
python -m v2 complete --code <CODE> --state <STATE>
```
Trang Vercel công khai nên callback của bất kỳ ai cũng về được; họ chỉ cần
relay hai giá trị cho bạn (app secret vẫn ở máy bạn).

**Bốn quyết định khó đảo cần cân nhắc khi tính chuyện mở rộng thật
(V2_LONGTERM §3):**

1. **Domain riêng** — bạn đang tạm dùng `*.vercel.app` để test, chấp nhận
   được. Nhưng nếu đổi project/hạ tầng thì URL đổi và **người đã enroll không
   bị ảnh hưởng** (redirect chỉ kiểm lúc cấp quyền); chỉ cần đăng ký URL mới
   trong Console cho người enroll *sau*. Mua domain riêng để tránh phụ thuộc
   `*.vercel.app` là việc nên làm khi lên production, không bắt buộc lúc test.
2. Đã xin **đủ scope** ngay từ đầu (1.2) — thêm scope sau = cả công ty duyệt lại.
3. Orchestrator giữ WebSocket, không để thứ khác chiếm `app_id`.
4. Transcript đã lưu dạng `segments` (đã đúng trong `transcribe.py`).

> **Cảnh báo bảo mật (V2_ARCHITECTURE §11, MULTI_USER §5):** máy admin giữ
> user token toàn quyền của mọi người enroll. Chỉ chạy trên máy admin, nói rõ
> với nhân sự, xin scope tối thiểu. Cân nhắc kỹ trước khi mở rộng danh sách.

---

## Bảng lỗi thường gặp

| Mã / triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| OAuth `redirect_uri_mismatch` | redirect trong `.env` khác Console | Khớp **từng ký tự** (kể cả `http`, cổng, `/oauth/callback`) |
| `missing_scope` khi đọc | Console đã bật nhưng user chưa cấp | Enroll lại; kiểm tra scope có trong màn hình Đồng ý |
| Scope viết sai khi authorize | Dùng tên hiển thị thay vì identifier | Lấy đúng identifier trong Console (1.2) |
| **230013** "no availability" | App chưa phát hành cho người nhận | Availability → toàn công ty, **tạo version mới** (1.6) |
| **200340** khi bấm nút thẻ | Card callback chưa cấu hình | Bật Interactive Card qua Long Connection (1.5) |
| **99992361** "open_id cross app" | Trộn open_id giữa hai app | V2 gửi bằng `union_id` — không xảy ra nếu giữ nguyên |
| `minutes_list` rỗng | Endpoint list chưa khớp tenant | Dùng `enqueue --token` + WebSocket; hoặc chỉnh endpoint trong `lark_api.py` |
| Tải bản ghi hỏng | `minutes_media_url` response khác | Xem response thật, sửa `lark_api.minutes_media_url` |
| Whisper treo/không xong | transcribe_server chưa chạy / GPU bận | `curl :8502/health`; đảm bảo server sống trước khi `process` |
| Refresh token hết hạn | 7–30 ngày không gọi API | `python -m v2 enroll` lại người đó |

---

## Tóm tắt lệnh

```bash
python -m v2 genkey                       # sinh Fernet key (Phần 0)
python -m v2 status                       # cấu hình + auth + hàng đợi
python -m v2 enroll-url                    # in link OAuth (luồng Vercel)
python -m v2 complete --code X --state Y   # hoàn tất enroll (Vercel/xa)
python -m v2 enroll                        # enroll qua callback localhost
python -m v2 scan                         # quét minute (polling)
python -m v2 enqueue --token T            # nạp 1 minute tay (test/khi list lỗi)
python -m v2 process [--send]             # xử lý hàng đợi 1 lần
python -m v2 action --token T --act deliver_all   # duyệt tay
python -m v2 run --ws --send              # vòng lặp chính (vận hành)
```
