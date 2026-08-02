# Hàm serverless V2 trên Vercel

Hai việc, cùng một project (`vercel-oauth`, alias
`https://vercel-oauth-two.vercel.app`):

| Đường | Việc |
|---|---|
| `GET /oauth/callback` | hộp thư OAuth — hiển thị `code` + `state` để admin chạy `python -m v2 complete` |
| `GET /` · `/status` | dashboard trạng thái V2 (ai enroll, hàng đợi, đếm ngược vòng quét, kết quả `doctor`) |
| `GET /api/status?json=1` | snapshot thô |
| `POST /api/status` | máy local đẩy snapshot lên (cần bearer secret) |

Nguyên tắc không được phá (V2_ARCHITECTURE §3): **token và `state.db` không bao
giờ chạm Vercel.** Vercel không query về máy local; chiều dữ liệu chỉ là
local → Vercel.

## 1. Hộp thư OAuth

Vì sao an toàn dù hiển thị công khai:
- App secret **chỉ ở máy local** → Vercel không đổi được code lấy token.
- `code` dùng-một-lần, hết hạn ~5 phút.
- `state` (nonce) do máy local sinh và tự kiểm; callback giả không qua được.

## 2. Dashboard trạng thái

Mô hình "local đẩy snapshot":

```
python -m v2 push-status          ->  POST /api/status  (Bearer STATUS_PUSH_SECRET)
                                          |
                                      Vercel Blob (store v2-status)
                                          |
người xem  ->  GET /  ->  render HTML
```

Snapshot **đã ẩn danh ở máy local** (`v2/status_push.py`): chỉ có display_name,
status, mốc hết hạn refresh, ĐẾM job theo status, kết quả `doctor`, cấu hình
không bí mật. KHÔNG có open_id, token, tên cuộc họp, minute_token, transcript.
Xem đúng cái sắp gửi trước khi mở cho người khác:

```bash
python -m v2 push-status --print
```

### Biến môi trường trên Vercel

| Tên | Bắt buộc | Việc |
|---|---|---|
| `BLOB_READ_WRITE_TOKEN` | có | tự sinh khi nối Blob store |
| `STATUS_PUSH_SECRET` | có | bearer để nhận POST; phải khớp `v2/.env` |
| `STATUS_VIEW_TOKEN` | không | đặt để khoá trang xem → phải vào `/?k=<token>` |

Snapshot có tên người thật, nên nếu không muốn công khai thì đặt
`STATUS_VIEW_TOKEN`.

### Vì sao Blob mà không phải KV

KV trên Vercel giờ là integration Marketplace (Upstash) — phải bấm qua dashboard
và chấp nhận điều khoản bên thứ ba. Blob là store gốc, tạo bằng CLI một dòng.

**Cạm bẫy đã đo (2026-07-30):** ghi đè một pathname cố định thì đọc bị cũ tới
~60s, `get(..., {useCache:false})` cũng KHÔNG thoát được (SDK không cho
`cacheControlMaxAge` < 60s). Vì vậy lúc đó mỗi snapshot ghi vào **pathname
riêng** `status/<epoch_ms>.json`, đọc bản mới nhất qua `list()`, và mỗi POST
`list`+`del` để dọn, chỉ giữ 3 bản.

**Đã đảo lại quyết định đó (2026-08-02, xem V2_MAINTENANCE §32).** Cách trên
tốn **3 thao tác Blob mỗi lần đẩy**, mà hạn mức *Advanced Requests* của gói
free chỉ có **2.000 thao tác/THÁNG** — Vercel đã gửi thư báo dùng hết 75% sau
~3,5 ngày, và cạn hạn mức thì **hộp thư OAuth chết theo, tức không ai enroll
được**. Nay ghi đè đúng một file `status/latest.json`: POST tốn **1 thao tác**.

Giá phải trả là đúng cạm bẫy trên — trang có thể cũ vài chục giây sau khi ghi
(đo 02/08: ngay sau POST vẫn thấy bản cũ, một lát sau thì đúng). Vô hại với
nhịp đẩy 30 phút (`STATUS_PUSH_EVERY=1800`); hồi 30/07 nó mới đáng lo vì lúc đó
đẩy mỗi vòng và người ta F5 để xem đổi ngay.

## Deploy

**Chạy từ GỐC REPO, không phải từ thư mục này** (sửa 02/08/2026):

```bash
cd E:\meetingxlark
npx vercel --prod --yes
```

Ba cái bẫy đã trả giá, đừng lặp:

1. **`cd v2/vercel-oauth` rồi deploy là HỎNG.** Project đặt
   *Root Directory = `v2/vercel-oauth`* trong Settings, nên CLI đi tìm
   `v2/vercel-oauth/v2/vercel-oauth` và báo *"The provided path … does not
   exist"*. Lệnh trong bản README cũ là lệnh này.
2. **Chạy từ gốc khi gốc CHƯA link thì CLI lặng lẽ tạo PROJECT MỚI** tên
   `meetingxlark` rồi dừng ở prompt hỏi git remote. Đã xảy ra thật; phải
   `npx vercel project rm meetingxlark` (prompt cần `printf 'y\n' |`, cờ
   `--yes` không tồn tại cho lệnh này) rồi
   `npx vercel link --yes --project vercel-oauth`. Kiểm trước khi deploy:
   `cat .vercel/project.json` phải ra `"projectName":"vercel-oauth"`.
3. **Gốc repo có `v2/data/` (state.db + transcript nguyên văn), `v2/.env`, và
   `v1/config.bat` đang tracked với app secret sống.** Vì vậy có
   [`.vercelignore`](../../.vercelignore) ở gốc theo kiểu **danh sách trắng**.
   Deployment là bất biến — tải nhầm rồi thì không rút lại được.

Xác minh sau khi deploy rằng đúng những file cần thiết mới lên (đo 02/08: **12
file**, không có file nhạy cảm nào):

```bash
TOK=$(python -c "import json;print(json.load(open(r'%APPDATA%\xdg.data\com.vercel.cli\auth.json'))['token'])")
curl -s -H "Authorization: Bearer $TOK" \
  "https://api.vercel.com/v6/deployments/<dpl_id>/files?teamId=<team_id>"
```

Tạo lại Blob store từ đầu (nếu làm ở project khác):

```bash
npx vercel blob create-store v2-status --access public --yes
```

Đặt secret:

```bash
npx vercel env add STATUS_PUSH_SECRET production
```

## Nâng cấp sau (chưa làm)

Enroll tự động (không copy-paste tay): Vercel mã hoá `code` bằng public key của
máy local rồi lưu vào Blob; máy local poll `/api/oauth/pending`, giải bằng
private key, tự `complete`. Khi đó `oauth.complete()` giữ nguyên, chỉ thay
đường lấy code.
