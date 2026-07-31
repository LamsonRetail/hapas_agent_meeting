# V2 — Việc cần làm (giao cho phiên Claude mới)

Viết 31/07/2026. File này là **đơn đặt việc**: 5 việc đã chốt phạm vi, có sẵn
file:dòng, cách kiểm, và bẫy đã biết. Làm được ngay, không phải đi khảo sát lại.

**Không có trong file này:** ba việc hạng 1 (đổi phiên âm sang GPU `large-v3`,
thử transcript của Lark thay whisper, bỏ phụ thuộc sự kiện lịch). Chúng đổi kiến
trúc nên phải bàn với chủ hệ thống trước — đừng tự làm.

---

## 0. Đọc trước (bắt buộc, theo thứ tự)

1. [V2_HANDOFF.md](V2_HANDOFF.md) — đang ở đâu, cái gì đã kiểm chứng THẬT.
2. [V2_MAINTENANCE.md](V2_MAINTENANCE.md) — sổ tay vận hành. Các mục hay cần:
   §5 bảng chẩn lỗi · §10 tra người dự · §11 Base · §13 Hermes ↔ Lark ·
   §14 cửa vào tự phục vụ · §15 recap qua Hermes · §16 tự chạy.
3. [../README.md](../README.md) — bản đồ "ở đâu có gì" (4 phần ở 4 chỗ).

## 1. Trạng thái hệ thống lúc viết file này

| Thành phần | Trạng thái |
|---|---|
| Orchestrator | đang chạy thật, `PAUSED=0`, `send=THẬT`, tự chạy khi đăng nhập Windows |
| Whisper | `small`, CPU, int8, cổng 8000 — 0,59x realtime |
| Hermes gateway | đang chạy: bot hỏi đáp **và** sinh recap cho V2 qua `api_server` cổng 8642 |
| Người nhận biên bản | đã đạt `calendar[verified]` |
| Base "Biên bản" | 19 cột, có cột theo dõi (chủ/người dự/user_id/file/thời gian whisper) |
| Hàng đợi | `delivered=2`, `discarded=1` |
| Enroll | tự phục vụ qua bot; nonce sống 24h |

`python -m v2 doctor` phải ra **SẴN SÀNG**. Nếu không, sửa cái đó trước.

## 2. Sáu luật không được phá

1. **Đừng chạy `python -m v2 run --ws`.** Hermes dùng CÙNG `app_id`. Lark nhận cả
   hai kết nối WebSocket, **không báo lỗi**, rồi Hermes lặng lẽ mất tin nhắn.
2. **Đừng đổi `V2_FERNET_KEY`** — mọi token đã enroll thành rác.
3. **Đừng thêm hàm ghi/xoá vào `v2/qa.py`.** Nó chỉ đọc, có chủ ý: dữ liệu Base
   bắt nguồn từ lời người ta nói trong họp và chảy thẳng vào prompt của agent.
4. **`v2 gate` và `v2 mcp`: stdout là kênh giao thức.** Thêm `print()` vào hai
   đường đó là hỏng plugin/MCP. Log phải đi stderr.
5. **File `.bat` chỉ dùng ASCII + CRLF.** `chcp 65001` cộng ký tự ngoài ASCII làm
   cmd đọc lệch theo byte offset và file thành rác (§16).
6. **Đừng `Stop-Process` theo chuỗi rộng.** Lọc `-match '-m v2 run'` đã giết lây
   worker con của Hermes hai lần. Liệt kê ra trước, kill theo PID cụ thể.

---

# VIỆC 1 — Cảnh báo qua Lark DM — ✅ XONG 31/07/2026

**Đã làm:** `v2\alerts.py` + bảng `alert_state` + `ALERT_UNION_IDS` /
`ALERT_WHISPER_AFTER_MIN` + lệnh `python -m v2 alerts [--dry-run]` + một mục
trong `doctor`. Chi tiết thiết kế, bốn quyết định và **bảng kết quả đo** ở
**V2_MAINTENANCE §17**. Cả ba "cách kiểm" dưới đây đã chạy và đạt, DM thật có
`message_id` của Lark.

Còn nguyên đề bài bên dưới để đối chiếu sau này.

---

## (đề bài gốc)

**Mục tiêu:** hệ thống tự nói khi nó hỏng, thay vì chờ người vào đọc log.

**Hiện trạng:** không có cảnh báo nào. Mọi thứ phải tự đi xem
`v2\data\logs\v2-<ngày>.log` hoặc dashboard Vercel — mà **đúng lúc hỏng nhất thì
dashboard cũng đứng im**, vì nó chỉ cập nhật khi vòng `run` còn sống
(`orchestrator.run` → `status_push.heartbeat`, [orchestrator.py:281](../v2/orchestrator.py:281)).

**Phải làm:** module mới `v2/alerts.py`, gửi DM bằng bot (`lark_api.im_send_text`,
`id_type="union_id"`) cho danh sách admin. Ba tình huống, mỗi cái CHỈ báo một lần
cho tới khi tình trạng đổi (chống spam — lưu mốc đã báo vào `state.db`):

| Khi nào | Nội dung cần có |
|---|---|
| job chuyển `failed` | tên cuộc họp, `minute_token`, `attempts`, lỗi cuối, và câu lệnh SQL trả job về `queued` (§16 có sẵn) |
| whisper không gọi được > N phút liên tục | `TRANSCRIBE_URL`, số job đang `queued` bị kẹt |
| token của ai còn ≤2 ngày | tên người đó **+ link enroll tự phục vụ** để họ tự gia hạn |

- Cấu hình: thêm `ALERT_UNION_IDS` (danh sách, phẩy) + `ALERT_WHISPER_AFTER_MIN`
  (mặc định 15) vào `v2/config.py` và `v2/.env.example`. Trống = tắt hẳn.
- Chỗ gọi: trong vòng `while True` của [orchestrator.run](../v2/orchestrator.py:251),
  sau `process_queue()`. Bọc try/except riêng — **cảnh báo hỏng không được làm
  chết vòng run**.
- Việc token sắp hết hạn: `tokenstore.list_users()` có `refresh_exp`;
  `doctor._check_users` đã có logic ngưỡng 3 ngày, đọc lại cho khớp.
- Link enroll: `oauth.start()` trả `(url, nonce)`. Nonce nay sống 24h nên gửi qua
  DM là dùng được thật (§14).

**Cách kiểm (đo được, đừng chỉ "chạy thử xem"):**
1. `ALERT_UNION_IDS=<union_id của mình>`, tạo một job giả rồi `set status='failed'`
   → phải nhận đúng 1 DM, chạy vòng thứ hai **không** nhận thêm.
2. Trỏ `TRANSCRIBE_URL` sang cổng chết + `ALERT_WHISPER_AFTER_MIN=1` → sau ~1 phút
   nhận DM; bật whisper lại → lần sau không báo nữa.
3. Sửa tay `refresh_exp` của một token về "còn 1 ngày" → nhận DM kèm link bấm được.

**Bẫy:** `im_send_text` cần `union_id` (xem [pipeline.deliver](../v2/pipeline.py)).
`open_id` cũng chạy nhưng phải đổi `id_type`. Đừng gửi vào group.

---

# VIỆC 2 — Whisper watchdog — ✅ XONG 31/07/2026 (chọn cách **b**)

**Đã làm cách (b)**, nằm chung trong `alerts.py`: mỗi vòng `run` gọi `/health`,
hỏng thì **in log MỖI VÒNG + DM một lần** sau `ALERT_WHISPER_AFTER_MIN` phút,
**không spawn tiến trình**. Cách (a) trong `.bat` **chưa làm** — chỉ làm nếu chủ
hệ thống muốn tự phục hồi thật (§17 ghi rõ điều kiện).

Đã đo cả nửa sau của "cách kiểm": chạy `process_queue` thật 3 vòng với whisper
tắt → `attempts` **giữ 0**, status về `queued`; **đối chứng** job hỏng vì lỗi
khác → `attempts` lên 1, 2, 3 (tức cái đếm vẫn sống, không phải nó chết). Bảng
đầy đủ ở V2_MAINTENANCE §17.

---

## (đề bài gốc)

**Mục tiêu:** whisper chết giữa đường thì có người/máy bật lại, thay vì job nằm chờ.

**Hiện trạng:** [run-v2-auto.bat](../run-v2-auto.bat) chỉ kiểm whisper **lúc khởi
động** (`:waitloop`, dòng 64–73) rồi vào `:loop` chạy python (dòng 80). Chết giữa
đường thì không ai bật lại. Nhờ bản sửa 31/07 (`TranscribeUnavailable` không tiêu
`MAX_ATTEMPTS`) thì job **không mất**, nhưng nằm `queued` im lặng vô hạn.

**Phải làm — chọn MỘT, đừng làm cả hai:**

- **(a) Trong `.bat`** (đơn giản, không đụng Python): tách whisper ra một vòng
  riêng chạy song song bằng `start` một `.bat` con: mỗi 60s `curl` `/health`, chết
  thì `start` lại `E:\whisper\run-server.bat` và ghi log. Nhớ luật ASCII+CRLF.
- **(b) Trong `orchestrator.run`** (dễ kiểm hơn): mỗi vòng gọi `/health`; hỏng thì
  chỉ **ghi log + gửi cảnh báo** (Việc 1), **KHÔNG spawn tiến trình** — V2 không
  nên đi bật cửa sổ Windows. Cách này không tự phục hồi nhưng minh bạch.

Đề nghị: làm (b) trước vì nó nằm chung với Việc 1 và kiểm được bằng test; (a) chỉ
làm nếu chủ hệ thống muốn tự phục hồi thật.

**Cách kiểm:** tắt whisper giữa lúc `run` đang chạy → trong ≤1 vòng
(`POLL_INTERVAL=300s`) phải thấy log/DM; bật lại → job `queued` tự đi tiếp, và
`attempts` **không tăng** trong suốt thời gian whisper tắt (đây là điểm dễ hỏng
lại nhất, kiểm bằng `select attempts from jobs`).

---

# VIỆC 3 — Rotate secret + xoá khỏi git history (hạng 3, RỦI RO CAO)

**⚠️ Việc này viết lại history và làm V2 + Hermes NGỪNG CHẠY tới khi cập nhật
secret mới. PHẢI hỏi chủ hệ thống và được đồng ý rõ ràng trước khi bắt đầu.**

**Hiện trạng đã đo:**
- `v1/config.bat` **đang được git tracked** (vào từ commit `985573f`) và chứa
  `OPENAI_API_KEY` + `SENDER_APP_SECRET` **dạng trần**. `SENDER_APP_SECRET` chính
  là app secret Lark mà V2 **và** Hermes đang dùng (`cli_aae288361ef89eed`).
- History còn ~35 MB media họp thật; blob lớn nhất **30,3 MB**:
  `minutes/obsg23lsr45qp273r1m6i8nc/Web scraper.mp4`. Đã `git rm --cached` nên
  không commit thêm, nhưng **vẫn nằm trong history**.
- Repo là **private** (`gh api repos/tientham2005/MeetingxLark` → `"private":true`).

**Thứ tự phải đúng, nếu không là tự khoá mình ra ngoài:**

1. **Backup trước:** copy cả thư mục repo + `v2\.env` + `v2\data\state.db` +
   `%LOCALAPPDATA%\hermes\.env` ra chỗ khác.
2. **Rotate ở Console trước, đổi file sau.** Lark Console → app
   `cli_aae288361ef89eed` → tạo app secret mới. Rồi cập nhật **ba chỗ**:
   `v2\.env` (`LARK_APP_SECRET`), `%LOCALAPPDATA%\hermes\.env` (`FEISHU_APP_SECRET`),
   `v1\config.bat` (`SENDER_APP_SECRET`).
3. **Khởi động lại cả hai:** `hermes gateway restart` và tiến trình `v2 run`
   (xem §16 để bật lại đúng cách; nhớ luật 6 khi kill).
4. **Kiểm ngay:** `python -m v2 doctor` phải SẴN SÀNG; nhắn thử bot trong Lark
   phải thấy dòng `[v2-gate] cho vào` trong
   `%LOCALAPPDATA%\hermes\logs\gateway.log`.
5. **OpenAI key**: recap đã chuyển sang Hermes (§15) nên key đó **không còn dùng**
   — vào dashboard OpenAI **xoá** nó, đừng chỉ đổi. Dòng comment `# LLM_API_KEY=sk-…`
   trong `v2\.env` cũng phải xoá.
6. **Xoá khỏi history** (chỉ làm sau khi 1–5 xong và hệ thống chạy lại được):
   `git filter-repo --invert-paths --path config.bat --path v1/config.bat
   --path minutes --path out --path jobs --path poller_state.json`
   rồi `git push --force`. Nếu chưa có `git filter-repo` thì cài, **đừng dùng
   `filter-branch`**.
7. Sau force-push: `git gc --prune=now --aggressive`, và bảo mọi máy khác clone lại.

**Cách kiểm:** `git log --all --oneline -- v1/config.bat` phải **rỗng**;
`git rev-list --objects --all | git cat-file --batch-check … | awk '$3>1000000'`
phải không còn blob MB nào; `gh api .../MeetingxLark --jq .size` giảm rõ rệt.

---

# VIỆC 4 — `v2 users` + `v2 revoke` — ✅ XONG 31/07/2026

Đã làm đúng phạm vi, kèm 3 lưới an toàn (bắt buộc `--yes`, cảnh báo khi đó là
người active cuối cùng, báo rõ khi không thấy người) và tra được bằng cả
`--open-id` lẫn `--union-id`. Chi tiết + bảng kết quả đo: **V2_MAINTENANCE §18**.

Một điểm khác đề bài, có lý do: chu trình revoke chạy trên **bản sao `state.db`**
chứ không phải DB thật. Hệ thống chỉ có MỘT người enroll — thu hồi thật là bỏ V2
ở trạng thái không đọc được gì cho tới khi người đó tự bấm lại link OAuth, mà
không ai bấm hộ được. Trên DB thật chỉ chạy hai phép không phá (thiếu `--yes`
phải từ chối; người không tồn tại phải báo rõ).

---

## (đề bài gốc)

**Mục tiêu:** quản trị người đã enroll bằng lệnh, không phải viết Python.

**Hiện trạng:** [tokenstore.revoke](../v2/tokenstore.py:141) đã tồn tại nhưng
**không có lệnh CLI nào gọi nó**. Muốn khoá một người phải tự mở Python. Danh sách
người thì đang phải đọc qua `v2 status` / `v2 doctor` (lẫn với thứ khác).

**Phải làm** trong [v2/__main__.py](../v2/__main__.py) (theo mẫu `cmd_invites`):

- `v2 users` — bảng: tên · open_id · union_id · status · refresh còn mấy ngày ·
  lần dùng cuối. Dùng `tokenstore.list_users(active_only=False)` để thấy cả người
  đã revoke.
- `v2 revoke --open-id <ou_...>` — gọi `tokenstore.revoke`, in tên người vừa khoá.
  **Bắt buộc `--yes`** để không khoá nhầm (đây là hành động làm V2 mất quyền đọc
  minutes của người đó).
- Cân nhắc `v2 revoke --union-id` cho tiện, vì `union_id` là thứ hay có trong tay
  (log của gate in union_id).

**Cách kiểm:** `v2 users` thấy đúng 1 người đang `active`; `v2 revoke` người đó →
`v2 users` thấy `revoked`; `v2 gate --union-id <của họ> --no-send` phải trả
`decision != "allow"`; rồi enroll lại (bấm link) → về `active`, `gate` trả `allow`.
**Nhớ enroll lại sau khi test**, đừng để hệ thống không còn ai.

---

# VIỆC 5 — Hai chỗ gây đọc sai — ✅ XONG 31/07/2026

**5a:** làm phương án **(c)** — snapshot mang `pushed_by: "run" | "manual"`,
trang Vercel hiện `đẩy tay — không rõ vòng run đang gửi thật hay dry-run`.
Đã deploy production và đo cả hai chiều. **Phương án (a) không dùng được** và
đây là lý do đáng ghi: `.env` ghi `SEND_MODE=0` còn vòng thật chạy `run --send`,
nên cờ đúng chỉ nằm trong RAM của tiến trình đó — `push-status` đọc `.env` vẫn
ra 0, vẫn nói dối. Chi tiết: **V2_MAINTENANCE §9**.

**5b:** user chọn **(a)**. Cột `Trạng thái` nay là `đã phát` / `phát hỏng` /
`không có recap`, tính từ bảng `deliveries`. Đã đổi option của cột trên Base
thật (`PUT`, không phải PATCH) và đổ lại 2 record cũ. Kéo theo: bỏ
`bitable.mark_final()` + lệnh `v2 base-final`, sửa prompt `qa.py` và schema
`mcp_server.py`. Chi tiết: **V2_MAINTENANCE §11**.

---

## (đề bài gốc)

### 5a. Dashboard hiện `send_mode` sai

[status_push.py:88](../v2/status_push.py:88) lấy `bool(config.SEND_MODE)` của
**tiến trình nào đẩy snapshot cuối**. Chạy tay `python -m v2 push-status` (không
có `--send`) là trang Vercel hiện "dry-run" **dù orchestrator đang gửi thật** —
đọc sai trạng thái hệ thống, đúng chỗ không nên đọc sai.

Phải làm: hoặc (a) `push-status` thừa hưởng `SEND_MODE` từ `.env` và **không**
ghi đè khi chạy tay, hoặc (b) thêm cờ `--send` cho `push-status`, hoặc (c) tốt
nhất: snapshot ghi rõ `pushed_by: "run" | "manual"` và trang Vercel hiển thị
"đẩy tay" để người xem biết đây không phải trạng thái vòng chạy.

Kiểm: `v2 run --send` đẩy → trang hiện gửi thật; chạy `push-status` tay → trang
**không** được biến thành "dry-run" một cách âm thầm.

### 5b. Cột `Trạng thái` trong Base đã vô nghĩa

[bitable.py:38](../v2/bitable.py:38) `F_STATUS` với `draft`/`final`. Chủ hệ thống
**đã bỏ tính năng approve** (31/07, xem V2_HANDOFF §4.5) nên không còn đường nào
tự flip sang `final` — mọi record sẽ ở `draft` **mãi mãi**, chỉ trừ khi ai đó gọi
tay `v2 base-final`.

Phải làm — hỏi chủ hệ thống chọn:
- **(a)** đổi ý nghĩa cột thành cái phản ánh sự thật: `đã phát` / `phát hỏng` /
  `không có recap` (đọc từ bảng `deliveries`), hoặc
- **(b)** bỏ cột (xoá khỏi `SCHEMA` + `write_draft`; cột trên Base xoá tay), hoặc
- **(c)** giữ nguyên nhưng ghi vào sổ tay §11 một dòng "draft là bình thường,
  không phải chưa xong" — rẻ nhất, và đủ nếu chỉ sợ người sau đọc sai.

---

## Thứ tự đề nghị

1. ~~**Việc 1** (cảnh báo)~~ — ✅ XONG 31/07/2026, §17 sổ tay.
2. ~~**Việc 2b** (watchdog kiểu log/alert)~~ — ✅ XONG 31/07/2026, chung với Việc 1.
3. ~~**Việc 4** (`users`/`revoke`)~~ — ✅ XONG 31/07/2026, §18 sổ tay.
4. ~~**Việc 5**~~ — ✅ XONG 31/07/2026: 5a → §9, 5b → §11.
5. **Việc 3** (secret + history) — **CHƯA LÀM, và user đã dặn hai lần là đừng
   đụng.** Chỉ làm khi chủ hệ thống nói rõ là bắt đầu: nó làm ngừng dịch vụ và
   viết lại git history.

**File này coi như đã đóng, trừ Việc 3.** Việc còn lại của dự án nằm ở
[V2_HANDOFF.md](V2_HANDOFF.md) §4.5 (task từ `action_items`, domain riêng, người
thứ hai enroll) và ba việc hạng 1 ở đầu file này (phiên âm GPU, transcript của
Lark, bỏ phụ thuộc sự kiện lịch) — cả ba đổi kiến trúc nên phải bàn trước.

**Hai thứ cần khởi động lại mới ăn mã mới:** tiến trình `run` (cho `bitable.py`
mới; xem §16, nhớ luật 6 khi kill) và Hermes gateway (cho schema tool MCP mới —
dữ liệu thì vẫn đúng ngay vì MCP đọc Base trực tiếp).

## Kỷ luật khi làm (dự án này đã trả giá để có)

- **Đo, đừng đoán.** Mỗi việc ở trên đều có mục "cách kiểm" — làm đúng nó rồi mới
  nói xong. Trong phiên 30–31/07 có **ba** kết luận sai vì suy từ một bằng chứng
  của chỗ khác (xem V2_HANDOFF §6 và V2_MAINTENANCE §10).
- **`lark-cli <cmd> --dry-run`** in ra đúng method + URL + body của Open API — cách
  lấy contract khi tài liệu không nói. ⚠️ Trong môi trường có Hermes, `lark-cli`
  đòi `config bind`; đừng tự bind, gọi API trực tiếp bằng `v2.lark_api`.
- **Gọi API với tham số CỐ Ý SAI** để phân biệt thiếu quyền (`99991672`/`99991679`,
  Lark đọc thẳng tên scope còn thiếu) với qua-được-cửa-quyền (`230001`, `234008`,
  `91402`, `99992351`). Không gây tác dụng phụ.
- **Nguồn sự thật về quyền:** `GET /open-apis/application/v6/scopes` bằng tenant
  token, đọc kèm `scope_type`. Chuỗi `scope` trên token thì KHÔNG tin được.
- Muốn một scope có tác dụng cần **ba** điều: Console duyệt đúng danh tính **+**
  tên có trong `OAUTH_SCOPES` **+** enroll lại.

---

## Câu mồi để giao cho phiên mới (copy dán nguyên)

```
Dự án V2 ở E:\meetingxlark. Đọc docs\V2_VIEC_CAN_LAM.md trước (đơn đặt việc,
có sẵn file:dòng + cách kiểm), rồi làm Việc 1 và Việc 2b.

Ba điều: (1) hệ thống ĐANG CHẠY THẬT — PAUSED=0, gửi tin cho người thật, nên
đừng test bằng cách gửi cho ai ngoài tôi; (2) đọc "Sáu luật không được phá"
trong file đó trước khi chạy lệnh nào; (3) mỗi việc có mục "cách kiểm" — làm
đúng nó rồi mới báo xong, đừng kết luận từ suy đoán.

Việc 3 (rotate secret + xoá git history) ĐỪNG làm, hỏi tôi trước.
```

Bản gọn (dùng khi phiên mới chạy trong đúng thư mục này, đã có MEMORY.md trỏ đường):

```
Đọc E:\meetingxlark\docs\V2_VIEC_CAN_LAM.md và làm theo thứ tự đề nghị ở cuối
file. Bỏ qua Việc 3. Hệ thống đang chạy thật, đừng gửi tin cho ai ngoài tôi.
```

Ba câu trong bản dài lần lượt chặn ba kiểu đi sai mà phiên 31/07 đã gặp thật:
gửi biên bản cho người thật lúc đang thử nghiệm, phá một luật ngầm (kill theo
chuỗi rộng / `.bat` non-ASCII / `run --ws`), và kết luận từ suy đoán thay vì đo.
