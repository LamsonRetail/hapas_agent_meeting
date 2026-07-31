# V2 orchestrator — meeting note tự động, không phụ thuộc lark-cli/MCP

Bản triển khai của `docs/V2_ARCHITECTURE.md` + `docs/V2_LONGTERM.md`. Một
process, một máy, gọi thẳng Lark Open API. Nhân viên không cài gì — chỉ
authorize một lần qua OAuth.

> **Trạng thái:** nền tảng lõi đã dựng và test wiring (dry-run, không mạng).
> Phần đọc Lark thật và WebSocket cần cấu hình app + kiểm chứng endpoint
> (xem [Việc cần bạn](#việc-cần-bạn-làm) cuối file).

## Kiến trúc

```
                 ┌─ python -m v2 run ──────────────────────────────┐
  Lark  ◄──────► │  orchestrator (một process)                     │
   (Open API)    │   scan_once()      polling: minute mới -> job    │
                 │   process_queue()  transcribe -> recap -> duyệt  │
                 │   handle_action()  nút Duyệt -> phát             │
                 │   tokenstore       user token (SQLite + Fernet)  │
                 └───────────────┬─────────────────────────────────┘
   ws_listener.py (tùy chọn)     │ POST /transcribe
     minutes.generated -> enqueue│ (giữ nguyên transcribe_server V1)
     card.action      -> action  ▼
                            transcribe_server (GPU, localhost:8502)
```

## Module

| File | Vai trò |
|---|---|
| `config.py` | Đọc `.env`, mọi tham số. Không hardcode path/múi giờ. |
| `models.py` | `Transcript`/`Segment`, `MeetingMeta`, `Recap` — ngôn ngữ chung qua các seam. |
| `crypto.py` | Mã hóa token (Fernet). |
| `db.py` | SQLite: tokens, khóa chống trùng `minutes_lock`, nonce, jobs, deliveries. |
| `lark_api.py` | **Gom mọi lời gọi Lark Open API** (§7.4). Lark đổi API -> sửa một file. |
| `tokenstore.py` | Lưu/refresh/xoay vòng user token. |
| `oauth.py` + `oauth_callback.py` | Enroll qua OAuth (nonce, đổi code, callback local). |
| `transcribe.py` | Seam phiên âm -> `transcribe_server`. |
| `summarize.py` | Seam LLM (provider-neutral) -> `Recap`. Đổi GPT↔Hermes chỉ bằng `LLM_BASE_URL` (§4.2). |
| `meetings.py` | Phát hiện minute + tra người được mời (port V1 §6). |
| `cards.py` | Thẻ duyệt + thẻ recap + parse nút bấm. |
| `jobstore.py` | Máy trạng thái job. |
| `pipeline.py` | Tải -> ffmpeg -> transcribe -> recap -> phát. |
| `orchestrator.py` | Vòng chính: scan + queue + duyệt + hết hạn. |
| `alerts.py` | Cảnh báo qua Lark DM khi hệ thống hỏng (job failed / whisper chết / token sắp hết hạn). Chống spam bằng bảng `alert_state`. Xem `docs/V2_MAINTENANCE.md` §17. |
| `ws_listener.py` | WebSocket đường nhanh (tùy chọn). |
| `__main__.py` | CLI. |

## Cài & cấu hình

```bash
pip install -r v2/requirements.txt
cp v2/.env.example v2/.env
python -m v2 genkey        # dán kết quả vào V2_FERNET_KEY trong .env
```

Điền `LARK_APP_SECRET`, `OAUTH_REDIRECT_URI` (khớp Console), `LLM_API_KEY`.

## Chạy

```bash
python -m v2 status                    # cấu hình + auth + hàng đợi
python -m v2 enroll                    # mở link OAuth, hứng callback ở :8080
python -m v2 scan                      # quét minute mới 1 lần
python -m v2 process                   # xử lý hàng đợi (dry-run)
python -m v2 process --send            # xử lý + gửi thật
python -m v2 run --send                # vòng lặp chính
python -m v2 run --ws --send           # kèm WebSocket đường nhanh
python -m v2 action --token T --act deliver_all   # duyệt tay
```

Mặc định an toàn: không `--send`/`SEND_MODE=1` thì **không gửi thật**.

## Enroll từ xa (người không ngồi cùng máy)

`enroll` hứng callback ở máy local. Người ở xa: gửi họ link `authorize`
(in ra khi chạy `enroll`), họ bấm Đồng ý; nếu `redirect_uri` là tunnel/URL
công khai thì callback tự về. Không có tunnel thì lấy `code`+`state` trên
URL redirect rồi:

```bash
python -m v2 complete --code <CODE> --state <STATE>
```

## Bảo mật

Máy này giữ **user token toàn quyền của mọi người enroll** — xem cảnh báo
`docs/MULTI_USER.md §5` và `V2_ARCHITECTURE §11`. Chỉ chạy trên máy admin,
backup `data/state.db` hàng ngày (mất = cả công ty enroll lại), key backup
để tách khỏi file.

## Việc cần bạn làm

Nền tảng đã sẵn, nhưng để chạy với Lark thật cần (đúng thứ tự
`V2_ARCHITECTURE §8`, kiểm 1–3 trước khi tin):

1. **App + scope + Availability** (rào lớn nhất, không phải việc code):
   bật OAuth, thêm scope trong `OAUTH_SCOPES`, publish app cho toàn công ty.
   Xác nhận **tên scope** đúng với Console tenant (mẫu trong `.env.example`
   có thể khác tên thật).
2. **Kiểm chứng endpoint đọc** đánh dấu `[VERIFY]` trong `lark_api.py`
   (`minutes_list`, `minutes_media_url`, `calendar_*`, `vc_meeting_recording`)
   bằng một cuộc họp thật — Lark có nhiều biến thể response.
3. **WebSocket**: xác nhận `minutes.minute.generated_v1` bắn cho họp thường
   và `card.action.trigger` cấu hình đúng (tránh lỗi 200340).
4. **Domain riêng** trước khi enroll người thứ hai (`V2_LONGTERM §3.1`).

Chưa có (mốc sau): hộp thư OAuth trên Vercel, mở rộng web-portal (trang nhân
sự / cuộc họp / sức khỏe), heartbeat, job xóa transcript theo retention.
