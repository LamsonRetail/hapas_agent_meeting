# Sang máy mới: có gì rồi, phải làm lại cái gì

Viết 31/07/2026. Trả lời đúng hai câu: **cái gì đã có trong repo (clone là xong)**
và **cái gì nằm ngoài repo, phải dựng lại bằng tay**.

Bản đồ tổng thể ở [README.md](../README.md). Vận hành hằng ngày ở
[V2_MAINTENANCE.md](V2_MAINTENANCE.md).

---

## 1. Bảng phân loại — nguồn của mọi rắc rối khi chuyển máy

| Thứ | Ở đâu | Clone repo là có? |
|---|---|---|
| Code V2 + V1 | `v2/`, `v1/` | ✅ |
| Tài liệu | `docs/` | ✅ |
| Launcher V2 | `start-v2.bat`, `mcp-meetings.bat`, `v2-gate.bat` | ✅ |
| Plugin cửa vào Hermes | `hermes/v2-enroll-gate/` | ✅ (bản gốc; phải **cài** sang Hermes) |
| Hàm serverless Vercel | `v2/vercel-oauth/` | ✅ (phải **deploy**) |
| **Whisper server** | `E:\whisper\` | ❌ |
| **Hermes** (bản thân nó) | `%LOCALAPPDATA%\hermes\` | ❌ |
| **`v2/.env`** — secret V2 | không commit | ❌ |
| **`.env` + `config.yaml` của Hermes** | `%LOCALAPPDATA%\hermes\` | ❌ |
| **`v2/data/state.db`** — token đã enroll, hàng đợi | không commit | ❌ |
| **`V2_FERNET_KEY`** | trong `v2/.env` | ❌ |
| App Lark + scope đã duyệt | Lark Console (đám mây) | — không cần làm lại |
| Base "nội dung đã chốt" | Lark Base (đám mây) | — không cần làm lại |

Rút ra: **repo cho bạn code, không cho bạn hệ thống đang chạy.** Bốn thứ phải
mang tay là `v2/.env`, `state.db`, cấu hình Hermes, và whisper.

---

## 2. Thứ tự dựng lại (làm đúng thứ tự này)

### Bước 0 — mang theo từ máy cũ

Copy đúng bốn thứ này (USB / thư mục nội bộ, **không** đẩy lên git):

```
v2\.env                       <- secret + V2_FERNET_KEY
v2\data\state.db              <- token đã enroll, hàng đợi, lời mời
%LOCALAPPDATA%\hermes\.env
%LOCALAPPDATA%\hermes\config.yaml
```

⚠️ **`V2_FERNET_KEY` và `state.db` phải đi CÙNG NHAU.** Đổi key mà giữ db cũ =
mọi token thành rác, mọi người phải enroll lại (V2_HANDOFF §5.2).

### Bước 1 — Python + ffmpeg

- Python 3.12 (V2 đang dùng bản ở `C:\Users\<user>\AppData\Local\Programs\Python\Python312`).
  Hai launcher `mcp-meetings.bat` và `v2-gate.bat` **dán cứng đường dẫn này** —
  sửa dòng `set PY=` trong cả hai nếu máy mới khác chỗ.
- ffmpeg trên PATH (V2 gọi để tách audio).

```bash
git clone <repo> E:\meetingxlark
cd E:\meetingxlark
pip install -r v2\requirements.txt
```

Rồi copy `v2\.env` + `v2\data\state.db` từ bước 0 vào.

### Bước 2 — Whisper server

Không nằm trong repo. Dựng lại `E:\whisper\` (`server.py` + `run-server.bat`),
model đặt bằng `set WHISPER_MODEL=` trong `.bat` (đang là `small`), cổng **8000**.
Khớp với `TRANSCRIBE_URL` trong `v2\.env`.

Máy không GPU thì server tự rơi về CPU — đo được **0,59x realtime** (họp 1 tiếng
≈ 35 phút phiên âm).

### Bước 3 — Hermes

1. Cài Hermes (0.19.0 trở lên). Trên Windows nó nằm ở `%LOCALAPPDATA%\hermes\`,
   exe ở `...\hermes-agent\venv\Scripts\hermes.exe` (**không** ở `~/.hermes/`).
2. Copy `.env` + `config.yaml` từ bước 0 vào `%LOCALAPPDATA%\hermes\`.
3. **Cài thư viện adapter Feishu** — Hermes không tự cài:
   ```
   %LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe -m pip install "lark-oapi==1.6.8" "qrcode==7.4.2"
   ```
4. **Đăng nhập lại Codex** (`auth.json` có thể không mang sang được):
   `codex login` → cần trình duyệt. **Đây là chỗ ma sát duy nhất chưa ai đo** —
   trên VPS không màn hình thì phải tìm cách khác.
5. Sửa đường dẫn trong `config.yaml` nếu repo không ở `E:\meetingxlark`:
   `mcp_servers.meetings.command`.
6. Cài plugin cửa vào (từ repo):
   ```
   hermes\install-plugin.bat
   ```
   **PHẢI kiểm** `hermes plugins list | findstr v2-enroll-gate` thấy `enabled`.
   Không bật được mà `FEISHU_ALLOW_ALL_USERS=true` thì **bot mở cho cả tenant**.
7. Tự chạy khi đăng nhập: `hermes gateway install` (chạy trong terminal
   **Administrator** để được Scheduled Task có restart-on-failure; không có
   quyền admin thì nó rơi về Startup folder, không tự restart).

### Bước 4 — Vercel

```bash
cd v2\vercel-oauth
npx vercel --prod
npx vercel env ls          # cần STATUS_PUSH_SECRET + BLOB_READ_WRITE_TOKEN
```

Giữ **cùng domain/alias** thì Lark Console không phải sửa gì. Đổi domain thì phải
sửa `OAUTH_REDIRECT_URI` trong `v2\.env` **và** redirect URI trong Console —
hai chỗ phải khớp từng ký tự.

### Bước 5 — Lark Console: KHÔNG phải làm gì

Adapter Feishu chỉ mở **WebSocket đi ra**: không cần IP công cộng, không cần
domain, không cần sửa Console khi đổi máy. Khác hẳn webhook.

⚠️ **Tắt gateway máy CŨ trước khi bật máy mới.** Hai máy cùng `app_id` thì Lark
nhận cả hai kết nối, **không báo lỗi nào**, và tin nhắn mất im lặng (đã đo).

### Bước 6 — kiểm

```bash
python -m v2 doctor
```

Phải ra **SẴN SÀNG** với đủ: người enroll + hạn refresh, whisper sống, LLM recap
(local, đã gọi thử), Base. Rồi nhắn thử bot trong Lark và soi:

```bash
findstr "v2-gate" "%LOCALAPPDATA%\hermes\logs\gateway.log"
```

Có tin vào mà không có dòng `[v2-gate]` nào = plugin không chạy = cửa đang trống.

---

## 3. Cái gì KHÔNG mang được, phải chấp nhận làm lại

| Thứ | Vì sao | Hệ quả |
|---|---|---|
| Session/memory của Hermes | nằm trong `sessions/`, `memories/` của Hermes | bot mất ngữ cảnh cũ; hỏi đáp vẫn đúng vì dữ liệu thật nằm ở Base |
| `auth.json` (Codex OAuth) | có thể ràng buộc thiết bị — **chưa đo** | phải `codex login` lại |
| Transcript cũ trên đĩa | `v2/data/transcripts/` không commit | job cũ mất khả năng "phát lại không phiên âm lại" nếu không copy theo |

---

## 4. Nếu tách sang nhiều máy (không phải chỉ đổi máy)

**Hermes và V2 buộc phải cùng máy** ở cấu hình hiện tại, vì hai lý do:

1. MCP nối bằng **stdio** — Hermes spawn `mcp-meetings.bat` làm tiến trình con.
2. Recap gọi `http://127.0.0.1:8642` — api_server của Hermes chỉ bind loopback.

Muốn tách thì phải: đổi `mcp_servers.meetings` từ `command` sang `url`+`headers`
(HTTP/SSE), và cho api_server bind ra LAN + đổi `LLM_BASE_URL`. **Cả hai chưa
ai làm, chưa ai đo.**

Whisper thì tách được ngay — nó đã là HTTP: đổi `TRANSCRIBE_URL` thành
`http://<ip-máy-GPU>:8000` là xong, không sửa code (V2_LONGTERM §6 bước 5).

---

## 5. Bốn thứ dán cứng đường dẫn — grep khi chuyển máy

```
mcp-meetings.bat        set PY=...Python312\python.exe
v2-gate.bat             set PY=...Python312\python.exe
start-v2.bat            set WHISPER_BAT=E:\whisper\run-server.bat
hermes config.yaml      mcp_servers.meetings.command = E:/meetingxlark/mcp-meetings.bat
hermes plugin __init__   GATE_BAT = E:\meetingxlark\v2-gate.bat  (đổi được bằng env V2_GATE_BAT)
```
