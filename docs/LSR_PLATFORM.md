# Đưa MeetingxLark lên LSR Agent Platform (external agent)

Trạng thái **20/08/2026: đã chạy.** `AG-MEETINGNOTE` đã đăng ký dạng external,
`V2_TELEMETRY=1`, orchestrator đã restart và nạp code mới (PID mới lúc 14:44,
quét xong một chu kỳ sạch). Trace thật xuất hiện khi có cuộc họp đầu tiên được
xử lý — chưa có job nào từ lúc restart nên bảng còn trống, đó là đúng.

Platform: `https://github.com/LamsonRetail/lsr-agent-platform` — nền tảng agent
nội bộ LamsonRetail (registry, telemetry, chấm điểm, golive).

---

## 1. Vì sao "external" là chỗ đúng

Platform chia agent làm hai loại: `managed` (họ host trên VM/Vercel của họ) và
`external` (team tự vận hành). MeetingxLark là loại thứ hai, và loại này được
thiết kế sẵn chứ không phải chỗ lách:

| Bằng chứng trong repo họ | Nói gì |
| --- | --- |
| `PLAN.md §10c(1)` | "External thì platform **không** đụng vào runtime" · "platform **không kill process được**" |
| `infra/lsr-platform/platform_api/app.py:361` | cột `runtime` trong bảng `agents`, mặc định `'external'` |
| `STATUS.md` B2 | kill switch cho agent external = collector trả 403 |
| `tests/test_adopt.py` | khoá cứng `mode: external` |

Cắt một agent external gồm đúng ba việc: thu hồi khoá telemetry, collector trả
403, đánh dấu `deactivated` trên dashboard. Cả ba đều không chạm `python -m v2`
trên máy này. Vế "cắt Lark" trong tài liệu của họ chỉ bám được vào app nào
platform đang giữ khoá — app của MeetingxLark thì không (xem `lsr-agent.yaml`,
mục `lark.bot.app`).

## 2. Vì sao KHÔNG chạy `lsr_adopt.py` của họ

Script đó gói ba việc: sinh manifest, gọi enroll, và **cài 4 hook vào
`.claude/settings.json`** (`PreToolUse`/`PostToolUse`/`UserPromptSubmit`/`Stop`).

Việc thứ ba là thứ phải tránh. Hook chỉ đo phiên Claude Code, **không** đo
`python -m v2` — tức là không đo được thứ ta muốn đo. Đổi lại, mỗi lời gọi tool
trong phiên làm việc sẽ hỏi Policy API của họ qua mạng. Repo này hiện chưa có
`.claude/settings.json` (chỉ có `settings.local.json` chứa permissions), nên
script sẽ tạo mới hoàn toàn.

Hai việc còn lại làm tay: manifest đã có ở `lsr-agent.yaml`, enroll là một lệnh
curl (§5).

## 3. Nối vào luồng — đã áp 20/08/2026

Chỗ nối: `v2/orchestrator.py`, **cuối thân vòng `for` trong `_process_queue`**,
sau bước glossary.

**Không** phải sau `_deliver_requested` như bản nháp đầu của tài liệu này. Lý do
nằm ở chính luồng: nó rẽ hai nhánh — `priority>=2` thì gửi transcript cho người
đã hỏi, còn lại thì để `held` chờ người hỏi — nên không tồn tại một "điểm đã gửi
xong" duy nhất. Chỉ có điểm ĐÃ XỬ LÝ XONG.

```python
        if not dry_run:
            telemetry.send(task_id=token[:12],
                           summary=f"priority={priority} processed",
                           tool_calls=[{"ok": True, "name": "whisper"}])
```

> **Bẫy đã suýt mắc — đọc trước khi sửa dòng này.** Bản nháp đầu viết
> `summary=f"delivered {len(sent)}/{len(failed)}"`. Trong `_process_queue`
> **không có** biến `sent` hay `failed` (`_deliver_requested` trả `None`). Đó là
> `NameError` **lúc chạy**, không phải lúc import — nghĩa là
> `python -c "import v2.orchestrator"` vẫn báo OK, hệ thống chạy ngon hàng giờ,
> rồi sập đúng lúc có cuộc họp thật, và vòng `:loop` của `run-v2-auto.bat` sẽ
> chết-đợi-120s-chết mãi. Chỉ dùng biến gán ở ĐẦU thân vòng (`token`,
> `priority`). Kiểm bằng AST, đừng tin mỗi `import`.

Bốn tính chất đã đo, không phải suy đoán:

- **Không chặn** — `send()` đẩy sang thread daemon. 5 lần gọi vào một IP hố đen
  tốn tổng 2.3 ms.
- **Không ném** — mọi đường đều nằm trong `except Exception`, kể cả lúc dựng
  payload. Collector chết, khoá sai, mạng đứt: V2 không biết và không quan tâm.
- **Tắt mặc định** — thiếu `V2_TELEMETRY=1` thì `send()` thoát ngay ở dòng đầu.
- **Công tắc ăn liền** — sửa `V2_TELEMETRY` trong `.env.lsr` là có tác dụng ở
  lượt kế tiếp, KHÔNG cần restart. Bản đầu không được vậy (nạp file một lần rồi
  khoá); đã đo ra và sửa cùng ngày — xem docstring `_load_env_lsr`.

Gỡ khẩn cấp: đặt `V2_TELEMETRY=0` trong `.env.lsr`. Hết. Không sửa code, không
git, không restart.

## 4. Trùng phạm vi — còn treo

Platform đã có hai agent khai "biên bản họp":

- `AG-MINH-ANH` — tự nhận là **agent demo của platform** (`agents/AG-MINH-ANH/USECASE.md`).
- `AG-DATA-SUPPORT` — team Data, có hẳn mục "Biên bản họp (Lark Meeting)" trong README.

MeetingxLark là hàng production thật, đang chạy.

**Chốt 20/08/2026 (owner):** cứ để cả ba cùng tồn tại, việc xoá bớt để **BOD
duyệt** sau. Đây là quyết định có ý thức, không phải bỏ sót — đừng tự ý gộp
hay xoá `AG-MINH-ANH` / phần biên bản của `AG-DATA-SUPPORT` khi dọn registry.

## 5. Các bước đăng ký — ĐÃ CHẠY XONG 20/08/2026 (giữ làm hồ sơ)

Owner **có quyền admin platform**, nên đường đi ngắn hơn tài liệu ONBOARDING của
họ — và có hai bẫy phải né, cả hai đọc ra từ `platform_api/app.py`:

> **Bẫy 1 — đừng gọi `/v1/agents/register` dù nó là endpoint "admin".**
> Nó `ON CONFLICT DO UPDATE`: gõ nhầm một id đã tồn tại là **đè cấu hình agent
> đó và xoay khoá telemetry của nó** (app.py:2372), không cảnh báo gì. Lại còn
> ghi cứng `status='registered'` nên cũng chẳng active được.
> `/v1/agents/enroll` thì **409 khi trùng id** (app.py:2456) và admin enroll là
> `active` luôn (app.py:2448). An toàn hơn *và* đúng ý hơn.
>
> **Bẫy 2 — enroll của admin đi vòng qua cổng checklist.**
> `/v1/agents/{id}/status` chặn `active` khi chưa đủ 28 mục golive (409, trừ khi
> `force`), nhưng enroll ghi thẳng `status='active'` ngay lúc INSERT. Owner kiêm
> admin = tự duyệt việc của mình, đúng thứ platform cố tách vai. Nên hạ về
> `testing` ngay sau khi enroll, chỉ lên `active` khi trace đã chạy ổn.

Không cần SSH vào VPS lấy `PLATFORM_ADMIN_TOKEN`: code ưu tiên danh tính người
dùng, và enroll bằng danh tính còn tự gắn người tạo làm moderator của agent.

1. ✅ Đã chốt: `agent.id` = `AG-MEETINGNOTE`, `agent.owner` = `thamnt@hapas.vn`,
   `agent.squad` = `PLATFORM`.
2. **Đăng nhập** (một lần, mở link duyệt trên console) — chạy trong repo platform:
   ```bash
   bash scripts/lsr-login.sh
   ```
3. **Xem registry** trước khi tạo, để chắc `AG-MEETINGNOTE` chưa ai dùng và để
   xem `AG-MINH-ANH` có traffic thật không (dữ liệu cho §4):
   ```bash
   curl -s -H "Authorization: Bearer $(cat ~/.lsr/token)" https://platform.34-126-154-135.sslip.io/v1/agents
   ```
4. **Đăng ký** — trả về khoá telemetry, **chỉ hiện một lần**:
   ```bash
   curl -s -X POST https://platform.34-126-154-135.sslip.io/v1/agents/enroll \
     -H "Authorization: Bearer $(cat ~/.lsr/token)" \
     -H "Content-Type: application/json" \
     -d '{"agent_id":"AG-MEETINGNOTE","name":"MeetingxLark","owner":"thamnt@hapas.vn","squad":"PLATFORM","connect_mode":"bot","deployment":"external","repo_url":"https://github.com/tientham2005/MeetingxLark.git","host_note":"May Windows tai cho, khong VPS","skills":["meetings","v2-enroll-gate","whisper"]}'
   ```
5. **Hạ về `testing`** cho tới khi trace thông (xem Bẫy 2):
   ```bash
   curl -s -X POST https://platform.34-126-154-135.sslip.io/v1/agents/AG-MEETINGNOTE/status \
     -H "Authorization: Bearer $(cat ~/.lsr/token)" \
     -H "Content-Type: application/json" -d '{"status":"testing"}'
   ```
6. **Ghi `.env.lsr`** ở gốc repo (`.gitignore` đã nuốt `.env*`):
   ```
   LSR_AGENT_ID=...
   LSR_TELEMETRY_API_KEY=...
   LSR_COLLECTOR=https://collector.34-126-154-135.sslip.io
   V2_TELEMETRY=0
   ```
7. **Smoke test** — thử đường ống, không đụng cuộc họp nào:
   ```bash
   python -m v2.telemetry smoke
   ```
   `ok:true` là thông · 401 là sai khoá · 403 là agent đang deactivated.
8. **Áp một dòng §3**, bật `V2_TELEMETRY=1`, xem dashboard
   `https://app.34-126-154-135.sslip.io` có số liệu.
9. **Golive** (chỉ khi cần chạy chính thức): `golive.json` 28 mục →
   `submit-golive.sh` → admin duyệt. **Hỏi maintainer trước** — tài liệu của họ
   nói lúc duyệt platform sẽ "mở kênh Lark + sync bot vào nhóm"; phải xác nhận
   bước đó không đụng app Lark riêng của MeetingxLark.

## 6. Caddy chỉ mở một danh sách hẹp — biết trước kẻo mất thì giờ

Từ máy này, phần lớn Platform API **không gọi được**, kể cả khi bạn là admin và
cầm token cá nhân hợp lệ. Reverse proxy chặn trước khi tới ứng dụng: trả
`403 forbidden`, `Server: Caddy`, thân là text chứ không phải JSON — nhìn giống
lỗi phân quyền nhưng không phải (`infra/lsr-platform/caddy/Caddyfile`).

Mở (không cần gì thêm ngoài token cá nhân):

- `POST /v1/agents/enroll` · `GET /v1/auth/me` · `/v1/auth/device/*` · `/v1/auth/tokens*`
- `GET|POST /v1/agents/{id}/golive-checklist|spec|profile`
- `/v1/self*` · `/v1/chat/*` · `/v1/lark/*` · `/bootstrap/*`
- **`POST /v1/traces`** trên host collector — đường telemetry, thứ quan trọng nhất

Chặn (phải có header `X-Gateway-Token`, secret nằm trên VM):

- `GET /v1/agents` — không liệt kê registry từ đây được
- `POST /v1/agents/{id}/status` — **không đổi trạng thái từ đây được**
- `POST /v1/agents/register` — tức là ví dụ trong `CONTRIBUTING.md` của họ cũng
  không chạy được từ ngoài; enroll là đường duy nhất

Mẹo kiểm một agent có tồn tại không mà không cần gateway token: gọi
`GET /v1/agents/{id}/spec` — 404 nghĩa là "agent không tồn tại" (endpoint truy
vấn thẳng bảng `agents`), 200 là đã có người dùng id đó.

## 7. Tên hiển thị — sửa được ở đâu, không sửa được ở đâu

Owner chốt 20/08/2026: tên hiển thị là **`Mino Lê - Meeting Note Assistant`**,
thay cho `MeetingxLark`. Nhưng nó nằm ở ba chỗ khác nhau, và chỉ một chỗ sửa
được từ máy này:

| Chỗ | Giá trị hiện tại | Sửa được? |
| --- | --- | --- |
| `lsr-agent.yaml` → `agent.name` | ✅ đã đổi | tại chỗ |
| Platform, cột `agents.name` (hiện trên console/Chi phí) | vẫn `MeetingxLark` | **KHÔNG** |
| Lark, tên bot người dùng thấy (`V2_BOT_NAME` trong `v2/.env`) | `Agent Meeting của Chuyển đổi số` | được, nhưng đổi hành vi thật |

**Vì sao không sửa được `agents.name`:** rà cả 7 chỗ `UPDATE agents SET` trong
`platform_api/app.py` thì không chỗ nào đụng cột `name`. Nó chỉ được ghi lúc
INSERT, tức chỉ qua `/v1/agents/register` (`ON CONFLICT DO UPDATE`) hoặc
`/v1/agents/enroll` (insert mới). Cả hai đường đều tắc:

- `/register` bị Caddy chặn (cần `X-Gateway-Token` nằm trên VM), **và** nó xoay
  luôn `telemetry_key_hash` — chạy được cũng làm hỏng khoá trong `.env.lsr`.
- `/enroll` trả 409 vì id đã tồn tại.

Ba đường còn lại, phải vào VM `34.126.154.135`:

1. `UPDATE agents SET name=... WHERE agent_id='AG-MEETINGNOTE'` bằng psql — gọn
   nhất, không xoay khoá, không đụng gì khác.
2. Gọi `/register` từ trong VM (có sẵn cả hai token) rồi **chép khoá mới vào
   `.env.lsr`** — nhớ bước chép, quên là telemetry chết câm.
3. Bỏ qua: để `MeetingxLark` trên registry, tên đẹp chỉ nằm ở manifest.

## 8. Hai chỗ cố ý bỏ trống

- ~~**Số token.**~~ Đã làm 20/08: `summarize._post` gom `usage` của Hermes vào
  một rổ **theo luồng** (`threading.local`), `_process_queue` dọn rổ ở đầu thân
  vòng và lấy ở cuối, đẩy vào `llm_calls`. Rổ theo luồng chứ không dùng chung vì
  `_notify_minute -> recap_from_text` chạy được từ thread của `ws_listener` song
  song với vòng chính — rổ chung sẽ gán token cuộc này sang cuộc kia.

  Đã đo: một lời gọi Hermes thật cho `{"input_tokens": 709, "output_tokens": 5}`
  (Hermes nhồi sẵn ~700 token system prompt vào MỌI lời gọi, nên đừng ngạc nhiên
  khi cuộc họp ngắn vẫn tốn nhiều), và collector trả đúng `total_tokens: 2004`
  cho trace gửi 2004 token.

  **Còn thiếu có hệ thống:** `_backfill_recaps()` chạy SAU vòng lặp và cũng gọi
  LLM. Token của nó nằm lại trong rổ tới đầu vòng sau rồi bị dọn — không tính
  vào cuộc nào. Con số trên dashboard vì vậy là cận DƯỚI. Đừng dùng nó để đặt
  hạn mức cứng mà không cộng biên.
- **Nội dung biên bản.** `V2_TELEMETRY_CONTENT` mặc định 0. Luật ACL đã chốt là
  "được mời + đã enroll mới nhận biên bản"; đẩy nội dung sang collector là một
  đường đi nằm ngoài luật đó. Collector của họ có che PII, nhưng che PII không
  phải là che nội dung họp. Muốn bật thì phải là quyết định có ý thức của owner.
