# Sang máy mới: có gì rồi, phải làm lại cái gì

Viết 31/07/2026, **viết lại 03/08/2026** khi đã có hạ tầng đích: **Windows Server,
luôn bật, có GPU NVIDIA**; máy cũ **giữ làm dự phòng nguội** (không chạy song song).

Trả lời đúng hai câu: **cái gì clone repo là có** và **cái gì nằm ngoài repo, phải
mang tay**. Bản đồ tổng thể: [README.md](../README.md). Vận hành:
[V2_MAINTENANCE.md](V2_MAINTENANCE.md).

> **Ràng buộc thời gian, đọc trước khi lên lịch:** refresh token Lark sống **7
> NGÀY** và trượt theo chính vòng `run` (đã đo, V2_MAINTENANCE §28). Vòng `run`
> tắt quá 7 ngày = **mọi người phải enroll lại**. Nên cửa sổ chuyển máy phải
> khép trong 7 ngày kể từ lần cuối máy cũ chạy `run` — cứ để máy cũ chạy tới
> đúng lúc máy mới sẵn sàng, rồi mới tắt.

---

## 0. Năm thứ bản 31/07 KHÔNG có (đừng làm theo bản cũ)

| Mới | Hệ quả khi chuyển máy |
|---|---|
| Sao lưu tự động + **khóa Fernet để ngoài repo** (§6) | phải mang thêm `%USERPROFILE%\.meetingxlark\fernet-key.txt` |
| Scheduled Task `V2_Alerts` + watchdog Hermes (§28) | thêm 2 thứ phải đăng ký lại, và phải **gỡ ở máy cũ** |
| Biên bản là `.docx` (§35) | không ảnh hưởng chuyển máy, nhưng `run` phải khởi động lại mới ăn |
| Vercel Blob đã siết còn ~9 ops/ngày (§32) | **không deploy lại** — đụng vào là dễ mất bản đã siết |
| App secret nằm ở HEAD repo `origin` (§37) | chuyển máy là **thời điểm đúng nhất để xoay secret** — xem §5 dưới |

Và một thứ chỉ lộ ra khi đo trên máy này: **`run` đang mượn Python của Hermes** (§1).

---

## 1. Bảng phân loại — nguồn của mọi rắc rối khi chuyển máy

| Thứ | Ở đâu | Clone repo là có? |
|---|---|---|
| Code V2 + V1, tài liệu, launcher `.bat` | `v2/`, `v1/`, `docs/`, gốc repo | ✅ |
| Plugin cửa vào Hermes | `hermes/v2-enroll-gate/` | ✅ (bản gốc; phải **cài** sang Hermes) |
| Hàm serverless Vercel | `v2/vercel-oauth/` | ✅ (**đã deploy rồi, đừng deploy lại**) |
| **Whisper server** | `E:\whisper\` | ❌ |
| **Hermes** (bản thân nó) | `%LOCALAPPDATA%\hermes\` | ❌ |
| **`v2\.env`** — secret + `V2_FERNET_KEY` | không commit | ❌ |
| **`v2\data\state.db`** — token đã enroll, hàng đợi, phân quyền hỏi đáp | không commit | ❌ |
| **`%USERPROFILE%\.meetingxlark\fernet-key.txt`** | ngoài repo có chủ ý (§6) | ❌ |
| **`.env` + `config.yaml` của Hermes** | `%LOCALAPPDATA%\hermes\` | ❌ |
| Transcript cũ | `v2\data\transcripts\` | ❌ (mất = job cũ phải phiên âm lại) |
| Scheduled Task `V2_Orchestrator`, `V2_Alerts`, `Hermes_Gateway` | Task Scheduler | ❌ (chạy lại `install-autostart-v2.bat`) |
| App Lark + scope, Base "nội dung đã chốt", Vercel | đám mây | — không cần làm lại |

Rút ra: **repo cho bạn code, không cho bạn hệ thống đang chạy.**

### Cái bẫy Python — đo 03/08/2026, chưa từng ghi ở đâu

```
where python
  C:\...\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe   3.11.15  ← đứng đầu
  C:\...\AppData\Local\Programs\Python\Python312\python.exe
```

Ba tiến trình V2 đang chạy bằng **hai Python khác nhau**, và không ai cố ý:

| Tiến trình | Chạy bằng | Vì sao |
|---|---|---|
| `run-v2-auto.bat` → vòng `run` | **Python 3.11 trong venv của Hermes** | gọi `python` trần, PATH trả về cái này |
| `mcp-meetings.bat` (Hermes spawn) | Python 3.12 | `set PY=` dán cứng |
| `v2-gate.bat` (plugin spawn) | Python 3.12 | `set PY=` dán cứng |

Máy này sống được **chỉ vì V2 deps đã lỡ cài vào cả hai** (đã kiểm: `httpx`,
`cryptography`, `lark_oapi` import được ở cả 3.11 lẫn 3.12). Máy mới cài theo thứ
tự khác → `python` trần trỏ chỗ khác → vòng `run` chết vòng lặp, hoặc tệ hơn:
chạy được nhưng bằng interpreter không ai định.

**Làm ở máy mới:** thêm `set PY=` dán cứng vào `run-v2-auto.bat` +
`run-v2-alerts.bat` (cùng khuôn với hai file kia) và gọi `"%PY%" -m v2 …`. Một
Python 3.12 duy nhất, cài `v2\requirements.txt` vào đúng nó. Đừng để PATH quyết định.

---

## 2. Thứ tự dựng lại

### Bước 0 — lấy bản sao NHẤT QUÁN từ máy cũ (đừng copy `state.db`)

⚠️ **Copy `v2\data\state.db` là chép về bản THIẾU.** Máy này đang chạy WAL: file
`.db` 172 KB nhưng `state.db-wal` **4,0 MB** (đo 03/08). Bản chép thiếu vẫn mở
được bình thường nên không ai biết. Dùng đường chính thức:

```bash
python -m v2 backup          # VACUUM INTO -> bản đã checkpoint, nhất quán
python -m v2 backup --list
```

Cả sáu thứ dưới đây gom bằng **một lệnh** (chạy lại được nhiều lần, tự `v2 backup`,
tự sinh `SHA256.txt` + `README.txt` có vân tay khóa):

```bash
tools\goi-may-moi.bat            REM mặc định gom vào D:\may-moi
tools\goi-may-moi.bat F:\goi     REM hoặc chỉ chỗ khác
```

Chạy lượt **cuối cùng SAU KHI đã tắt vòng `run`** ở máy cũ (bước 8) — mọi enroll
và job phát sinh giữa lúc chép và lúc tắt sẽ mất im lặng.

Danh sách nó gom, nếu muốn làm tay (USB / thư mục nội bộ, **không** đẩy lên git):

```
v2\data\backups\state-<ngày>.db          <- bản vừa tạo, KHÔNG phải state.db
v2\.env                                   <- secret + V2_FERNET_KEY
%USERPROFILE%\.meetingxlark\fernet-key.txt
%LOCALAPPDATA%\hermes\.env
%LOCALAPPDATA%\hermes\config.yaml
v2\data\transcripts\                      <- không mang = job cũ phiên âm lại
```

Kèm `E:\whisper\` (`server.py`, `run-server.bat`, `requirements.txt`,
`*-prompt.txt`) — **bỏ `.venv\` và `hop.mp3`**, dựng lại ở máy đích.

⚠️ **`V2_FERNET_KEY` và `state.db` phải đi CÙNG NHAU.** Đổi key mà giữ db cũ =
mọi token thành rác, cả công ty enroll lại (V2_HANDOFF §5.2). `README.txt` trong
thư mục backup có sẵn **vân tay khóa** — đối chiếu trước khi tin.

### Bước 1 — Python + ffmpeg + repo

- **Một** Python 3.12, ghi nhớ đường dẫn tuyệt đối của nó.
- ffmpeg trên PATH (`winget install Gyan.FFmpeg`).

```bash
git clone <repo> <đường-dẫn>
cd <đường-dẫn>
"<python312>\python.exe" -m pip install -r v2\requirements.txt
```

Về **clone repo nào**: `origin` (`tientham2005/MeetingxLark`) có đủ code mới nhất
nhưng history chứa app secret đang dùng thật + 35 MB bản ghi họp (§29, §37).
`lamson` sạch nhưng **đang chậm hơn** (`4a64d52` vs local `4537021`). Cách sạch
nhất: **xoay secret ở Console TRƯỚC** (bước 5), rồi clone `origin` — lúc đó secret
trong history đã là rác.

Copy `v2\.env` vào, đổi tên bản backup thành `v2\data\state.db` (xoá `-wal`,
`-shm` nếu có), đặt `fernet-key.txt` vào `%USERPROFILE%\.meetingxlark\`.

Rồi sửa **6 chỗ dán cứng** ở §5.

### Bước 2 — Whisper server (máy này CÓ GPU — đổi cấu hình)

`server.py` **đã tự dò CUDA** rồi chọn `cuda/float16/large-v3`; nhưng
`run-server.bat` đang **ghi đè bằng `set WHISPER_MODEL=small`** (cấu hình cho máy
cũ không GPU). Ở máy mới:

```bat
set WHISPER_MODEL=          REM để TRỐNG -> tự chọn large-v3 trên GPU
```

⚠️ **Chưa ai đo đường GPU trên hệ này.** `faster-whisper`/`ctranslate2` cần
cuBLAS + cuDNN đúng phiên bản; thiếu là nó **ném lỗi lúc nạp model**, không phải
lúc cài. Cách kiểm ngay, đừng đợi cuộc họp thật:

```bash
python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"   # phải > 0
curl http://localhost:8000/health
```

Mốc so sánh của máy cũ (CPU, `small`): **0,59x realtime** — họp 1 tiếng ≈ 35 phút.
Đo lại con số này trên GPU rồi ghi vào V2_MAINTENANCE — nó là thứ quyết định
`SETTLE_MINUTES` và cảm giác "bao lâu thì có biên bản".

Cổng **8000**, khớp `TRANSCRIBE_URL` trong `v2\.env`.

### Bước 3 — Hermes

1. Cài Hermes **0.19.0 trở lên** (máy cũ: `v0.19.0 (2026.7.20)`, install method
   `git`). Trên Windows nó ở `%LOCALAPPDATA%\hermes\`, exe ở
   `…\hermes-agent\venv\Scripts\hermes.exe` (**không** ở `~/.hermes/`).
2. Copy `.env` + `config.yaml` từ bước 0 vào `%LOCALAPPDATA%\hermes\`.
3. **Cài thư viện adapter Feishu** — Hermes không tự cài:
   ```
   %LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe -m pip install "lark-oapi==1.6.8" "qrcode==7.4.2"
   ```
4. **Đăng nhập lại Codex**: `codex login` → cần trình duyệt. Trên Windows Server
   có RDP thì mở trình duyệt trong phiên RDP là xong; đây vẫn là **chỗ ma sát duy
   nhất chưa ai đo** (`auth.json` có thể ràng buộc thiết bị). Mất Codex = mất
   recap (LLM đi qua `api_server` của Hermes, §15) — kiểm bằng `python -m v2 doctor`.
5. Sửa `mcp_servers.meetings.command` trong `config.yaml` nếu repo không ở
   `E:\meetingxlark`.
6. Cài plugin cửa vào: `hermes\install-plugin.bat`, rồi **PHẢI kiểm**
   `hermes plugins list | findstr v2-enroll-gate` thấy `enabled`. Không bật được
   mà `FEISHU_ALLOW_ALL_USERS=true` thì **bot mở cho cả tenant**.
7. `hermes gateway install` (terminal **Administrator**) + chạy
   `hermes-watchdog.bat` — xem bước 6 về đặc thù máy server.

### Bước 4 — Vercel: KHÔNG deploy lại

Hàm OAuth + dashboard là **đám mây, không thuộc máy nào**. Đổi máy không đụng tới
nó. Chỉ kiểm:

```bash
cd v2\vercel-oauth
npx vercel env ls          # cần STATUS_PUSH_SECRET + BLOB_READ_WRITE_TOKEN
```

⚠️ Bản đang chạy (`dpl_3EJGFqjUe7r94BomFEiZCBSaxfjM`) là bản **đã siết Blob từ
~427 xuống ~9 ops/ngày** (§32). Deploy đè bằng code cũ là mở lại đường tự khoá
cửa enroll. Giữ **cùng domain** thì Console không phải sửa gì; đổi domain thì
`OAUTH_REDIRECT_URI` trong `v2\.env` **và** redirect URI trong Console phải khớp
từng ký tự.

### Bước 5 — Lark Console: không phải làm gì vì đổi máy, NHƯNG nên xoay secret

Adapter Feishu chỉ mở **WebSocket đi ra**: không cần IP công cộng, không cần
domain, không sửa Console khi đổi máy. Khác hẳn webhook.

Việc còn tồn ở §37 lại **đúng lúc làm bây giờ**: `SENDER_APP_SECRET` đã nằm trần
trong history repo `origin`. Xoay ở Console rồi điền bản mới vào **cả hai chỗ**
(`v2\.env` **và** `%LOCALAPPDATA%\hermes\.env` — cùng một giá trị, đã so) khi
dựng máy mới, thay vì xoay riêng một lần nữa sau này.

⚠️ **Chưa đo: xoay app secret có làm mất refresh token của người đã enroll
không.** Làm lúc còn máy cũ để lùi được, và báo trước cho 3 người đã enroll.
**Không rewrite history rồi force push** (§29).

### Bước 6 — đặc thù máy luôn bật (khác hẳn máy cũ)

`install-autostart-v2.bat` in ra dòng *"máy PHẢI đăng nhập Windows thì V2 mới
chạy (đây là máy cá nhân, không phải server)"* — **dòng đó nay sai với máy mới,
nhưng ràng buộc thì vẫn còn**:

- Cả `V2_Orchestrator` (`/SC ONLOGON`) lẫn `hermes gateway install` đều cần **một
  phiên người dùng đang đăng nhập**: Hermes đọc `%LOCALAPPDATA%`, Codex OAuth
  gắn với hồ sơ người dùng. Chạy `/RU SYSTEM` sẽ đổi `%LOCALAPPDATA%` sang
  `C:\Windows\System32\config\systemprofile` → Hermes không thấy `.env` của bạn.
- Cách rẻ và giống-đã-kiểm-chứng nhất: một tài khoản chuyên dụng + **autologon**,
  và **đừng để phiên bị lock/logoff**. Giữ nguyên `/SC ONLOGON`.
- Đăng ký: `install-autostart-v2.bat` (tạo cả `V2_Orchestrator` lẫn `V2_Alerts`
  15 phút/lần), rồi `install-autostart-v2.bat /trangthai` phải thấy **cả ba** dòng
  `CO`.

**Và đây là lúc dọn nguyên nhân chết thật của máy cũ** (§31.2 — user hoãn đúng
tới lúc đổi hạ tầng): các lần `run` chết là do **máy tự ngủ** (Kernel-Power ID 42
khớp từng giây với mã thoát `-1073741205`). Trên máy mới:

```bat
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off
```

### Bước 7 — kiểm, theo đúng thứ tự này

```bash
python -m v2 selftest      # 274 phép kiểm, ~15s, không mạng, không đụng state.db thật
python -m v2 doctor        # phải ra SẴN SÀNG
```

`doctor` phải đủ: người enroll + hạn refresh, whisper sống, LLM recap (gọi thử
thật), Base. Dòng `[!] Base: ai có link cũng đọc được` là **trạng thái đã chấp
nhận** (§28.1), không phải lỗi mới.

Rồi nhắn thử bot trong Lark và soi:

```bash
findstr "v2-gate" "%LOCALAPPDATA%\hermes\logs\gateway.log"
```

Có tin vào mà không có dòng `[v2-gate]` nào = plugin không chạy = **cửa đang trống**.

Cuối cùng, một lượt đầu-cuối thật: `python -m v2 process` (dry-run) trên một job
cũ → xem nó **dùng lại transcript, không phiên âm lại**. Đó là phép kiểm chứng
minh `state.db` + `transcripts/` sang đủ.

### Bước 8 — hạ máy cũ xuống "dự phòng nguội"

⚠️ **Tắt máy cũ TRƯỚC khi bật máy mới.** Hai máy cùng `app_id` thì Lark **nhận cả
hai WebSocket, không báo lỗi nào**, và tin nhắn mất im lặng (đã đo). Ở máy cũ:

```bash
install-autostart-v2.bat /go        # xoá V2_Orchestrator + V2_Alerts + Startup item
schtasks /Delete /TN "Hermes_Gateway" /F
```

Rồi kết thúc `python.exe` đang chạy `-m v2 run` và cửa sổ watchdog. **Giữ đĩa
nguyên trạng** — nó là đường lùi duy nhất nếu bước 7 hỏng.

---

## 3. Cái gì KHÔNG mang được, phải chấp nhận làm lại

| Thứ | Vì sao | Hệ quả |
|---|---|---|
| Session/memory của Hermes | trong `sessions/`, `memories/` của Hermes | bot mất ngữ cảnh cũ; hỏi đáp vẫn đúng vì dữ liệu thật ở Base |
| `auth.json` (Codex OAuth) | có thể ràng buộc thiết bị — **chưa đo** | phải `codex login` lại |
| Log cũ (`v2\data\logs\`) | không commit | mang được nếu muốn tra lịch sử, không bắt buộc |

---

## 4. Nếu sau này tách sang nhiều máy (không phải chỉ đổi máy)

**Hermes và V2 buộc phải cùng máy** ở cấu hình hiện tại:

1. MCP nối bằng **stdio** — Hermes spawn `mcp-meetings.bat` làm tiến trình con.
2. Recap gọi `http://127.0.0.1:8642` — `api_server` của Hermes chỉ bind loopback.

Muốn tách: đổi `mcp_servers.meetings` từ `command` sang `url`+`headers` (HTTP/SSE),
và cho `api_server` bind ra LAN + đổi `LLM_BASE_URL`. **Cả hai chưa ai làm.**

Whisper thì tách được ngay — nó đã là HTTP: đổi `TRANSCRIBE_URL` thành
`http://<ip-máy-GPU>:8000`, không sửa code (V2_LONGTERM §6 bước 5). Với máy mới
có GPU thì không cần nữa, nhưng đường này vẫn để mở.

---

## 5. Sáu chỗ dán cứng đường dẫn — grep khi chuyển máy

```
mcp-meetings.bat        set PY=...Python312\python.exe
v2-gate.bat             set PY=...Python312\python.exe
run-v2-auto.bat         `python` TRẦN  -> nên thêm set PY= (xem §1)
run-v2-alerts.bat       `python` TRẦN  -> nên thêm set PY= (xem §1)
start-v2.bat            set WHISPER_BAT=E:\whisper\run-server.bat   (+ `python` trần)
hermes config.yaml      mcp_servers.meetings.command = E:/meetingxlark/mcp-meetings.bat
hermes plugin __init__  GATE_BAT = E:\meetingxlark\v2-gate.bat  (đổi được bằng env V2_GATE_BAT)
```

`hermes-watchdog.bat` không cần sửa: nó dùng `%LOCALAPPDATA%`, tự đúng.
