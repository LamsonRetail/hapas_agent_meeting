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
`cacheControlMaxAge` < 60s). Vì vậy mỗi snapshot ghi vào pathname riêng
`status/<epoch_ms>.json`, đọc bản mới nhất qua `list()` (gọi API metadata, không
bị cache). Mỗi lần POST dọn bớt, chỉ giữ 3 bản gần nhất → dung lượng có chặn.

## Deploy

```bash
cd v2/vercel-oauth
npx vercel --prod --yes
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
