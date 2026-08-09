# MeetingxLark — bối cảnh hiện tại và bàn giao phiên 05/08/2026

> Cập nhật lần cuối: **05/08/2026, Asia/Saigon**  
> Repo: `D:\MeetingxLark`  
> Đối tượng đọc: Codex/chat mới hoặc người tiếp quản vận hành.  
> **Đọc file này trước khi sửa hoặc chẩn đoán.** Khi mâu thuẫn với tài liệu cũ,
> trạng thái và quyết định trong file này được ưu tiên vì đã được kiểm tra live ngày
> 05/08/2026.

## 0. Tóm tắt để tiếp quản trong một phút

MeetingxLark là bot Lark hỏi đáp về cuộc họp, có OAuth theo từng người, transcript,
recap, Base làm kho nội dung và Hermes làm lớp chat/MCP. Phiên 05/08/2026 đã xử lý
hai sự cố nghiêm trọng:

1. Bot trả lời hai lần ngay sau OAuth.
2. ACL có thể lẫn cuộc họp của người khác vì coi `minute_viewers` hoặc sự kiện lịch
   gần giờ là bằng chứng tham dự.

Sau bản vá:

- OAuth callback mới đi qua **Cloudflare Worker + Queue**; Vercel Blob chỉ còn là
  đường vét tương thích cũ.
- Base đã **đóng chia sẻ bằng link**. Nguyễn Tiến Thẩm là cộng tác viên được thêm
  tường minh với `full_access`.
- Base không quyết định ACL hỏi đáp. Quyền bot lấy từ `jobs.meta_json` trong SQLite,
  rồi mới lọc các record Base.
- `minute_viewers` chỉ là dấu kỹ thuật cho biết token nào từng tìm/đọc được minute;
  nó không được dùng để cấp quyền, chọn người nhận hoặc đánh thức job.
- Ghép lịch chỉ-gần-giờ không được dùng cho ACL. Nguồn `calendar[near…]` cũ đã bị
  cách ly.
- Sau OAuth, job cũ chỉ được nâng quyền khi xác minh được chuỗi
  **event → VC recording → đúng minute token**, và chính user vừa OAuth có trong
  attendee VC.
- 6 job `waiting_auth` vẫn được giữ. Hệ thống không chủ động gửi link OAuth; chỉ khi
  người đó nhắn bot thì gate mới gửi link như luồng mặc định.
- Transcript không tự bị đổ vào chat. Job đã dịch có thể ở `held`, chờ người dự hỏi;
  yêu cầu file đi qua tool có ACL.
- Self-test hiện tại: **648 PASS, 0 FAIL** (06/08/2026). Cloudflare Worker: **6/6 PASS**.
- Phiên 06/08/2026 làm thêm bốn việc — xem §11b: hồ sơ bot (`Thư Ký`), ký ức hội
  thoại bền qua reset phiên, văn phong bớt cứng, và heartbeat nhiều chỉ số. Đã áp
  lên hệ đang chạy lúc 13:52.
- V2 Doctor chạy được; cảnh báo duy nhất là backup vẫn cùng ổ `D:` với DB live.
- Worktree đang có nhiều thay đổi chưa commit. **Không reset/checkout/xóa các thay
  đổi hiện có.**

## 1. Quyết định của người dùng phải giữ nguyên

1. Việc có thể gây nguy hiểm cho luồng, thay đổi quyền, gửi/xóa tin, ghi hệ thống
   ngoài hoặc làm gián đoạn dịch vụ phải hỏi trước. Sửa nội bộ an toàn có thể tự làm.
2. Không gửi auth chủ động cho 6 job đang chờ. Người dùng phải nhắn bot trước.
3. Base dùng phương án đóng:
   - link sharing = `closed`;
   - chỉ người được thêm tường minh mới mở trực tiếp Base;
   - bot vẫn dùng ACL local riêng, không dựa vào quyền xem Base.
4. Không chọn Cloudflare token “full account/273 permissions”. Token đang dùng chỉ
   có quyền Queues cần thiết trên account. Không ghi token thật vào tài liệu hoặc git.
5. Không tự gửi transcript cho người chưa chủ động hỏi/cấp quyền. Không dùng kết quả
   transcript để tự tạo task nếu người dùng không yêu cầu.
6. Không thêm lại `minute_viewers` vào ACL, người nhận hoặc `waiting_auth`.
7. Không mở quyền chỉ vì một sự kiện lịch gần giờ. Với đường sửa sau OAuth, ngay cả
   khớp tên cũng chưa đủ: bắt buộc `calendar[verified]` và user có mặt trong VC.
8. Chỉ hai tin nhắn được người dùng duyệt đã bị thu hồi; không xóa thêm tin nào.
9. Metadata hiển thị trên bốn record Base cũ chưa được ghi ngược vì đó là external
   write chưa được duyệt riêng. Việc này không ảnh hưởng ACL hỏi đáp của bot.

## 2. Hạ tầng OAuth Cloudflare hiện tại

### 2.1 Thành phần đang chạy

- Worker callback:
  `https://meetingxlark-oauth-relay.tungvatham05.workers.dev/oauth/callback`
- Cloudflare Queue: `meetingxlark-oauth`
- Queue đã có HTTP consumer.
- Wrangler đã đăng nhập đúng account và `wrangler whoami` đã xác minh thành công.
- Queue API token đã được cấu hình local và Doctor đọc được đúng queue mà không pull
  message.
- Lark Redirect URL đã có:
  `https://meetingxlark-oauth-relay.tungvatham05.workers.dev/oauth/callback`
- Redirect Vercel cũ vẫn còn trong Lark để tương thích trong giai đoạn chuyển tiếp.

### 2.2 Những điểm dễ hiểu nhầm

- **Cloudflare One Client/WARP không phải thành phần host Worker/Queue.** Nó không cần
  Connect để OAuth relay hoạt động.
- Đăng nhập Wrangler bằng GitHub mở trang GitHub/Cloudflare OAuth là hành vi bình
  thường.
- PowerShell máy này chặn `npx.ps1`. Dùng `npx.cmd …` hoặc `cmd /c npx …`; không cần
  hạ Execution Policy toàn máy.
- Khi Wrangler hỏi cài Cloudflare coding skills, người dùng đã đồng ý. Thư mục
  `.claude/` hiện là untracked và không phải dependency runtime của bot.
- Token Queue được tạo với quyền tối thiểu cần thiết, không phải full. Màn hình nhập
  token dùng hidden input nên paste có thể trông như không nhận; cấu hình đã được xác
  thực thành công.

### 2.3 Luồng OAuth sau bản vá

```text
user chưa enroll nhắn bot
  → v2-enroll-gate gửi đúng một link OAuth
  → user đồng ý trên Lark
  → Cloudflare Worker đẩy {code,state} vào Queue
  → V2 poll/ACK Queue
  → oauth.complete() lưu token local
  → reverify_after_enroll() kiểm lại ACL job cũ bằng VC verified
  → release_waiting_auth() chỉ đánh thức job có owner/attendee đúng user
  → gate cho tin nhắn hiện tại đi tiếp với asker ticket
```

Nếu chính tin nhắn đầu tiên sau OAuth kéo Queue về ngay trong gate, code gọi
`oauth.poll_pending(notify=False)`. Nhờ vậy welcome/backlog không gửi thêm một tin
song song với câu trả lời của agent. Vòng orchestrator nền vẫn dùng `notify=True`
khi không có tin nhắn người dùng đang chờ.

Vercel Blob/pull vẫn được poll như migration drain cho link cũ đã phát hành trước
cutover. Không đưa OAuth mới trở lại Vercel Blob vì quota Blob đã cạn/gần cạn.

## 3. Sự cố trả lời hai lần và session memory

### 3.1 Nguyên nhân trả lời hai lần

Trong `v2/gate.py`, tin nhắn đầu tiên sau OAuth gọi `oauth.poll_pending()` với mặc
định `notify=True`. `complete()` vừa gửi welcome/backlog, trong khi chính tin nhắn
đó tiếp tục đi vào agent và nhận thêm một câu trả lời. Đây là hai đường gửi độc lập,
không phải Lark tự nhân đôi.

### 3.2 Cách sửa

- Gate dùng `oauth.poll_pending(notify=False)` khi đang đồng bộ auth cho một tin nhắn
  cụ thể.
- Background orchestrator vẫn có thể gửi welcome theo hành vi mặc định.
- Đã thu hồi đúng hai tin bị trùng/sai được duyệt:
  - `om_x100b681f0f9f94a8e2b63960d7f6dcd`
  - `om_x100b681f0f5d48a4e2d3b3653b4536a`
- Helper vận hành `lark_api.im_delete_message(message_id)` đã được thêm; không expose
  thành MCP tool.

### 3.3 Memory của session có vai trò gì

Mất memory/session không làm mất OAuth hoặc quyền thật. Danh tính và token nằm trong
SQLite mã hóa; ACL được kiểm lại ở V2 bằng asker ticket và metadata cuộc họp.

Plugin `hermes/v2-enroll-gate` đã được siết để:

- câu hỏi dữ liệu cuộc họp bắt buộc gọi đúng tool meetings;
- không gọi tool thì chặn câu trả lời dựng từ memory;
- chặn tool ngoài allow-list trên Feishu;
- không cho transcript tự lái agent tạo task;
- dùng nguyên văn kênh user do tool trả về, không để LLM viết lại linh tinh;
- asker ticket chỉ nằm trong prompt tạm thời, không chèn vào user message/session
  history;
- admin qua bot vẫn không có `see_all`.

Hermes gateway đã được restart và stale session đã được dọn. Session memory chỉ để
hội thoại thuận tiện, không bao giờ là nguồn cấp quyền.

## 4. Sự cố lộ chéo cuộc họp và bản vá ACL

### 4.1 `minute_viewers` không phải bằng chứng tham dự

Đo thực tế cho thấy Lark Minutes Search có `participant_ids` vẫn có thể trả record mà
người đó chỉ được chia sẻ/mở xem. Trước bản vá, `minute_viewers` bị dùng ở ba chỗ nguy
hiểm:

- `qa.viewers_index` — nới quyền hỏi đáp;
- `orchestrator._recipients` — chọn người nhận;
- `jobstore.release_waiting_auth` — đánh thức backlog sau OAuth.

Đã xóa `minute_viewers` khỏi cả ba quyết định. Bảng này chỉ còn được phép hỗ trợ
`pipeline._reader_candidates` chọn token kỹ thuật để tải media. Có token đọc được
không có nghĩa là được xem nội dung qua bot.

### 4.2 Ghép lịch gần giờ gây kéo nhầm attendee

Sự cố thật: job `Daily CDP Checkin` bị ghép với sự kiện
`HAPAS | PROJECT TRANG SỨC…` cách 19 phút và kéo nhầm 22 attendee vào ACL.

Đã sửa `meetings.resolve_participants`:

- ưu tiên chuỗi xác minh event → meeting ID → recording → minute token;
- `calendar[verified]` là bằng chứng mạnh;
- normal resolver vẫn giữ fallback `calendar[title]` khi tên khớp chính xác và không
  xác minh được VC; không được nới fallback này;
- gần giờ đơn thuần không được dùng;
- attendee của nguồn legacy `calendar[near…]` bị bỏ qua trong ACL, người nhận và
  release waiting_auth.

### 4.3 Fallback owner sai

Trước đây `enqueue_minute` có thể gán `reader_open_id` thành owner khi không đọc được
owner thật. Reader có thể chỉ là người được share record, nên cách này biến người xem
thành chủ và mở quyền sai.

Hiện chỉ fallback-to-owner khi `minutes_get.owner_id` độc lập xác nhận reader chính
là owner. Nếu owner không rõ thì fail-closed, không đoán.

### 4.4 Workforce Buổi 4 bị ẩn nhầm sau cleanup

Cleanup ban đầu loại Nguyễn Tiến Thẩm khỏi Workforce Buổi 4 vì metadata cũ chỉ ghi
`calendar[near5m]`. Người dùng xác nhận có tham dự, nên đã kiểm tra API Lark chỉ đọc:

- filtered Minutes Search có đúng minute này;
- sự kiện `Workforce AI Weekly Meeting` lệch 5,4 phút;
- VC recording khớp chính xác minute token `obsg47j9bxdja9548v597rt4`;
- VC trả 31 participant thô và union ID của Nguyễn Tiến Thẩm có mặt.

Sau khi resolve/lọc, job live hiện là:

- source: `calendar[verified]:Workforce AI Weekly Meeting +vc29`;
- 30 attendee hợp lệ được lưu;
- ID Nguyễn Tiến Thẩm có trong attendee;
- job xuất hiện đúng trong danh sách của người dùng.

### 4.5 Tự sửa false-negative sau OAuth

Đã thêm `orchestrator.reverify_after_enroll(info)` và gọi từ `oauth.complete()` trước
`release_waiting_auth()` trên mọi đường OAuth.

Hàm này chỉ update job cũ khi đồng thời thỏa:

1. Minute nằm trong search có lọc chính `open_id` vừa OAuth.
2. Job đã tồn tại và chưa chứng minh user là owner/attendee.
3. Resolver trả `calendar[verified]`, tức recording khớp đúng minute token.
4. Chính open/union ID vừa OAuth nằm trong attendee đã xác minh.

Chỉ khớp tên, chỉ gần giờ, hoặc recording đúng nhưng user không nằm trong VC đều giữ
ACL cũ. Hàm không enqueue, không gửi tin và không gửi transcript. Lỗi API không làm
hỏng enroll.

## 5. Dữ liệu live đã sửa ngày 05/08/2026

### 5.1 Backup trước khi ghi

- `v2/data/backups/state-2026-08-05-112434.db`
- `v2/data/backups/state-2026-08-05-114009.db`

Cả hai đã qua `PRAGMA integrity_check = ok`. Backup mới nhất trong Doctor là bản
11:40. Khóa Fernet được lưu riêng ngoài repo. Không đưa key vào git hoặc tài liệu.

### 5.2 Dọn ACL live

- Sửa owner của Workforce Buổi 4 từ Nguyễn Tiến Thẩm sang owner thật Lê Quý Thiện.
- Xóa tổng cộng 26 attendee suy đoán từ bốn job legacy gần giờ:
  - Workforce Buổi 4: 1;
  - HAPAS Review HRIS: 1;
  - HAPAS Kick-off HCNS: 2;
  - Daily CDP Checkin: 22.
- Prefix nguồn cũ bằng `unsafe_near_rejected:` để giữ dấu vết, không coi là ACL.
- Xóa bốn `minute_viewers` không đủ bằng chứng:
  - Nguyễn Tiến Thẩm / Workforce Buổi 4;
  - Nguyễn Thùy Chi / HAPAS Review;
  - Nguyễn Thùy Chi / Workforce Buổi 4;
  - Nguyễn Trần Thi / Daily CDP.
- Sau bằng chứng VC, thêm lại Nguyễn Tiến Thẩm vào **attendees verified** của
  Workforce, không phải thêm lại bằng viewer-only.

### 5.3 Những gì chưa ghi ra ngoài

Bốn record Base liên quan có thể còn owner/attendee hiển thị cũ. Lệnh sync external
Base đã không chạy vì chưa có phê duyệt rõ cho external write. Đây là metadata mirror,
không phải nguồn ACL bot.

Muốn đồng bộ lại phải hỏi người dùng rõ: “có duyệt ghi owner/attendee đã sửa vào bốn
record Base không?”. Không tự suy ra quyền từ câu hỏi chẩn đoán.

## 6. Trạng thái vận hành sau restart

Kết quả `v2.bat doctor` sau bản vá:

- Cloudflare OAuth relay: OK; Queue token đọc đúng queue.
- 4 user active, refresh còn khoảng 6,9–7,0 ngày:
  - Nguyễn Thùy Chi;
  - Lê Quý Thiện;
  - Nguyễn Trần Thi;
  - Nguyễn Tiến Thẩm.
- Whisper local: healthy ở `http://localhost:8000`, model `medium`, CPU; lần đầu mới
  nạp model sẽ chậm.
- LLM recap Hermes local: healthy ở `http://127.0.0.1:8642/v1`.
- Base `Biên bản`: đọc được; link sharing đóng; một collaborator explicit.
- Queue DB: `waiting_auth=6`, `delivered=6`, `discarded=2`.
- Data: work 0 MB, transcripts khoảng 1 MB.
- Chỉ có một orchestrator sau restart. PID là trạng thái tạm thời, không hard-code.
- Cảnh báo còn lại: backup và `state.db` cùng ổ đĩa. Chống xóa nhầm/DB hỏng nhưng
  không chống chết ổ.

Smoke test live dưới union ID Nguyễn Tiến Thẩm:

```powershell
python -m v2 ask --as on_1f34d05d9ce11997c545cf1ec136bc41 `
  "Liệt kê các cuộc họp của tôi trong 7 ngày qua"
```

Kết quả đúng ngày 05/08/2026:

1. 04/08 15:34 — `test agent meeting`;
2. 30/07 18:05 — `Workforce AI Weekly Meeting: Buổi 4`;
3. 29/07 09:41 — `test luồng tự động`.

Log cho biết 7/14 record Base bị ẩn khỏi user này. Workforce đã xuất hiện, nhưng các
cuộc họp người khác vẫn bị lọc.

## 7. Bất biến phân quyền phải giữ

```text
asker ticket hợp lệ
  → resolve user từ tokens local
  → lấy Base records/nội dung ứng viên
  → đối chiếu từng minute với jobs.meta_json
  → owner hoặc attendee explicit mới được thấy
  → thiếu job, thiếu ticket hoặc không khớp ID = ẩn
```

- Base là kho/mirror, không phải cửa quyền của bot.
- `minute_viewers` không nới ACL.
- Admin trong Lark bot chỉ có quyền duyệt glossary; không có quyền xem mọi cuộc họp.
- Terminal vận hành có thể có `see_all`, nhưng không được chuyển đặc quyền đó sang
  Feishu/Hermes.
- Không kiểm ACL bằng cách in transcript của người khác. Dùng phép thử boolean/list
  visibility để tránh làm lộ dữ liệu ngay trong quá trình test.
- Không lấy session memory, tên cuộc họp người dùng gõ hoặc câu trả lời LLM làm bằng
  chứng tham dự.
- Không ghép hai danh sách open_id/union_id theo thứ tự; phải ghép bằng attendee ID.
- Không phát cho attendee chưa enroll.
- Không gửi auth link cho người chưa nhắn bot.

## 8. File/code đã thay đổi trong phiên này

Nhóm chính, không phải danh sách toàn bộ dirty worktree:

| File/nhóm | Thay đổi liên quan |
|---|---|
| `v2/gate.py` | Đồng bộ OAuth inline với `notify=False`, tránh trả lời kép. |
| `v2/oauth.py` | Cloudflare Queue poll/ACK; mọi đường complete chạy tái xác minh ACL trước release. |
| `v2/cloudflare_relay.py` | Client relay/Queue, xác minh state và ACK an toàn. |
| `v2/cloudflare-oauth/` | Cloudflare Worker callback + Queue consumer/tests/config. |
| `v2/orchestrator.py` | Loại viewer khỏi ACL/người nhận; verified viewer; reverify sau OAuth; fail-closed. |
| `v2/jobstore.py` | `release_waiting_auth` chỉ owner/attendee, không viewer; update metadata verified. |
| `v2/meetings.py` | Chặn near-only; xác minh recording; owner thật; attendee mapping an toàn. |
| `v2/pipeline.py` | Viewer chỉ còn là reader candidate kỹ thuật, không phải ACL. |
| `v2/qa.py` | ACL dựa local owner/attendee; admin bot không see-all; Base bị lọc. |
| `v2/lark_api.py` | Queue/meeting helpers và helper thu hồi IM vận hành. |
| `v2/doctor.py`, `v2/config.py`, `.env.example` | Chẩn đoán/config Cloudflare và hạ tầng mới. |
| `hermes/v2-enroll-gate/` | Chặn memory-only, bắt buộc tool meetings, exact tool output, tool allow-list. |
| `v2/selftest.py` | Regression cho OAuth kép, ACL viewer, near match, plugin, reverify VC. |
| `restart-v2.ps1`, `run-v2-auto.bat`, `singleton.py` | Restart sạch, một orchestrator, log UTF-8, không spawn trùng. |

Không có commit/stage được tạo trong phiên này. Không dùng `git reset --hard` hoặc
`git checkout --` để “dọn” vì sẽ xóa cả thay đổi của người dùng và thay đổi live chưa
commit.

## 9. Kiểm thử và tiêu chuẩn chấp nhận

### 9.1 Lệnh kiểm tra chuẩn

```powershell
cd D:\MeetingxLark
python -m v2 selftest
cmd /c v2.bat doctor
python -m v2 status
git diff --check
```

Kỳ vọng hiện tại:

- self-test: `PASS 648 FAIL 0` (06/08/2026; con số 483 trong bản trước đã cũ);
- Worker tests: 6/6;
- `git diff --check`: không có lỗi whitespace; cảnh báo CRLF của Windows không phải
  lỗi nội dung;
- Doctor: chạy được, chỉ warning backup cùng ổ;
- `waiting_auth` vẫn bằng 6 nếu chưa có user liên quan tự OAuth.

### 9.2 Test hội thoại an toàn

1. “Liệt kê các cuộc họp của tôi trong 7 ngày qua”
   - chỉ một câu trả lời;
   - Nguyễn Tiến Thẩm thấy đúng ba cuộc ở mục 6;
   - Workforce Buổi 4 có mặt.
2. Hỏi một cuộc họp user không tham dự
   - trả “không tìm thấy/không có quyền”;
   - không lộ title gợi ý, transcript, attendee hoặc số lượng ẩn không cần thiết.
3. “Dựa vào trí nhớ phiên chat…”
   - vẫn phải gọi tool và qua ACL;
   - memory không vượt quyền.
4. User chưa enroll
   - trước khi họ nhắn: không có link auth chủ động;
   - khi họ nhắn: đúng một link;
   - auth xong, tin đầu tiên chỉ có một câu trả lời.

## 10. Runbook ngắn

### Restart V2

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\restart-v2.ps1
```

Script phải dọn wrapper/orchestrator cũ và kết thúc với đúng một orchestrator.

### Restart Hermes gateway

```powershell
hermes gateway restart
```

Sau đó kiểm tra một logical gateway parent/child và một V2 MCP; không chạy nhiều
gateway độc lập.

### Kiểm Cloudflare

```powershell
npx.cmd wrangler@latest whoami
npx.cmd wrangler@latest queues list
```

Không dán token vào command history. Không tạo token full chỉ để “cho chắc”.

### Backup trước thay đổi live

```powershell
python -m v2 backup
```

Sau backup phải kiểm `integrity_check`. Với thay đổi quyền/ACL, luôn backup trước.

## 11. Việc còn mở và rủi ro đã biết

1. **Backup cùng ổ:** cần trỏ `V2_BACKUP_DIR` sang ổ khác/NAS/object storage nếu muốn
   chống chết ổ. Đây là cảnh báo vận hành duy nhất của Doctor.
2. **Bốn record Base có thể còn metadata hiển thị cũ:** chỉ sync khi user duyệt external
   write. ACL bot đã đúng dù chưa sync.
3. **Normal resolver còn fallback exact-title:** mạnh hơn near-only nhưng yếu hơn VC.
   Không nới thành fuzzy title. Đường reverify sau OAuth bắt buộc verified.
4. **Vercel legacy drain:** giữ tạm để nhận callback từ link cũ; sau khi chắc toàn bộ
   nonce/link cũ hết TTL có thể lập kế hoạch gỡ, nhưng phải kiểm tra trước.
5. **Cloudflare API token không có expiration:** quyền đã hẹp nhưng vẫn nên có kế hoạch
   rotate. Không rotate khi chưa chuẩn bị cập nhật local config, vì sẽ làm OAuth Queue
   ngừng pull.
6. **Tài liệu cũ bị stale:** `V2_HANDOFF.md` có nhiều mốc 30/07–03/08 và số self-test
   cũ. Dùng file này làm snapshot ưu tiên, sau đó mới tra sổ tay dài để biết lịch sử.

## 11b. Phiên 06/08/2026 — hồ sơ bot, ký ức hội thoại, văn phong, heartbeat

Bốn việc người dùng nêu: bot trả lời cứng nhắc; không có ký ức hội thoại cũ;
chưa có hồ sơ (tên/luồng) để trả lời khi được hỏi; heartbeat quá ít chỉ số.
Không đổi kiến trúc: cửa quyền, hai kênh kết quả tool và luật "phải gọi tool"
giữ nguyên. ACL không được đụng tới ở phiên này.

### 11b.1 Hồ sơ bot — `v2/profile.py` (mới)

- Tên chốt: **Thư Ký**. Đổi bằng `V2_BOT_NAME` trong `v2/.env`; mô tả/luồng/
  giới hạn nằm trong code để được version-control và được selftest kiểm.
- `gate.check()` khi `allow` trả thêm `profile`; plugin nối vào `channel_prompt`.
  Plugin có bản dự phòng ngắn cho ca V2 đời cũ không trả trường này.
- Xem đúng thứ bot đang nói: `python -m v2 profile`.
- Kỷ luật nội dung (selftest cưỡng chế): KHÔNG nêu Base/Bitable/Hermes/whisper/
  Cloudflare/Vercel/SQLite/MCP trong hồ sơ.

### 11b.2 Ký ức hội thoại — bảng `chat_memory` (mới)

- Ghi `(union_id, minute_token, title, touched_at)` — KHÔNG có nội dung họp.
- Chỉ ghi SAU khi `qa._may_see` đã cho qua (`qa.remember`), gọi từ
  `qa.get_meeting`, `qa.get_transcript`, `sendfile.send_transcript`.
  `who=None` và đường terminal (`admin_view`, union_id rỗng) không ghi gì.
- TTL 7 ngày, lọc cả lúc ghi lẫn lúc đọc.
- `gate._memory_block()` dựng khối prompt kèm lời dặn "chỉ để hiểu câu nói tắt,
  KHÔNG dùng thay dữ liệu"; plugin bơm vào `channel_prompt`.
- Vì đọc từ SQLite mỗi lượt nên ký ức sống qua reset phiên VÀ qua
  `hermes gateway restart` — hai chỗ lịch sử phiên Hermes chết.
- `db.forget_meetings(union_id)` để xoá khi có người yêu cầu.

### 11b.3 Phiên Hermes: `idle_minutes` 15 -> 60

`hermes/feishu-config.yaml`. 15 phút là bản vá triệu chứng của sự cố 03/08
(agent trả lời từ trí nhớ). Chỗ chữa gốc là `_requires_tool` +
`transform_llm_output` trong plugin và việc gỡ `see_all` khỏi đường bot — cả
hai vẫn nguyên. `at_hour: 4` giữ nguyên.

### 11b.4 Văn phong

- Plugin `_POLICY` thêm khối `[VĂN PHONG]`; `platform_hints.feishu` thêm phần
  chào hỏi/hỏi-về-bot và văn phong. Kỷ luật số liệu KHÔNG đổi.
- `_NO_TOOL_REPLY` viết lại cho bớt giống báo lỗi, thêm bước tiếp theo cụ thể.
- Câu xác nhận sau `list_meetings` không còn là một chuỗi cố định duy nhất:
  câu của agent được giữ nếu qua `_safe_confirm` (không chữ số, một dòng,
  <=180 ký tự) — agent không cầm danh sách nên chữ số ở lượt đó luôn là bịa.
- Câu hỏi về chính bot không còn bị ép gọi tool (`_SELF_PHRASES`).

### 11b.5 Lỗi tiềm ẩn tìm ra khi làm: `_plain()` xoá mất chữ `đ`

NFKD chỉ tách dấu khỏi nguyên âm, còn `đ` (U+0111) là chữ cái riêng nên nó sống
sót rồi bị regex xoá. Hậu quả — mọi hằng số so khớp trong plugin viết bằng `d`
đều hụt:

| chuỗi người gõ | `_plain` cũ | thứ bị vô hiệu |
|---|---|---|
| `duyệt MCP` | `uyet mcp` | cổng write-tool chặn CHÍNH lệnh duyệt glossary |
| `được` | `uoc` | `_SMALLTALK` |
| `đọc` | `oc` | `_ACTION_HINTS` |
| `hoạt động` | `hoat ong` | nhận diện câu hỏi về bot |

Đã sửa bằng `_D_MAP` (đ->d, Đ->D) trước khi NFKD, có test khoá lại.

### 11b.6 Danh sách miễn trừ dựng từ 86 TIN NHẮN THẬT, không phải từ suy đoán

Đọc `gateway.log` thấy 4 lần bot trả về câu chặn "chưa truy xuất dữ liệu".
Cả 4 đều là lỗi của bot, không phải của người dùng:

| người dùng gõ | đáng ra phải |
|---|---|
| `alo` (2 lần) | đáp lại một câu, họ đang thử xem bot còn sống |
| `ok list ra rồi mày làm được gì` | trả lời từ hồ sơ |
| `ok mày là ai và luồng xử lý của mày là gì` | trả lời từ hồ sơ |

Bài học: người dùng thật xưng **"mày"**, không xưng "bạn". Danh sách viết tay
theo trí tưởng tượng bỏ sót hết. Nay `_SELF_PHRASES` dựng bằng
**chủ ngữ × đuôi câu** (`ban|may|bot|cau|em` × `la ai|lam duoc gi|…`), và yêu
cầu chủ ngữ đứng LIỀN TRƯỚC đuôi câu chính là thứ giữ an toàn:
`du an hoat dong ra sao` không khớp, `may hoat dong ra sao` thì khớp.

Đã chạy cả 86 tin thật qua `_requires_tool`: đúng 5 câu được miễn (3 xã giao +
2 câu hỏi về bot), 43 câu dữ liệu đều vẫn phải gọi tool — **0 sai cả hai
chiều**. Các câu bẫy nhất (`theo bạn thì phần lark minute ok hơn…`,
`ok mày đánh giá thế nào về nội dung cuộc họp này ?`) nay là test case.

### 11b.7 Heartbeat: sửa lại chính ngưỡng báo động vừa thêm

Lần chạy thật đầu tiên báo `job_ket=207m` + `job_hong=4` + `scan_cu=22m` trong
khi hệ thống **hoàn toàn khỏe** — nó đang nạp bù một loạt buổi đào tạo 2–3
tiếng audio, và vừa dịch xong một cuộc 9802s. Ba chỉ số đều sai kiểu:

- `job_ket` đo "tuổi job cũ nhất trong hàng đợi" -> báo động to nhất đúng lúc
  hệ thống làm việc chăm chỉ nhất. Thay bằng **`idle`** = "lâu bao nhiêu rồi
  không phiên âm xong cuộc nào", và chỉ báo khi CÒN job chờ.
- `job_hong` đếm cả 4 job `empty_transcript` (bản ghi 3s/37s không có tiếng
  nói) — chúng `failed` vĩnh viễn nên đó là WARN không bao giờ tắt. Nay đếm
  `failed_act`, trừ đúng danh sách `alerts.SILENT_FAIL_CODES`, có test đối
  chiếu hai bên để không trôi dạt.
- `scan_cu` im khi hàng đợi còn việc: vòng `run` chạy một mạch rồi mới quét
  lại, nên khoảng cách 20–30 phút giữa hai lần quét là thiết kế.

Ngưỡng tách thành hàm thuần `problems(d)` + `level_of(p)` để test được mà không
cần chạm vào máy. **Một cảnh báo không bao giờ tắt là một cảnh báo người ta học
cách bỏ qua** — đó là lý do sửa ngay thay vì để đó.

### 11b.8 ĐÃ ÁP lên hệ đang chạy — 06/08/2026 13:52

1. `%LOCALAPPDATA%\hermes\config.yaml` merge từ `hermes/feishu-config.yaml`
   bằng PyYAML (deep-merge, giữ nguyên 28 khoá ngoài phạm vi). Đúng hai thay
   đổi: `session_reset.idle_minutes` 15->60 và `platform_hints.feishu.append`.
   Sao lưu: `config.yaml.bak-20260806-135042`.
2. Plugin 1.2.0 đã chép + enable; SHA-256 của `__init__.py` và `plugin.yaml`
   khớp với bản trong repo.
3. `hermes gateway restart` lúc 13:52 — dừng sạch, websocket nối lại, 2
   platform, **0 lỗi/cảnh báo** sau khi khởi động.

Restart lúc không có ai chờ (tin cuối 09:56, cách 3,9 tiếng) —
`gateway_restart_notification: false` nên người đang chờ sẽ không được báo gì.

### 11b.9 Còn mở

`obsgz38g52uf4u1co84wt1x1` — `empty_transcript` trên **1517s** audio (25 phút).
Ba job `empty_transcript` còn lại chỉ 3s/3s/37s nên im lặng là hợp lý, nhưng
cuộc 25 phút ra 0 chữ nhiều khả năng là whisper nuốt. Whisper hiện chạy tốt
(17847 từ lúc 12:25 hôm nay), nên thử lại là đáng. CHƯA làm vì nó đổi dữ liệu
job và xếp sau 3 job backlog đang chạy:

```
sqlite3 "<state.db>" "UPDATE jobs SET status='queued', attempts=0
  WHERE minute_token='obsgz38g52uf4u1co84wt1x1';"
```

## 11c. Phiên 06/08/2026 (tối) — phủ sóng cuộc họp và trần quyền của Lark

Bối cảnh: chị Nguyễn Thị Ngọc Anh (L&D) vừa kết nối lúc 19:29 và báo không thấy
cuộc họp cũ; kèm nghi ngờ Base không cập nhật và không hỏi đáp được bằng Lark
Minute khi Base chưa xong.

### 11c.1 Đo được gì (tất cả đều CHỈ ĐỌC)

| Nghi ngờ | Thực tế đo được |
|---|---|
| Base không cập nhật | **Sai.** 0/76 job thiếu record Base (`held` 46, `waiting_auth` 18, `delivered` 6, `failed` 4, `discarded` 2). |
| Ngọc Anh không thấy cuộc họp | **Một phần.** Bot trả đúng 7 cuộc, 5 cuộc có biên bản đầy đủ. |
| Cuộc họp cũ bị thiếu | **Đúng.** Lark biết 18 cuộc của chị ấy trong 90 ngày, DB mới có 7. |
| Hỏi đáp bằng Lark Minute khi chưa có whisper | **Không làm được** — xem 11c.2. |

### 11c.2 TRẦN QUYỀN CỦA LARK MINUTES — phát hiện quan trọng nhất phiên này

Quyền Minutes có **hai tầng**, không phải một:

```
minutes_get       (tiêu đề, giờ, chủ, link, cover)  ->  NGƯỜI DỰ đọc được
minutes_transcript / minutes_media  (NỘI DUNG)      ->  CHỈ CHỦ BẢN GHI
```

Bằng chứng đo trực tiếp:

- `07-30 | Workforce AI Weekly Meeting: Buổi 4` — chủ bản ghi (Lê Quý Thiện)
  đọc được **44.340 ký tự** nguyên văn của Lark; Ngọc Anh, người **có dự chính
  cuộc đó**, bị `2091005 permission deny`.
- 18 job `waiting_auth`: **18/18** đọc được metadata, **0/18** đọc được nguyên
  văn hoặc tải được bản ghi.

Hệ quả phải nhớ trước khi hứa gì với người dùng: **không có đường vòng kỹ thuật
nào** cho cuộc họp mà chủ bản ghi chưa kết nối. Không phải whisper yếu, cũng
không phải thiếu tính năng "đọc Lark Minute" — Lark không đưa nội dung ra cho
bất kỳ token nào khác. `lark_api.minutes_transcript()` có sẵn trong repo và
chưa từng được gọi; giờ đã biết vì sao gọi nó cũng không cứu được gì.

Cách duy nhất: chính chủ bản ghi vào bot. Khi đó job tự chạy tiếp, không phải
làm gì thêm.

### 11c.3 `python -m v2 coverage [--days 90]` (mới)

Trả lời "ai đang thấy được bao nhiêu cuộc họp của họ, và ai đang chặn". CHỈ
ĐỌC. Hai khối: bảng phủ sóng theo người, và danh sách **chủ bản ghi chưa kết
nối** gom theo số cuộc đang chặn — đó là danh sách đi mời, không phải danh sách
để bot tự gửi link (§1 mục 2 vẫn nguyên).

Đo 06/08/2026: 8 người đang chặn 18 cuộc. Nhiều nhất là Đỗ Phương Linh (7 cuộc,
chuỗi Daily CDP Checkin) và LÊ MẠNH CHUNG - CEO (3 cuộc, HỌP EBITDA + 2 HỌP BOD).

### 11c.4 `ENROLL_BACKFILL_DAYS` (mới, MẶC ĐỊNH KHÔNG ĐỔI HÀNH VI)

Tách cửa sổ "quét lùi để tạo backlog lúc một người kết nối" khỏi `LOOKBACK_DAYS`:

- `LOOKBACK_DAYS` — chạy MỖI VÒNG, cho MỌI người, mãi mãi. Giữ 7.
- `ENROLL_BACKFILL_DAYS` — chạy MỘT LẦN, cho MỘT người, lúc họ vào.

Mặc định `= LOOKBACK_DAYS`, tức chưa đổi gì. Đặt `ENROLL_BACKFILL_DAYS=90`
trong `v2/.env` là được điều người dùng muốn: ai kết nối cũng có sẵn 90 ngày,
không phải chờ ai chạy `backfill` bằng tay.

Thẻ chào vẫn chỉ liệt kê `LOOKBACK_DAYS` ngày — cố ý tách, có test: nạp nền thì
im lặng và có ích, còn liệt kê 90 ngày ở tin nhắn đầu tiên là vài chục dòng
không ai đọc. Backlog vẫn `priority=0` + `notify=False`.

### 11c.4b ĐÃ CHẠY — 06/08/2026 23:16–23:20

- Backup trước: `state-2026-08-06-231645.db`.
- `v2/.env`: thêm `ENROLL_BACKFILL_DAYS=90`. Thẻ chào vẫn 7 ngày.
- `python -m v2 backfill --days 90 --yes` → nạp **11/11** job, `priority=0`,
  `notify=False`, ACL phần lớn là `calendar[verified]`. Không tin nào được gửi.
- `restart-v2.ps1` → đúng một orchestrator (PID 1692), đã đồng bộ 11 record
  Base và bắt đầu phiên âm; job đầu mượn quyền của chính chị Ngọc Anh.

### 11c.4c SỬA LẠI 11c.2 — không có API tóm tắt, kể cả cho chủ

Bản 11c.2 nói đúng về nguyên văn nhưng chưa trả lời câu "đọc **bản tóm tắt**
của Lark thì sao". Đã dò TOÀN BỘ endpoint Minutes v1 trên cùng một cuộc
(`08-06 | Workforce AI Weekly Meeting`), cả token chủ lẫn token người dự:

| Endpoint | Chủ bản ghi | Người dự |
|---|---|---|
| `minutes/{token}` | 200 — chỉ `cover, create_time, duration, owner_id, title, token, url` | 200, **y hệt** |
| `…/statistics` | 200 — chỉ lượt xem | 200, y hệt |
| `…/transcript` | 200 — **68.863 ký tự** | **403 `2091005`** |
| `…/media` | 200 — có `download_url` | **403 `2091005`** |
| `…/summary` | **404 — endpoint KHÔNG tồn tại** | 404 |

Kết luận đúng: **Minutes v1 không phơi bản tóm tắt ra API cho bất kỳ ai, kể cả
chủ bản ghi.** Bản tóm tắt nhìn thấy khi bấm link là giao diện web, không phải
dữ liệu API. Nên "cho bot đọc bản tóm tắt của Lark" không làm được — không phải
vì quyền, mà vì không có cửa nào để gọi.

Điều này KHÔNG chặn sản phẩm: bot tự sinh tóm tắt từ nguyên văn whisper, và bản
đó nằm trên đĩa nhà mình nên **người dự đọc được bình thường** qua ACL của V2.
Chỗ duy nhất tắc vẫn đúng như 11c.2: cuộc mà chủ bản ghi chưa kết nối thì không
có whisper (không tải được media) và cũng không có đường nào khác.

### 11c.4d Bot phân tích NHẦM CUỘC khi người dùng trả lời một thẻ

Ca thật 06/08/2026 19:51 (người dùng gửi ảnh chụp). Hệ thống đẩy thẻ
"Họp xong: 08-06 | Workforce AI Weekly Meeting"; người dùng bấm TRẢ LỜI chính
thẻ đó và gõ "Có recap rồi đó, phân tích". Bot đi phân tích một cuộc tên
`work` lúc 15:41 và kết luận "chưa phải một cuộc họp có nội dung hoàn chỉnh".

Ba điều cần tách bạch:

1. **Không phải lỗi recap.** Job `08-06` có `recap_json` 3.501 ký tự (tóm tắt
   1.307 ký tự, 6 quyết định, 7 việc cần làm) và transcript whisper 57.142 ký
   tự. Dòng "Chưa sinh được recap" trên thẻ là tin lúc **18:11**, sinh ra khi
   recap chưa chạy xong; thẻ là ảnh chụp một thời điểm, không tự cập nhật.
2. **Chính `chat_memory` góp phần gây lỗi.** Lúc 19:51 mục mới nhất trong ký ức
   của người này là `work` (17:50) — vì thẻ đẩy ra KHÔNG ghi vào ký ức. Cuộc
   `08-06` chỉ được ghi lúc 19:55, khi họ gọi đích danh.
3. **Sửa hai chỗ:**
   - `pipeline._anchor_memory` — gửi thẻ thành công cho ai thì ghi cuộc đó vào
     `chat_memory` của người đó. Không nới quyền: `recipients` vốn đã là người
     dự đã xác minh của chính cuộc ấy.
   - Khối ký ức trong `gate._memory_block` nay nói rõ **mục đầu tiên là cuộc
     mặc định** khi người dùng nói tắt, và không chắc thì hỏi lại một câu.

`pipeline.py` chạy trong orchestrator nên phần (3) chỉ có hiệu lực sau lần
`restart-v2.ps1` kế tiếp. Không gấp: job backlog `priority=0` kết thúc ở `held`
và KHÔNG đẩy thẻ, nên chỉ cuộc họp MỚI mới cần bản vá này.

**ĐÃ RESTART 07/08/2026 09:12** — orchestrator PID 14480, đúng một bản. Kiểm
bằng mốc thời gian chứ không tin dòng chữ "XONG" của script: mọi file đã sửa
(`orchestrator/pipeline/config/gate/qa/db/coverage/profile/.env`) đều có mtime
CŨ HƠN giờ khởi động tiến trình, tức đã được nạp thật.

### 11c.4e Bằng chứng "ai vào cũng được cập nhật"

Đo 07/08/2026 sau restart — cùng token, hai cửa sổ, đếm số minute Lark trả về:

| Người đã kết nối | 7 ngày | 90 ngày | chênh |
|---|---|---|---|
| Nguyễn Thùy Chi | 1 | 9 | +8 |
| Nguyễn Trần Thi | 7 | 14 | +7 |
| Nguyễn Tiến Thẩm | 3 | 9 | +6 |
| Lê Quý Thiện | 2 | 23 | +21 |
| Nguyễn Thị Ngọc Anh | 2 | 18 | **+16** |

Tức trước bản vá, người vào chỉ được phát hiện đúng cửa sổ 7 ngày — chị Ngọc
Anh lẽ ra nhận 18 cuộc thì chỉ được 2. Từ nay `welcome_and_backlog` quét
`max(ENROLL_BACKFILL_DAYS, LOOKBACK_DAYS)` = 90 ngày.

Đường chạy đã truy hết, không suy đoán:
`oauth._announce_completed` → `orchestrator.welcome_and_backlog` → quét 90 ngày
→ `enqueue_minute(priority=0, notify=False)`. Hai tiến trình có thể chạy đoạn
này đều đã ăn code mới: orchestrator (vừa restart) và tiến trình `v2 gate`
(sinh mới mỗi tin nhắn).

Kết quả sau backfill + restart: **0 cuộc còn thiếu** trong hệ thống (trước là
11). Chị Ngọc Anh từ 7 → **18 cuộc thấy được**, 5 → **15 cuộc đọc được nội
dung**. Ba cuộc còn lại của chị ấy nằm trong nhóm chờ chủ bản ghi kết nối.

### 11c.5 CHƯA làm — chờ người dùng quyết

Ba việc đều đụng vào luồng đang chạy nên dừng lại đúng như yêu cầu:

1. ~~`backfill --days 90 --yes`~~ — ĐÃ CHẠY 23:18, xem 11c.4b.
2. ~~Đặt `ENROLL_BACKFILL_DAYS=90`~~ — ĐÃ ĐẶT 23:17, xem 11c.4b.
3. **Mời 8 chủ bản ghi** — việc của người, không phải của bot. Bot KHÔNG được
   tự gửi link.

### 11c.6 Không cần làm: phân biệt Lark Minute vs whisper

Đã có sẵn và đúng: `qa.fmt_record` ghi "**Bản tóm tắt của Lark:** <link>" cho
bản của Lark, và "**Bản nguyên văn:** có sẵn — chép lại đúng từng câu" cho bản
whisper; `mcp_server` dặn agent không nói lẫn hai thứ. Không đổi gì.

## 11d. Phiên 07/08/2026 — đọc bản chép của Lark, ước tính, và luồng "có"

Người dùng nêu hai việc: bot trả lời thiếu tự nhiên; và **"nhiều case nó trả
lời là không đọc được bản Lark mà vào thì vẫn có đủ text"**. Kèm yêu cầu về
luồng: đọc được → hỏi có cần bản phiên âm chuẩn không → nói ước tính → tự gửi
khi xong, không phải nhắc lại.

### 11d.1 Vì sao bot "không đọc được" trong khi Lark có đủ chữ

KHÔNG phải trần quyền §11c.2 — đó là chuyện khác. Gốc là bot **chưa từng đi
lấy**: `lark_api.minutes_transcript()` chỉ được gọi ĐÚNG MỘT CHỖ
(`orchestrator._notify_minute`, lúc phát hiện cuộc họp) bằng ĐÚNG MỘT token —
token của người tình cờ tìm ra minute. Quyền đọc bản chép gắn với CHỦ bản ghi,
nên lời gọi đó gần như luôn hỏng, `minute_text` thành rỗng, thẻ "Họp xong" đi
ra không có tóm tắt, và **không có đường nào hỏi lại**. Từ đó tới lúc whisper
xong, mọi câu hỏi đều rơi vào "CHƯA CÓ BIÊN BẢN — đang phiên âm".

Đo 07/08/2026 trên chính DB này (chỉ đếm ký tự, không in nội dung ai):

| Nhóm job | Đọc được bản Lark |
|---|---|
| 12 cuộc gần nhất ĐÃ có whisper | **12/12** — 188 → 124.915 ký tự |
| 27 cuộc `waiting_auth` | **0/27** — chủ bản ghi chưa kết nối |
| 6 cuộc `failed` (chủ đã kết nối) | 6/6 |

Tức: **hễ whisper chạy được thì bản Lark cũng đọc được, và nó có sớm hơn
nhiều.** Cuộc `waiting_auth` vẫn tắc đúng như §11c.2 — không mở đường vòng nào.

### 11d.2 `v2/larktext.py` (mới) — lấy bằng THANG ứng viên, có cache

Dùng lại `pipeline._reader_candidates` (chủ → người dự → viewer kỹ thuật) chứ
không dựng danh sách riêng: quyền đọc bản chép và quyền tải bản ghi là cùng một
trần quyền, hai danh sách lệch nhau chỉ đẻ thêm một kiểu lỗi phải chẩn riêng.

- Lấy được → lưu `TRANSCRIPT_DIR/lark-<token>.txt`, ghi `jobs.lark_chars/lark_at`.
- Không ai đọc được → ghi `jobs.lark_tried_at`, **phanh 30 phút**. Không có
  phanh này thì một cuộc `waiting_auth` (tới 21 ứng viên) ăn 21 lời gọi mạng ở
  MỖI câu hỏi mà kết quả vẫn y nguyên.
- **Không phải cửa quyền.** Caller phải qua `qa._may_see` TRƯỚC. Selftest khoá
  lại: người không dự vẫn không lọt một chữ nào.

`_notify_minute` nay gọi qua đây, nên recap lúc họp xong cũng hết rỗng.

### 11d.3 `qa`: trả lời được ngay, và MỜI bản chuẩn

`get_transcript` và `get_meeting` thử bản Lark TRƯỚC khi nói "chưa có". Nội dung
đi kênh **DỮ LIỆU** (không phải "chép y hệt"), gắn nhãn nguồn rõ ràng — bản Lark
là bản máy nghe thô, không có tên người dự và không có glossary đã duyệt, nên nó
KHÔNG thay bản whisper.

Kênh nội bộ kèm `qa.OFFER_MARK = "[V2-OFFER: transcript]"` + ước tính thời gian,
dặn agent hỏi đúng một câu "có cần bản chuẩn không".

### 11d.4 `v2/eta.py` (mới) — ước tính bằng số đo

Câu cũ: *"cuộc ngắn vài phút, cuộc dài thì lâu hơn"* — đúng mà vô dụng. Đo 15
job gần nhất: tỉ lệ whisper/audio **0,11–0,16** (gộp 0,14; đo lại 07/08 = 0,148),
tức nhanh gấp ~7 lần thời gian thật, biên độ hẹp — con số này nói ra được.

Cộng thêm: tải mp4 + ffmpeg (`IO_RATIO` 0,06), recap LLM (`FIXED_OVERHEAD_S`
240), nửa nhịp `POLL_INTERVAL`, và **hàng chờ** (job đang chạy dở tính một nửa —
`process_queue` không cắt ngang). Làm tròn LÊN theo bậc thô dần: thà hứa dài hơn
thực tế. Không ước tính được thì trả rỗng và caller BỎ mệnh đề đó, không bịa số.

### 11d.5 Luồng "có" — chỗ suýt hỏng nếu chỉ sửa V2

Cổng write-tool của plugin chỉ nhìn TIN NHẮN HIỆN TẠI, nên bot mời xong người
dùng đáp **"có"** thì `send_transcript_file` bị chặn (chữ "có" không có trong
`_TRANSCRIPT_NOUNS`) — đúng lỗi đã sửa một lần ở `_asked_recently`.

Cách nối: `post_tool_call` thấy `_OFFER_MARK` trong kênh nội bộ → ghi phiên là
ĐÃ MỜI; lượt sau `_is_yes()` cho "có"/"ok"/"gửi đi" đi tiếp. Vẫn cần **cả hai
vế** (có mời VÀ có đồng ý) nên agent không tự mời rồi tự coi là được đồng ý.
`_is_yes` chặn nhầm bằng ba lớp: độ dài ≤4 chữ, TỪ ĐẦU trong danh sách, và không
nhắc phạm vi nghiệp vụ — "có cuộc họp nào hôm nay" là câu HỎI, không phải đồng ý.

`_cleanup_turn` (mỗi lượt) tách khỏi `_cleanup_session` (hết phiên): lời mời
PHẢI sống qua lượt, không thì câu "có" vô nghĩa.

**Phần tự gửi khi xong đã có sẵn** từ 03/08 — `sendfile` nâng `priority=2` + ghi
`transcript_requests`, `orchestrator._deliver_requested` gửi rồi xoá. Không sửa.

### 11d.6 Văn phong

Gốc của "thiếu tự nhiên" nhìn thấy trong ảnh chụp: agent **nhại nguyên xi nhãn
máy** ("hệ thống hiện vẫn báo **CHƯA CÓ BIÊN BẢN** — đang phiên âm"). Nhãn viết
hoa vẫn giữ trong danh sách vì mắt cần mốc để quét; chỗ chặn đúng là prompt.
`_POLICY` + `platform_hints.feishu` thêm: đừng nhại ngôn ngữ máy, đừng thuật lại
thao tác, đừng rào trước đón sau. `POLICY_VERSION` → `2026-08-07.1`.

### 11d.7 Trạng thái

Self-test **693 PASS / 0 FAIL** (trước: 648). Nhóm mới `34n` khoá lại: thang ứng
viên, phanh 30 phút, ACL không đổi, kênh DỮ LIỆU, hợp đồng `OFFER_MARK` giữa hai
tiến trình, ba lớp chặn của `_is_yes`, và ước tính tới được người dùng.

Ba cột `jobs.lark_chars/lark_at/lark_tried_at` ĐÃ có trên DB live (migration
additive, chạy ở mọi `db.init()`).

**CHƯA áp lên hệ đang chạy** — xem §11d.8.

### 11d.8 Việc áp (cần làm theo đúng thứ tự)

1. `restart-v2.ps1` — cho orchestrator ăn `larktext`/`eta`/`_notify_minute` mới.
2. Chép `hermes/v2-enroll-gate` (1.3.0) sang `%LOCALAPPDATA%\hermes\plugins`,
   rồi `hermes gateway restart`. **Bắt buộc**: MCP server chạy trong tiến trình
   của Hermes nên `qa`/`sendfile` mới chỉ có hiệu lực sau bước này, và luồng
   "có" thì hoàn toàn nằm ở plugin.
3. (Tuỳ chọn) deep-merge `hermes/feishu-config.yaml` vào
   `%LOCALAPPDATA%\hermes\config.yaml` bằng PyYAML — chỉ khoá
   `platform_hints.feishu.append`. Sao lưu trước. Bỏ qua cũng được: `_POLICY`
   của plugin chạy MỖI LƯỢT và đã mang đủ luật.

## 12. Checklist cho chat/code agent mới

Trước khi làm việc:

1. Đọc toàn bộ file này.
2. Đọc phần liên quan trong `V2_MAINTENANCE.md`; không cần đọc 250 KB nếu task hẹp.
3. Chạy `git status --short`; coi mọi thay đổi hiện có là của người dùng.
4. Chạy `python -m v2 selftest` trước và sau sửa.
5. Nếu liên quan live data/quyền/gửi tin/restart/external write: giải thích tác động và
   xin duyệt trước.
6. Không đọc/in transcript người khác để test ACL.
7. Không dựa vào Base, memory hoặc `minute_viewers` để mở quyền.
8. Sau OAuth phải giữ nguyên thứ tự: reverify verified → release waiting_auth → không
   gửi welcome kép trong inline gate.

Các câu hỏi mà tài liệu này phải trả lời được cho reader mới:

- OAuth hiện chạy ở đâu và vì sao không còn phụ thuộc Blob mới?
- Vì sao bot từng trả lời hai lần?
- Base có quyết định quyền hỏi đáp không?
- `minute_viewers` còn được dùng vào việc gì?
- Vì sao Workforce từng bị ẩn và bằng chứng nào mở lại nó?
- Điều kiện nào cho phép sửa ACL sau OAuth?
- Sáu job chờ auth phải cư xử thế nào?
- Lệnh nào kiểm sức khỏe và tiêu chuẩn PASS hiện tại là gì?
- Những external write nào vẫn chưa được duyệt?
- Điều gì tuyệt đối không được reset hoặc mở rộng?

