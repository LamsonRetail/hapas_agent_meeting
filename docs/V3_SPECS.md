# V3 — Specs: Xác nhận biên bản, kho .md phân quyền theo tổ chức, truy vấn ngữ nghĩa, dashboard

> Viết 16/09/2026. Trạng thái: **DRAFT — chưa được chủ hệ thống duyệt.**
> File này là đặc tả cho giai đoạn kế tiếp, dựa trên hiện trạng V2 (đã kiểm live
> tới 07–09/08/2026). Khi mâu thuẫn với tài liệu cũ về *hiện trạng*, tin
> `CURRENT_CONTEXT.md`; khi mâu thuẫn về *hướng đi mới*, tin file này sau khi
> các quyết định ở §C được chốt. Chủ hệ thống đã chốt 16/09/2026: **C.1**
> (auto-phát + confirm muộn được), **C.2** (Base = index, chi tiết trên Drive),
> **C.3** (quyền cũ giữ nguyên khi đổi sếp, không nhánh loại trừ, folder Drive
> tạo sẵn cho toàn bộ nhân sự), **C.4** (virtual key do thienlq cấp), **C.5**
> (dashboard tại meeting.lamsonretail.com / meeting.hapas-ai.tech, đăng nhập
> Lark SSO; chỉ số "họp kém hiệu quả" HOÃN).
>
> Quy ước của dự án vẫn giữ nguyên: mỗi hạng mục có "cách kiểm" — làm đúng nó
> rồi mới báo xong. Đo, đừng đoán.

---

## 0. Trạng thái triển khai (cập nhật khi xong từng việc)

| Việc | Code + selftest | Việc áp lên hệ đang chạy (máy Windows) |
|---|---|---|
| **YC1** xác nhận của chủ | ✅ **XONG 28/09/2026** — selftest 716 PASS / 0 FAIL (+21 kiểm) | restart `v2 run`; chép plugin `hermes/v2-enroll-gate` **1.4.0** + `hermes gateway restart` (tool MCP mới + policy duyệt) |
| **YC2** kho `.md` + quyền quản lý + Drive | ✅ **XONG 28/09/2026** — selftest 742 PASS / 0 FAIL (+26 kiểm) | Console: bật scope **tenant** đọc danh bạ (có trường `leader_user_id`, phạm vi dữ liệu = toàn công ty) + `drive`; `v2 org-sync` → `--yes` → `ORG_SYNC_HOURS=24`; `v2 drive-provision` → duyệt danh sách → `--yes` → `DRIVE_ENABLED=1`; Base tự thêm 2 cột `Xác nhận`, `File biên bản` ở lần ghi đầu (external write — C.6) |
| **YC3** semantic search | ✅ **XONG 28/09/2026** — selftest 758 PASS / 0 FAIL (+16 kiểm) | ⏸ **chờ thienlq** (xem mục "Việc giao thienlq" bên dưới) → `LITELLM_API_KEY` trong `v2/.env`; benchmark `v2 search --reindex --model <m> "câu hỏi thật"` cho 2–3 model rồi chốt `EMBED_MODEL`; chỉnh `semantic.MIN_SCORE` theo model đã chốt |
| **YC5** liên kết cuộc họp | ✅ **XONG 28/09/2026** — selftest 770 PASS / 0 FAIL (+12 kiểm) | restart `v2 run` + plugin 1.4.0 (tool `related_meetings`); sau khi chốt model embedding, chỉnh `links.LINK_MIN_SCORE` bằng 1 cặp dương + 1 cặp âm thật |
| **YC4** dashboard | ✅ **XONG 28/09/2026** — selftest 792 PASS / 0 FAIL (+22 kiểm); đã xem giao diện thật (desktop + mobile, light + dark) | Cloudflare Tunnel: 2 hostname → `http://127.0.0.1:8765`; Lark Console thêm Redirect URL `https://meeting.lamsonretail.com/auth/callback` + `https://meeting.hapas-ai.tech/auth/callback`; `DASHBOARD_SECRET` (chuỗi ngẫu nhiên ≥32 ký tự) trong `v2/.env`; chạy `run-v2-dashboard.bat` (thêm vào tự khởi động) |

**Thứ tự áp lên máy vận hành** (chạy `python -m v2 selftest` trước và sau):
1. `git pull` → restart `v2 run` (§16 sổ tay, luật 6 khi kill) → chép plugin
   `hermes/v2-enroll-gate` **1.4.0** + `hermes gateway restart`. Từ đây YC1
   (duyệt), YC5 và phần `.md`/quyền người dự của YC2 chạy ngay.
2. ⏸ Chờ thienlq cấp key → `LITELLM_API_KEY` → YC3 tự index nền; chốt `EMBED_MODEL`.
3. Console bật scope tenant danh bạ → `v2 org-sync` / `--yes` →
   `ORG_SYNC_HOURS=24` → `v2 notes` (cấp quyền quản lý cho cả cuộc cũ).
4. Console bật scope tenant Drive → `v2 drive-provision` / duyệt / `--yes` →
   `DRIVE_ENABLED=1` → `v2 notes` (đẩy file cũ lên Drive, share im lặng).
5. Tunnel + Redirect URL + `DASHBOARD_SECRET` → `run-v2-dashboard.bat`.

**Việc giao thienlq** (Lê Quý Thiện — Technical & AI Automation Leader,
`thienlq@hapas.vn`) — chốt 01/10/2026: gửi tin Lark để thienlq làm sau.
Trạng thái: ⏸ chưa gửi tin.
1. Cấp **virtual key** LiteLLM (`litellm.hapas-ai.tech`) cho MeetingxLark.
2. Bật trên key đó: 1 model **embedding** (đề xuất `text-embedding-3-small`)
   và 1 model **chat rẻ** cho digest dashboard (đề xuất `gpt-4o-mini`).
3. Đặt **giới hạn ngân sách tháng** ngay trên key.
4. Gửi key cho người vận hành qua kênh riêng (không dán vào chat nhóm/git).

Thiếu key thì hệ thống VẪN chạy: tìm kiếm rơi về từ khoá, dashboard không có
đoạn tóm tắt kỳ (vẫn có danh sách quyết định).

---

## A. Hiện trạng (nền để bổ sung, không viết lại)

### A.1 Dữ liệu meeting notes đang nằm ở đâu

| Thứ | Chỗ lưu | Ghi chú |
|---|---|---|
| **Index / nguồn sự thật** | bảng `jobs` trong SQLite `v2/data/state.db` | 1 dòng = 1 cuộc họp, khoá `minute_token`; chứa `status`, `meta_json` (người dự + ACL), `recap_json`, `transcript_path`, `bitable_record_id` |
| **Index cho người đọc** | Lark Base "Biên bản" (`v2/bitable.py`) — [link Base](https://o4pvcegwn6b.sg.larksuite.com/base/OuQ1b3f3JaVpTSs33SVlbWHZgef?table=tbl3oT9hsqOMqeTr&view=vewZG2bQgk) | GƯƠNG của `jobs`, mỗi job 1 record, ~24 cột. Base KHÔNG quyết định quyền. **Từ V3 (chốt 16/09/2026): Base chỉ còn là INDEX** — nội dung chi tiết dời sang file `.md` trên Drive (YC2) |
| Recap | `jobs.recap_json` + cột `Tóm tắt`/`Quyết định`/`Việc cần làm` trên Base | sinh bởi seam `summarize()` |
| Transcript whisper | JSON có `segments` tại `v2/data/transcripts/{title}-{minute_token}.json` (`pipeline.py:283`) | + attachment cột `File transcript` trên Base + file `.docx` |
| Bản chép của Lark | cache `v2/data/transcripts/lark-{minute_token}.txt` (`larktext.py`) | metadata ở `jobs.lark_chars/lark_at/lark_tried_at` |
| Ký ức hội thoại | bảng `chat_memory` | |

Toàn bộ state di trú gói trong `state.db` + `.env` (seam 4.3, V2_LONGTERM).

### A.2 Khả năng phân tích đã có

- `summarize()`: transcript → `{summary, decisions, action_items}`, mỗi cuộc một lần.
- MCP server (`v2/mcp_server.py`) phơi 8 tool cho Hermes: `list_meetings`,
  `get_meeting`, `search_meetings`, `get_transcript`, `send_transcript_file`,
  `create_task`, `glossary_*`. Agent Hermes là lớp phân tích thực tế.
- `search_meetings`: **substring matching** trên 4 cột Base. Không semantic.
- Transcript không vào prompt mặc định; `get_transcript` trả từng phần 6.000 ký tự.
- ACL: `_may_see` — chỉ người dự hoặc chủ cuộc họp; `who=None` → không trả gì.
- `qa.py` chỉ ĐỌC (luật 3/6): nội dung Base = input không tin cậy, chảy vào
  prompt agent.

### A.3 Khoảng cách so với 5 yêu cầu mới

1. Không có bước owner xác nhận: `transcribed` → phát thẳng (luồng duyệt cũ đã
   bị bỏ 31/07/2026).
2. Không có file `.md`; không có kho chia sẻ kiểu Drive; ACL không biết gì về
   cấp quản lý.
3. Tìm kiếm không ngữ nghĩa; agent không chủ động gom cuộc họp liên quan ngoài
   phạm vi người dự.
4. Trang Vercel hiện chỉ là heartbeat/status vận hành, không có backlog
   confirmation, chất lượng notes, digest tuần/tháng.
5. Không có liên kết ngữ nghĩa giữa các cuộc họp.

---

## B. Đặc tả 5 yêu cầu

## YC1 — Meeting confirmation: owner duyệt/chỉnh trước khi phát

### Mục tiêu
Sau khi có transcript + recap, gửi cho **chủ cuộc họp** để confirm/chỉnh sửa.
Chỉ sau khi owner xác nhận mới gửi brief + link cho từng người tham gia.

### Bối cảnh phải biết trước khi làm
Luồng duyệt (`approval_msg_id`, `draft/final`) **đã từng tồn tại và bị bỏ**
31/07/2026 vì không ai flip sang `final` → mọi record kẹt `draft` vĩnh viễn.
Bản mới KHÁC bản cũ ở ba chỗ, và đây là điều kiện để không lặp lại lỗi cũ:
1. có **nhắc lại** (reminder) khi owner im lặng;
2. có **chính sách timeout** rõ ràng (ĐÃ CHỐT §C.1: 24h không phản hồi → tự
   phát kèm nhãn "chưa được chủ trì review", và confirm muộn vẫn được);
3. trạng thái confirm là **dữ liệu đo được** (bảng riêng), nuôi dashboard YC4.

### Máy trạng thái mới
```
transcribed ──> awaiting_confirm ──(owner "duyệt")──> confirmed ──> delivered
                   │  │
                   │  └─(owner gửi chỉnh sửa)──> editing ──> awaiting_confirm (vòng lại, bản v2)
                   └─(timeout 24h, §C.1 ĐÃ CHỐT)──> auto_published ──> delivered
                                                        │
                              (owner confirm/sửa MUỘN, không giới hạn thời gian)
                                                        ▼
                                                    confirmed  → cập nhật .md/Base,
                                                                 báo người dự nếu nội dung đổi
```
**C.1 ĐÃ CHỐT 16/09/2026:** owner im lặng → **tự phát sau 24h**, bản phát và
file `.md` mang nhãn rõ **"⚠ chưa được chủ trì review"**; owner **vẫn confirm/
chỉnh sửa được sau đó** — khi đó hệ thống cập nhật `.md` + record Base, gỡ
nhãn, và nếu nội dung ĐỔI thì gửi cho người dự một tin ngắn "biên bản đã được
chủ trì hiệu chỉnh" kèm link (không gửi lại cả brief). `auto_published` là
trạng thái dữ liệu (cột riêng/flag trong `confirmations`), không phải nhánh
chết: job vẫn `delivered` như thường.
- Job `held` hiện tại (mô hình kéo) giữ nguyên ngữ nghĩa: `held` là SAU
  confirm, chờ người dự hỏi transcript.

### Thiết kế
- **Gửi cho owner**: card DM gồm recap (tóm tắt/quyết định/việc cần làm) + file
  transcript đính kèm + hai hành động: `Duyệt & phát` / `Cần sửa`. Đường "Cần
  sửa": owner nhắn nội dung chỉnh (text tự do) → LLM áp chỉnh sửa vào recap
  (KHÔNG sửa transcript nguyên văn — bản nguyên văn là bằng chứng, chỉ recap là
  sản phẩm biên tập) → gửi lại bản v2 để duyệt.
- **Bảng mới `confirmations`**: `minute_token, version, sent_at, responded_at,
  action('approve'|'edit'|'timeout'), edits_text, diff_chars, edited_recap_json`.
  Mỗi vòng một dòng. `diff_chars` = khoảng cách chỉnh sửa giữa recap máy sinh
  và bản owner chốt — nguyên liệu của chỉ số "chất lượng notes" (YC4).
- **Chỉ owner được confirm.** Xác định owner từ `jobs.meta_json` (owner đã
  verified), KHÔNG suy từ Base/`minute_viewers`/lịch gần giờ (bất biến cũ giữ nguyên).
- Owner chưa enroll → job vào `awaiting_confirm` và đứng đó; bot KHÔNG chủ
  động gửi link OAuth (quyết định user, CURRENT_CONTEXT §1.2). Alert cho admin
  sau N ngày (mặc định 3) qua `alerts.py`.
- Sau `confirmed`: đi đường `pipeline.deliver` hiện có (brief + link Minutes
  cho từng người dự đã enroll; người chưa enroll giữ luật hiện tại).

### Chạm vào đâu
`db.py` (bảng + status mới, migration additive), `pipeline.py` (cắt deliver
thành 2 pha), `cards.py` (card confirm), `ws_listener.py`/plugin Hermes (nhận
phản hồi owner), `alerts.py` (backlog quá hạn), `bitable.py` (cột `Tình trạng
xử lý` thêm giá trị mới), `selftest.py`.

### ✅ ĐÃ LÀM (28/09/2026) — khác spec ở 3 chỗ, có lý do
- **Trạng thái duyệt KHÔNG nằm trong `jobs.status`** mà ở bảng riêng
  `confirmations` (một dòng/cuộc, có bộ đếm `version/edits/diff_chars`). Lý do:
  thẻ báo đi lúc họp xong (bản chép của Lark) trong khi whisper còn chạy nền
  hàng giờ — hai máy trạng thái độc lập, trộn vào một cột là phá hàng đợi.
- **Chủ chưa enroll → phát NGAY kèm nhãn "chưa review"** (không chờ 24h): bot
  chỉ nhắn người đã cấp quyền, nên không ai duyệt được — chờ là vô ích. Chủ
  enroll về sau vẫn duyệt/sửa được.
- **Không có nút bấm** — card action đi vào WebSocket của Hermes. Chủ NHẮN
  "duyệt …"/"sửa …", agent gọi tool MCP `confirm_meeting` / `edit_meeting`
  (policy plugin 1.4.0 dặn cách gọi).

Code: `v2/confirm.py` (mới) · `orchestrator._notify_minute` gác `confirm.gate`
trước khi phát, tách `_broadcast_notice` · `confirm.tick()` mỗi vòng run (khối
try riêng) · `pipeline.save_recap` khoá bản của chủ · `summarize.apply_edit` ·
`cards.confirm_card` + nhãn `unreviewed`/`revised` · 2 tool MCP · config
`CONFIRM_ENABLED=1`, `CONFIRM_REMIND_HOURS=8`, `CONFIRM_TIMEOUT_HOURS=24`.

Bản whisper về khi chủ CHƯA đụng vào → chủ nhận "Bản cập nhật" để duyệt đúng
cái sẽ phát; chủ đã sửa/duyệt → bản của chủ bị khoá, whisper không ghi đè.

### Cách kiểm
1. Job giả có owner = mình → nhận đúng 1 card confirm; vòng run thứ hai không
   gửi lại (dedup như `outbound_dedup`).
2. Trả lời "duyệt" → job sang `delivered`, người dự nhận brief; bảng
   `confirmations` có dòng `approve`, `diff_chars=0`.
3. Trả lời chỉnh sửa → nhận bản v2; duyệt v2 → bản phát là bản ĐÃ SỬA (so
   chuỗi), `confirmations` có 2 dòng, `diff_chars>0`.
4. Không trả lời + giả lập quá 24h → tự phát, tin phát VÀ file `.md` có nhãn
   "chưa được chủ trì review"; `confirmations` có dòng `timeout`.
5. Sau khi đã auto-phát, owner nhắn chỉnh sửa → `.md` + Base cập nhật, nhãn
   được gỡ, người dự nhận tin "đã hiệu chỉnh" đúng 1 lần; owner nhắn "duyệt"
   không sửa gì → chỉ gỡ nhãn, KHÔNG gửi gì cho người dự.
6. Người KHÔNG phải owner nhắn "duyệt" → từ chối, log lại, không đổi status.

---

## YC2 — Kho meeting notes `.md` phân quyền theo cây tổ chức

### Mục tiêu
Mỗi cuộc họp một file `.md`, sống trong "space" của meeting owner (cơ cấu kiểu
Google Drive của workspace), share cho người dự, và **hiển thị cho chuỗi quản
lý**: quản lý các cấp (tới CEO) thấy được nội dung họp của thành viên nhánh
mình dù không tham dự.

Ví dụ chuẩn (dùng làm test case): họp owner A, tham gia B, C. D là quản lý của
E, E là quản lý của C ⇒ **D và E đều thấy và truy vấn được** cuộc họp này.

### ⚠️ Đây là thay đổi CHÍNH SÁCH phân quyền, không phải tính năng thường
Bất biến hiện hành (CURRENT_CONTEXT §1, §7) cấm mở quyền ngoài "người dự
verified". Yêu cầu này **cố ý nới** theo một nguồn mới: cây quản lý. Điều kiện
để nới mà không lặp lại sự cố lộ chéo 05/08:
1. Nguồn cây quản lý phải là **dữ liệu có thẩm quyền**: trường `leader` /
   department của Lark Contact (API `contact/v3`), KHÔNG phải suy đoán từ tên,
   lịch, hay Base.
2. Quyền theo chuỗi quản lý là **một luật, một chỗ**: mở rộng đúng hàm
   `_may_see` (qa.py) — không dựng luật song song (bài học §21: hai luật song
   song sẽ lệch, cái lỏng hơn thắng).
3. Mọi lượt xem "nhờ quyền quản lý" phải **ghi log audit** (ai xem, cuộc nào,
   theo nhánh nào) — để trả lời được câu "vì sao D đọc được họp của C".
4. Các bất biến khác giữ nguyên: `minute_viewers` không cấp quyền; lịch gần
   giờ không cấp quyền; Base không cấp quyền.

### Thiết kế

**B2.1 File `.md`** — sinh sau `confirmed` (bản đã duyệt, không phải draft):
```
# {Tên cuộc họp}
> Thời gian · Chủ trì · Người dự · minute_token · trạng thái xác nhận
## Tóm tắt
## Quyết định
## Việc cần làm  (checkbox, có người phụ trách nếu trích được)
## Liên kết      (Link Minutes của Lark; các cuộc họp liên quan — YC5)
## Transcript    (link/tham chiếu file, KHÔNG nhúng nguyên văn vào .md)
```
Lưu local `v2/data/notes/{owner_user_id}/{yyyy-mm}/{slug}-{minute_token}.md`
(local là nguồn sự thật, đồng bộ được, backup theo `state.db` + `data/`).

**B2.2 Kho chia sẻ — ĐÃ CHỐT 16/09/2026: Lark Drive là nơi ở của file chi tiết, Base chỉ làm index.**
- **Drive**: mỗi owner một folder ("space" của owner), file `.md` của từng cuộc
  họp nằm trong đó (upload `.md` hoặc import thành Lark Doc — chọn khi làm, tiêu
  chí: bản nào set permission qua `drive/v1/permissions` sạch hơn). Share cho
  người dự + chuỗi quản lý theo `may_see` (B2.4). **Giá phải trả: scope mới
  `drive`/`docs`** → màn hình duyệt mới, mọi người enroll lại (V2_LONGTERM
  §3.2). Đang chỉ có 1–2 người enroll — xin NGAY, gộp một lần với `contact`
  (B2.3), trước người thứ ba.
- **Base hiện tại** ([OuQ1b3f3JaVpTSs33SVlbWHZgef](https://o4pvcegwn6b.sg.larksuite.com/base/OuQ1b3f3JaVpTSs33SVlbWHZgef?table=tbl3oT9hsqOMqeTr&view=vewZG2bQgk))
  rút về vai trò **index**: giữ các cột định danh + vận hành (Cuộc họp, Thời
  gian, Chủ, Người dự, Tình trạng xử lý/gửi, Xác nhận, Link Minutes,
  minute_token) và thêm cột mới **`File biên bản`** = link tới file trên Drive.
  Các cột nội dung nặng (`Tóm tắt`/`Quyết định`/`Việc cần làm`/`File
  transcript`) ngừng ghi bản đầy đủ — chỉ còn 1–2 dòng mồi hoặc bỏ hẳn (chốt
  khi migrate; record cũ giữ nguyên, không xoá dữ liệu lịch sử).
- **Hệ quả cho tầng hỏi đáp (bắt buộc làm cùng lúc):** `qa.py` hiện đọc nội
  dung từ cột Base — khi Base thành index thì tầng dữ liệu chuyển sang đọc
  `jobs.recap_json` trong `state.db` (vốn là nguồn sự thật) + file `.md`.
  Vẫn MỘT luật quyền `may_see`; permission trên Drive chỉ là *chiếu* của nó —
  nguồn sự thật ACL không rời khỏi V2.
- File `.md` local (B2.1) vẫn được ghi trước, Drive là bản xuất bản đồng bộ từ
  local: mất mạng/Drive lỗi thì pipeline không chết, đồng bộ lại sau (cùng
  triết lý `TranscribeUnavailable`).

**B2.2b Provisioning folder — ĐÃ CHỐT 16/09/2026: tạo sẵn cho TOÀN BỘ nhân sự,
share im lặng, người mới tự có.**
- Chu kỳ sync org (B2.3) đồng thời là chu kỳ provisioning: nhân sự nào trong
  `org_edges` chưa có folder → tạo `Meeting Notes/{tên} ({user_id})/` và share
  cho đúng cá nhân đó với `need_notification=false` (API permission của Drive
  có cờ này — kiểm bằng `--dry-run` trước khi chạy thật). KHÔNG gửi bất kỳ
  DM/notify nào khi tạo.
- Người mới vào công ty → xuất hiện ở lần sync kế tiếp → folder tự tạo. Không
  cần ai làm gì.
- Bảng mới `drive_spaces(user_id, folder_token, created_at)` — map user →
  folder, để pipeline biết đổ file `.md` vào đâu mà không phải search Drive
  (cùng lý do `bitable_record_id` không tra lại bằng search).
- Folder tạo bằng danh tính BOT (tenant token) để bot luôn còn quyền quản lý
  permission về sau; cá nhân được share quyền xem/sửa folder của mình.
- Lần chạy đầu là một đợt tạo hàng loạt (~toàn công ty): chạy `--dry-run` in
  danh sách, chủ hệ thống duyệt, rồi mới chạy thật — đây là external write
  diện rộng, theo đúng tiền lệ §1.9.

**B2.3 Cây tổ chức** — bảng mới `org_edges(user_id, leader_user_id, dept_id,
synced_at)`, đồng bộ định kỳ (mặc định 24h) từ Lark Contact; cần scope đọc
contact đủ sâu (kiểm bằng lời gọi cố ý sai — kỹ thuật đã dùng ở V2). Hàm mới
`org.chain_up(user_id) -> [manager1, manager2, ..., CEO]` (đi lên, có chặn
vòng lặp và trần độ sâu 10).

**B2.4 Luật quyền mới (thay `_may_see`, một chỗ duy nhất) — C.3 ĐÃ CHỐT:
quyền được VẬT CHẤT HOÁ lúc phát, đổi sếp thì cái cũ vẫn giữ.**

Vì "đổi sếp thì những cái cũ vẫn giữ" nên KHÔNG tính quyền live từ org hiện
tại (tính live = đổi sếp là nhánh cũ mất quyền cũ — trái quyết định). Thay
vào đó, lúc job phát (confirmed/auto_published) hệ thống **chụp** chuỗi quản
lý tại thời điểm đó và ghi vào bảng mới:
```
note_grants(minute_token, user_id,
            source('attendee'|'owner'|'chain:D>E>C'), granted_at)
may_see(user, meeting) := ∃ dòng note_grants(meeting, user)
```
- Grant một khi đã ghi thì **không bị thu hồi** khi org đổi. Không có nhánh
  loại trừ; CEO/chuỗi trên cùng thấy tất cả nhánh dưới.
- Cuộc họp MỚI tính theo org tại thời điểm phát (org mới).
- Sếp mới nhận đội mới: mặc định chỉ có quyền các cuộc **từ khi tiếp quản**;
  muốn cấp lùi thì chạy lệnh chủ động `v2 grants-backfill --user <id> [--since]`
  (hành động tường minh của admin, có log — không tự động).
- Nghỉ việc: `tokens` revoke như hiện tại; dòng `note_grants` giữ làm sử liệu
  nhưng người không còn enroll/không còn trong org thì không còn đường hỏi.
- Cột `source` chính là audit log tại chỗ: trả lời được "vì sao D đọc được
  họp của C" bằng một câu SELECT.

### ✅ ĐÃ LÀM (28/09/2026) — các điểm khác/bổ sung so với thiết kế trên
- **Scope là của BOT (tenant), không phải của user** → bật trên Console một
  lần, **không ai phải enroll lại**. Folder/file do bot tạo; bot luôn giữ
  quyền quản lý permission.
- **Base giữ cột Tóm tắt/Quyết định/Việc cần làm làm "trích yếu" của index**
  thay vì bỏ — nhờ vậy `qa` không phải viết lại, ACL vẫn một chỗ. File chi
  tiết `.md` nằm trên Drive, Base có link ở cột `File biên bản` + cột
  `Xác nhận` (C.6, đi đường gương `sync_jobs`). Muốn bỏ hẳn cột nội dung thì
  làm sau, là thay đổi độc lập.
- **`.md` KHÔNG chứa transcript nguyên văn** — bản nguyên văn vẫn đi đường
  kéo (luật cũ "không tự đẩy transcript" giữ nguyên). Hệ quả cần biết: quản lý
  có quyền cuộc họp thì cũng *xin* được bản nguyên văn qua bot, vì chỉ có MỘT
  luật quyền (`qa.viewers_index`).
- Quyền vật chất hoá ở bảng `note_grants` (cột `source` = audit, vd
  `chain:D>E>C`); `qa.viewers_index` cộng bảng này — mọi đường đọc (hỏi đáp,
  gửi transcript, tạo task) tự áp luật mới.
- File local (nguồn) ở `v2/data/notes/{owner_open_id}/{yyyy-mm}/…md`; Drive là
  bản xuất bản: hỏng Drive không chặn việc phát. *ponytail:* nội dung đổi →
  file Drive mới (link đổi, Base cập nhật theo); muốn link cố định thì import
  thành Lark Doc.
- **Hai công tắc MẶC ĐỊNH TẮT** (`ORG_SYNC_HOURS=0`, `DRIVE_ENABLED=0`) vì bật
  là đổi quyền xem / ghi ra ngoài trên hệ đang chạy (AGENTS.md). Lệnh mới đều
  THỬ KHÔ mặc định: `v2 org-sync`, `v2 drive-provision`,
  `v2 grants-backfill --open-id … [--since]`, `v2 notes [token]`.
- Nhân viên đã nghỉ (`is_resigned`) bị bỏ khỏi cây; quyền đã cấp không xoá.

Code: `v2/org.py`, `v2/notes.py` (mới) · 4 bảng `org_edges`, `note_grants`,
`drive_spaces`, `note_files` · `lark_api` thêm danh bạ/Drive +
`drive_member_add(notify=False)` · `qa.viewers_index` · `orchestrator`
(`_publish_note` ở điểm phát, làm mới khi có tóm tắt nguyên văn,
`_maybe_org_sync` mỗi vòng) · `bitable` 2 cột index.

### Cách kiểm
1. Dựng org giả trong `org_edges`: D→E→C. Họp (A owner, B, C dự):
   `may_see` phải cho A,B,C,E,D và **từ chối** người ngoài nhánh (F cùng công
   ty nhưng khác nhánh).
2. `v2 ask --as D "C họp gì tuần này"` trả về cuộc họp đó; log audit ghi
   `D xem qua nhánh D>E>C`.
3. Đổi leader của C sang nhánh khác (F): các cuộc CŨ — E và D **vẫn thấy**
   (grant đã ghi, không thu hồi); cuộc họp MỚI của C → F thấy, E/D không có
   grant mới. `grants-backfill --user F` → F thấy cả cuộc cũ, có log.
4. File `.md` render đúng schema, KHÔNG chứa transcript nguyên văn.
5. Permission trên Drive khớp 100% với `may_see` cho 3 cuộc mẫu; sửa quyền
   tay trên Drive → chu kỳ đồng bộ kế tiếp phát hiện lệch và báo (Drive là
   chiếu, không phải nguồn).
6. Record Base sau migrate: bấm `File biên bản` mở đúng file Drive; cột nội
   dung nặng không còn bản đầy đủ ở record mới.

---

## YC3 — Truy vấn ngữ nghĩa, agent chủ động gom cuộc họp liên quan

### Mục tiêu
Người hỏi → agent tự tìm **hết** các cuộc họp liên quan trong phạm vi
`may_see` mới (gồm cả cuộc không tham dự nhưng thấy được nhờ chuỗi quản lý),
tìm theo **ngữ nghĩa** chứ không chỉ substring. Model qua **virtual key trên
`litellm.hapas-ai.tech`**, chọn model tối ưu chi phí.

### Thiết kế

**B3.1 Seam embedding** (song song `transcribe()`/`summarize()`):
```python
def embed(texts: list[str]) -> list[list[float]]:
    """Không rò khái niệm provider. Sau lưng: LiteLLM proxy."""
```
Env mới (`config.py` + `.env.example`):
```
EMBED_BASE_URL=https://litellm.hapas-ai.tech   # OpenAI-compatible /v1
EMBED_API_KEY=<virtual key — không commit>
EMBED_MODEL=<chốt sau benchmark §C.4>
```
LiteLLM là OpenAI-compatible nên đây đúng là "món quà seam 4.2": đổi model =
đổi env. Ứng viên rẻ, đo trên tiếng Việt thật rồi mới chốt (đừng chọn theo
cảm giác): `text-embedding-3-small`, `bge-m3`, `multilingual-e5-small`. Recap
tiếng Việt xen thuật ngữ Anh → nghiêng về model đa ngữ, nhưng **số đo quyết
định**.

**B3.2 Lưu vector**: bảng `embeddings(minute_token, chunk_id, kind
('recap'|'transcript'), text, vec BLOB, model, created_at)` ngay trong
`state.db` (giữ seam "toàn bộ state trong state.db + .env"). Quy mô vài trăm →
vài nghìn cuộc họp: **brute-force cosine bằng numpy là đủ**, chưa cần
sqlite-vec/FAISS — thêm hạ tầng khi chưa cần là mua rủi ro. Chunking: recap
nguyên khối + transcript theo cửa sổ ~1.500 ký tự chờm 200.
Chỉ index **bản đã confirmed**; owner sửa recap → re-embed.

**B3.3 Tool MCP mới `semantic_search(query, top_k=8, since=, until=)`**:
1. Lọc quyền TRƯỚC (`may_see`) rồi mới rank — giữ nguyên lý do chống dò nội
   dung đã ghi trong `search_meetings`.
2. Embed câu hỏi → cosine trên tập được phép → trả record + đoạn khớp +
   `minute_token` (kênh agent-note như hiện tại).
3. Kết quả kèm nhãn nguồn quyền: `(bạn dự)` / `(quyền quản lý, nhánh …)` — vừa
   minh bạch vừa là audit tại chỗ.
4. `search_meetings` substring GIỮ NGUYÊN làm đường fallback (mất mạng
   LiteLLM ≠ mất tìm kiếm) — mô tả tool dặn agent thứ tự dùng.

**B3.4 "Chủ động"** nằm ở prompt/`_POLICY` plugin Hermes: câu hỏi về nội dung
họp → gọi `semantic_search` trước, chỉ khi rỗng mới nói không có; câu hỏi có
tên người/dự án → tự mở rộng truy vấn. Không đổi kiến trúc chiều gọi
(Hermes → V2).

### ✅ ĐÃ LÀM (28/09/2026)
- `v2/semantic.py`: seam `embed()` → `{LITELLM_BASE_URL}/embeddings`
  (mặc định `https://litellm.hapas-ai.tech/v1`, key `LITELLM_API_KEY`, model
  `EMBED_MODEL=text-embedding-3-small` — rẻ nhất đủ dùng, chờ benchmark thật).
- Index: tóm tắt (bản CHỦ đã duyệt nếu có) + nguyên văn whisper (hoặc bản chép
  Lark) cắt 1.500 ký tự chờm 200. Dấu vân tay nguồn → recap/transcript/model
  đổi là tự index lại. Chạy nền mỗi vòng run (`EMBED_PER_ROUND=5`), LiteLLM
  chết thì vòng sau. Không numpy: `array` stdlib + tích vô hướng
  (*ponytail:* tới ~10k đoạn; vượt thì numpy/sqlite-vec).
- Tool MCP `semantic_search` (LỌC QUYỀN TRƯỚC khi xếp hạng; nhãn "bạn dự" /
  "quyền quản lý, nhánh D>E>C"); policy plugin dặn gọi nó ĐẦU TIÊN cho câu hỏi
  nội dung. Chưa có key / LiteLLM lỗi / chưa index → rơi về tìm từ khoá, nói rõ.
- Mọi lượt tìm ghi `query_log` (nguồn cho dashboard YC4).
- CLI `v2 search "…" [--as người] [--model m] [--reindex]` — cũng là công cụ
  benchmark model.
- **Vá kèm cho YC1** (tìm ra khi làm YC3): trong lúc CHỜ CHỦ DUYỆT, người dự
  hỏi bot vẫn ra nội dung — đi vòng qua cổng duyệt. Nay `qa.viewers_index`
  cho cuộc `pending` chỉ chủ thấy; duyệt/tự phát xong mới mở.

### Cách kiểm
1. Benchmark 20 câu hỏi thật trên ≥20 recap thật: semantic vs substring, đếm
   hit đúng (đây là bài "một buổi chiều" như V2_LONGTERM đã tả cho recap).
2. Hỏi "tuần trước chốt gì về giá?" khi biên bản chỉ viết "đơn giá" → semantic
   trúng, substring trượt (ghi lại làm bằng chứng giá trị).
3. ACL: cùng câu hỏi, `--as` người ngoài nhánh → 0 kết quả từ cuộc họp đó.
4. Tắt LiteLLM (trỏ cổng chết) → tool trả lỗi mềm + agent rơi về substring;
   **không** làm chết vòng run (embedding job bọc try/except như alerts).
5. Chi phí: log token/lượt embed; ước tính tháng ở mức đã chốt §C.4.

---

## YC4 — Dashboard theo dõi

### Mục tiêu
Một trang nhìn được: (1) backlog confirmation, (2) truy vấn notes, (3) chất
lượng notes, (4) nội dung chính tuần/tháng, (5) pending/rủi ro.
("Họp kém hiệu quả" — **HOÃN theo quyết định 16/09/2026**, chưa cần làm; giữ
lại thiết kế cũ ở cuối mục làm ghi chú cho sau.)

### Nguồn dữ liệu (tất cả đã/ sẽ có trong `state.db` — dashboard chỉ ĐỌC)
| Khối | Nguồn | Định nghĩa đo được |
|---|---|---|
| Confirmation backlog | `jobs.status='awaiting_confirm'` + `confirmations` | danh sách: cuộc họp, owner, chờ bao lâu, đã nhắc mấy lần; tô đỏ quá SLA |
| Notes query | bảng mới `query_log(ts, asker, tool, query, n_hits)` ghi từ `mcp_server` | lượt hỏi/ngày, top chủ đề hỏi, tỉ lệ trả rỗng (rỗng nhiều = kho thiếu hoặc tìm kém) |
| Chất lượng notes | `confirmations.diff_chars` + action | % duyệt-nguyên-bản, diff trung bình, xu hướng theo tuần (whisper/glossary tốt lên thì diff phải giảm) |
| Nội dung chính tuần/tháng | LLM digest từ recap confirmed trong kỳ (qua LiteLLM, chạy batch cuối tuần) | 5–7 gạch đầu dòng + quyết định lớn + link từng cuộc |
| Pending / rủi ro | action_items chưa `create_task` hoặc task chưa xong; chủ đề lặp ≥N tuần không có quyết định mới (dùng liên kết YC5) | bảng: vấn đề, xuất hiện ở những cuộc nào, lần cuối nhắc |
| ~~Họp kém hiệu quả~~ **HOÃN** | (giữ làm ghi chú cho sau: tín hiệu 0 quyết định + 0 việc cần làm; recap rỗng; họp dài mật độ thấp) | không làm ở V3 — quyết định 16/09/2026 |

### Thiết kế
- Đi lại đường `status_push` sẵn có: V2 tính số liệu mỗi chu kỳ (hoặc lệnh
  `v2 dashboard-push`), đẩy JSON snapshot lên trang (Vercel hiện tại hoặc
  page mới cùng project). Snapshot mang `pushed_by` như bài học Việc 5a.
- **Địa chỉ & đăng nhập (C.5 ĐÃ CHỐT 16/09/2026):** dashboard chạy tại
  **`meeting.lamsonretail.com`** / **`meeting.hapas-ai.tech`** (hai domain
  cùng một app — đúng lời khuyên V2_LONGTERM §3.1: domain riêng, đổi hạ tầng
  chỉ đổi DNS). Đăng nhập bằng **Lark OAuth (web SSO)**: đang có phiên Lark
  thì vào thẳng, không hỏi lại (dùng luồng authorize của Lark — có phiên là
  redirect về ngay với code, không hiện màn hình đăng nhập); chưa có phiên
  thì qua trang đăng nhập Lark một lần.
- **Hệ quả đẹp của việc đăng nhập bằng Lark:** dashboard biết NGƯỜI XEM LÀ AI
  → phần nội dung (tên cuộc họp, digest, backlog) lọc bằng đúng
  `note_grants`/`may_see` của người đó — cùng MỘT luật quyền với bot, không
  dựng luật thứ hai. Admin (danh sách union_id trong config) thấy thêm khối
  vận hành + backlog toàn cục.
- Kỹ thuật: SSO web cần một OAuth redirect cho dashboard — đi qua đúng hạ tầng
  Cloudflare Worker đang có (thêm route), KHÔNG tạo app Lark thứ hai cho việc
  này; session dashboard là cookie ký bởi server, ánh xạ về union_id.
- Digest tuần/tháng cũng gửi DM cho danh sách nhận (tận dụng `alerts.py`
  pattern, một lần/kỳ, chống spam bằng `alert_state`).

### ✅ ĐÃ LÀM (28/09/2026) — khác spec ở kiến trúc, có lý do
- **Không đặt ở Vercel/Worker mà V2 tự phục vụ** (`python -m v2 dashboard`,
  stdlib `http.server`, chỉ nghe `127.0.0.1:8765`), hai domain trỏ vào qua
  **Cloudflare Tunnel**. Lý do: nội dung họp không rời máy, và quyền xem lọc
  bằng ĐÚNG `qa._may_see` — đặt ở edge thì phải copy dữ liệu ra ngoài và viết
  lại luật quyền bằng JS (đúng thứ "hai luật song song, cái lỏng hơn thắng").
  Tiến trình RIÊNG với `v2 run` (`run-v2-dashboard.bat` tự bật lại khi chết).
- Đăng nhập: Lark OAuth **không xin scope** (chỉ danh tính) → có phiên Lark là
  vào thẳng. Cookie phiên ký HMAC, HttpOnly + Secure + SameSite=Lax, hết hạn
  `DASHBOARD_SESSION_HOURS=12`. `state` ký + hết hạn 10 phút; Host lạ không lái
  được redirect_uri. CSP chặn script/nhúng; chỉ GET.
- Người CHƯA enroll bot vẫn đăng nhập được — thấy đúng các cuộc có quyền (vd
  CEO thấy nhánh dưới nhờ `note_grants`).
- Khối **nội dung** (tên cuộc, quyết định, rủi ro) lọc theo người xem.
  **Admin** chỉ thấy thêm SỐ LIỆU vận hành (đếm backlog, thống kê truy vấn,
  câu hỏi hay gặp) — không vì là admin mà thấy nội dung cuộc của người khác
  (giữ quyết định 04/08/2026 tách `admin` khỏi `see_all`).
- "Nội dung chính": danh sách quyết định trong kỳ (tuần/tháng, không tốn LLM)
  + đoạn tóm tắt LiteLLM (`DIGEST_MODEL`, có trích nguồn [n]) **cache theo tập
  cuộc họp người xem thấy** — người cùng nhánh dùng chung một lần gọi.
- "Rủi ro": việc đã tới hạn (theo hạn trong biên bản — hệ thống chưa biết việc
  đã xong hay chưa, nên ghi "tới hạn" chứ không kết luận "trễ"), việc chưa có
  người nhận, biên bản chưa review, chủ đề lặp ≥3 buổi mà 3 buổi cuối không
  chốt gì. "Họp kém hiệu quả": HOÃN theo quyết định.
- Giao diện theo gợi ý UI UX Pro Max: Minimalism/Swiss, mật độ cao, navy +
  amber, Fira Sans, không emoji làm icon, tương phản ≥4.5:1, focus rõ, tự
  theo light/dark của máy, không JS, không cuộn ngang ở 375px.

### Cách kiểm
1. Tạo 2 job `awaiting_confirm` (1 quá SLA) → dashboard hiện đúng 2, đúng màu.
2. 5 truy vấn thử → `query_log` 5 dòng, dashboard khớp số.
3. Sửa 1 recap khi confirm → chỉ số chất lượng đổi đúng chiều, đúng kỳ.
4. Digest tuần trên dữ liệu thật: mọi câu trong digest truy được về ≥1
   `minute_token` (chống bịa — yêu cầu digest kèm liên kết).
5. Mở trang chưa đăng nhập → redirect Lark SSO; đang có phiên Lark → vào
   thẳng không hỏi thêm; đăng nhập bằng user X → nội dung hiển thị đúng bằng
   tập `may_see(X)` (so với `v2 ask --as X`), user ngoài nhánh không thấy tên
   cuộc họp của nhánh khác; admin thấy khối vận hành.

---

## YC5 — Liên kết ngữ nghĩa giữa các cuộc họp

### Mục tiêu
Các cuộc họp cùng chủ đề/chuỗi công việc phải "biết nhau": hỏi một cuộc thì
thấy các cuộc liên quan; dashboard nhìn được chuỗi chủ đề chạy qua nhiều tuần.

### Thiết kế
- Nền là embedding YC3 (làm SAU YC3). Bảng mới
  `meeting_links(token_a, token_b, score, reason, created_at)`;
  `reason ∈ {'semantic','entity','series'}`:
  - `semantic`: cosine giữa vector recap-mức-cuộc ≥ ngưỡng (chốt bằng đo trên
    dữ liệu thật, khởi điểm 0.75);
  - `entity`: trùng người phụ trách action item / thuật ngữ glossary / tên dự án;
  - `series`: cùng tên chuỗi họp định kỳ ("Weekly …") — regex + khoảng cách thời gian.
- Tính khi job vào `confirmed` (so với các cuộc đã index) — incremental, không
  quét lại toàn bộ.
- Bề mặt: (1) `get_meeting` thêm mục "Cuộc họp liên quan" (đã lọc `may_see`
  của NGƯỜI HỎI — liên quan không phải giấy thông hành, người không đủ quyền
  chỉ thấy "còn N cuộc liên quan bạn không có quyền xem", không thấy tên);
  (2) tool `related_meetings(minute_token)`; (3) dashboard: chuỗi chủ đề +
  phát hiện "chủ đề lặp N tuần chưa có quyết định" nuôi khối rủi ro YC4.

### ✅ ĐÃ LÀM (28/09/2026)
- `v2/links.py` + bảng `meeting_links`. Tính TĂNG DẦN mỗi khi một cuộc được
  phát (`notes.publish`) hoặc index lại (`semantic.index`).
- `series`: tên chuẩn hoá (bỏ ngày/số/"Buổi N") trùng + cách ≤ 45 ngày.
  `entity`: ≥ 2 thực thể chung (người phụ trách việc cần làm, thuật ngữ đã
  duyệt trong glossary) — chung 1 người thì không nối, tránh nối mọi cuộc có
  cùng một sếp. `semantic`: vector tóm tắt ≥ `LINK_MIN_SCORE=0.75`.
- Bề mặt: `get_meeting` thêm khối "Cuộc họp liên quan"; tool MCP
  `related_meetings`; mục "Liên kết" trong file `.md`.
- **Khác spec một điểm:** KHÔNG hiện "còn N cuộc liên quan bạn không có quyền
  xem" — user đã bỏ đúng kiểu câu này ngày 04/08/2026 (`_hidden_note`: đếm
  cuộc bị ẩn là đo hoạt động công ty). Người không đủ quyền chỉ không thấy gì.
- File `.md` được nhiều người đọc → chỉ nêu cuộc liên quan mà MỌI người đọc
  file đều xem được.

### Cách kiểm
1. 3 cuộc "Weekly Ops" liên tiếp → link `series` nối đủ 3.
2. 2 cuộc khác tên cùng bàn một dự án → link `semantic` (đo ngưỡng bằng 1 cặp
   dương + 1 cặp âm thật, không chọn ngưỡng chay).
3. Người chỉ được xem 1 trong 2 cuộc: mục liên quan không lộ TÊN cuộc kia.
4. Chủ đề xuất hiện 3 tuần, không quyết định mới → lên khối "pending/rủi ro".

---

## C. Quyết định phải chốt TRƯỚC khi code (hỏi chủ hệ thống)

| # | Câu hỏi | Mặc định đề xuất |
|---|---|---|
| C.1 | ~~Owner im lặng?~~ **ĐÃ CHỐT 16/09/2026:** nhắc sau **8h** (đổi từ 4h ngày 01/10/2026); **tự phát sau 24h** kèm nhãn "chưa được chủ trì review"; owner **vẫn confirm/sửa được sau đó** → hệ thống cập nhật `.md`/Base, gỡ nhãn, báo người dự nếu nội dung đổi | — |
| C.2 | ~~Kho `.md`: Drive hay kho ảo trong V2?~~ **ĐÃ CHỐT 16/09/2026 (chủ hệ thống):** file chi tiết nằm trên **Lark Drive** theo logic YC2; Base `OuQ1b3f3JaVpTSs33SVlbWHZgef` chỉ còn làm **index**. | Còn phải làm: xin scope `drive`+`contact` MỘT lần ngay bây giờ, trước người thứ ba enroll; local `.md` vẫn ghi trước, Drive đồng bộ theo (B2.2) |
| C.3 | ~~Quyền quản lý khi org đổi? Nhánh loại trừ?~~ **ĐÃ CHỐT 16/09/2026:** đổi sếp thì quyền CŨ **vẫn giữ** (grant vật chất hoá lúc phát, không thu hồi — B2.4); **không có nhánh loại trừ**, CEO thấy tất. Folder Drive **tạo sẵn cho toàn bộ nhân sự**, share từng cá nhân, **không notify**, người mới tự tạo (B2.2b) | Còn một điểm phụ mặc định: sếp MỚI không tự có quyền cuộc cũ của đội mới — cấp lùi bằng `grants-backfill` tường minh. Nói nếu muốn khác |
| C.4 | ~~Ai cấp virtual key?~~ **ĐÃ CHỐT 16/09/2026: thienlq cấp virtual key** (01/10: gửi tin Lark để thienlq làm sau — xem §0) trên `litellm.hapas-ai.tech`; ngân sách tháng chốt với thienlq lúc cấp key (đặt limit ngay trên LiteLLM) | Còn phải làm: benchmark 3 ứng viên §B3.1 trên tiếng Việt thật rồi chốt model theo số đo; key để trong `v2/.env`, không commit |
| C.5 | ~~Dashboard: ai xem? Ngưỡng "họp kém hiệu quả"?~~ **ĐÃ CHỐT 16/09/2026:** chạy tại `meeting.lamsonretail.com` / `meeting.hapas-ai.tech`, đăng nhập **Lark SSO** (có phiên Lark → tự vào, không hỏi lại); nội dung lọc theo `may_see` của người xem. **"Họp kém hiệu quả": HOÃN, chưa cần làm** | Còn phải làm: trỏ DNS hai domain, thêm route OAuth dashboard vào Cloudflare Worker |
| C.6 | Có ghi ngược trạng thái confirm lên Base không (external write)? | **ĐÃ LÀM theo mặc định: có** — cột `Xác nhận` + `File biên bản` (YC2). Base tự thêm 2 cột ở lần ghi đầu sau khi áp code: đó là external write, chủ hệ thống duyệt lúc áp |

## D. Thứ tự triển khai & phụ thuộc

```
Chốt C.1–C.6 ──> [Giai đoạn 1] YC1 confirmation  (độc lập, giá trị ngay)
                 [Giai đoạn 1] YC2 .md + org_edges + may_see mới
                        │  (scope contact/drive xin MỘT Lần, trước người thứ 3 enroll)
                        ▼
                 [Giai đoạn 2] YC3 embedding + semantic_search   (cần ACL mới của YC2)
                        ▼
                 [Giai đoạn 3] YC5 meeting_links                  (cần vector YC3)
                        ▼
                 [Giai đoạn 4] YC4 dashboard                      (tiêu thụ dữ liệu của cả 4)
```
Dashboard làm cuối không có nghĩa chờ lâu: `confirmations`, `query_log` phải
được GHI từ giai đoạn 1–2 để lúc dashboard lên là có sử liệu.

## E. Ràng buộc kế thừa (không được phá khi làm specs này)

1. Sáu luật của `V2_VIEC_CAN_LAM.md` §2 giữ nguyên (WS đơn instance, Fernet
   key, `qa.py` chỉ đọc, stdout của gate/mcp là kênh giao thức, `.bat`
   ASCII+CRLF, không kill theo chuỗi rộng).
2. `qa.py` và mọi tool mới (semantic_search, related_meetings, get_note):
   **chỉ đọc**. Đường ghi duy nhất mới là luồng confirm của YC1 — nằm ngoài
   `qa.py`, có chủ thể rõ (owner), có bảng ghi vết.
3. `minute_viewers`, lịch gần giờ, quyền xem Base: vẫn KHÔNG cấp quyền. Nguồn
   quyền mới duy nhất được thêm: `org_edges` từ Lark Contact (YC2), có audit.
4. Bot không chủ động gửi link OAuth; không tự gửi transcript cho người chưa hỏi.
5. Mọi state mới (bảng, vector, `.md`) nằm trong `state.db` + `data/` — seam
   di trú 4.3 còn nguyên.
6. Nội dung họp là input không tin cậy → mọi chỗ đưa nó vào prompt (digest,
   áp chỉnh sửa của owner, semantic answer) kế thừa `NOTE_UNTRUSTED`.
