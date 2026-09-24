# MeetingxLark — bối cảnh hiện tại và bàn giao

> Cập nhật lần cuối: **28/08/2026, Asia/Saigon**
> Repo: `D:\MeetingxLark`  
> Đối tượng đọc: Codex/chat mới hoặc người tiếp quản vận hành.  
> **Đọc file này trước khi sửa hoặc chẩn đoán.** Khi mâu thuẫn với tài liệu cũ,
> trạng thái và quyết định trong file này được ưu tiên vì đã được kiểm tra live.
>
> Các mục `0a`–`0m` xếp theo THỜI GIAN, mới nhất ở `0m`. Mục `0` (tóm tắt một
> phút) viết từ 05/08 và chỉ nói về sự cố ACL hồi đó — đọc `0k`–`0m` để biết
> trạng thái hiện tại.
>
> **Ba bài học lặp lại nhiều lần nhất, đọc trước khi chẩn đoán bất cứ thứ gì:**
> 1. **Log trắng không phải bằng chứng.** Phần lớn lỗi ở đây là "hệ thống làm sai
>    mà không báo gì". Trước khi kết luận "không xảy ra", hỏi: nếu nó hỏng ngay
>    bây giờ, tôi có thấy dòng nào không? (27/08: tôi kết luận sai hai lần vì tin
>    vào `gateway.log` trắng, trong khi cú bỏ tin chỉ log ở mức DEBUG.)
> 2. **Đổi trạng thái CUỐI của một luồng thì phải soát mọi chỗ lọc theo trạng
>    thái cũ.** Chúng không gãy — chúng im lặng ngừng chạy (`_backfill_recaps`
>    mù với `held` gần hai tháng).
> 3. **Bước phụ hỏng phải NÓI TO.** Một cú `continue` im lặng trong
>    `chat_members` giấu suốt 6 ngày việc 24 nhóm được mời họp mà bot chưa vào.

## 0a. Sự cố V2 tắt sau restart ngày 12–13/08/2026 — đã sửa

- Windows PowerShell Operational log ghi bốn lần gọi `restart-v2.ps1` ngày
  12/08, nhưng không có crash Python, reboot/sleep hoặc Application Error tương
  ứng. Script cũ dùng `Start-Process run-v2-auto.bat` từ một shell/job tạm; khi
  job cha kết thúc, cây process con bị dọn. Máy lúc đó chỉ có Startup `.vbs`,
  không có Scheduled Task `V2_Orchestrator`, nên không có gì bật lại.
- Nay `tools/register-v2-orchestrator.ps1` tạo task do Task Scheduler trực tiếp
  sở hữu wrapper, `RestartCount=999`, interval 2 phút, không giới hạn runtime,
  không dừng khi chuyển sang pin. `restart-v2.ps1` chỉ khởi động qua task này;
  nếu đăng ký/start task hỏng thì fail rõ, không rơi về child process tạm.
- Startup item cũ đã đổi đuôi `.disabled` để tránh hai launcher đua nhau. Đã đo
  live: đúng một wrapper + một `python -m v2 run --send`, task `Running`, và
  heartbeat `v2run=1` sau khi terminal restart đã thoát.
- Sáu job `empty_transcript` cũ đã được tải/đo bằng `ffmpeg volumedetect`, không
  in transcript: bốn clip 3–37 giây không có lời hữu ích; hai track 1517s và
  2485s là im lặng số (`mean=max=-91 dB`). Cả sáu chuyển `discarded`; failed=0.
  Pipeline mới đo peak trước Whisper: silent recording đi `discarded`, còn audio
  có tín hiệu mà Whisper trả 0 chữ vẫn `failed` và cảnh báo.
- Audit nay ghi SQLite local bền vững, payload Hermes truyền qua stdin, log câu
  cuối sau lớp lọc an toàn, và Base mirror là opt-in (`BITABLE_AUDIT_TABLE_ID`
  mặc định rỗng). Plugin live là `v2-enroll-gate` **1.10.0**.
- Kiểm ngày 13/08: selftest **747 PASS / 0 FAIL**, Worker **6/6**, Doctor không
  còn lỗi chặn, MCP kết nối và thấy đủ 9 tools, gate DM/ACL chạy và group/invalid
  ticket fail-closed. Cảnh báo vận hành còn lại: backup vẫn cùng ổ `D:`.
- Task tạm `V2_ApplyUpdateWhenIdle` đã được gỡ sau khi áp bản vá thủ công, tránh
  một lần restart bất ngờ lúc queue rỗng. Chỉ watchdog chính `V2_Orchestrator`
  còn hoạt động.

## 0b. Bản vá câu từ chối, nguồn bản dịch và reset chat — đã áp live 13/08 21:24

- Khi không tìm thấy cuộc trong phần user có quyền xem, Python dựng sẵn đúng một
  câu: `Mình chưa tìm thấy cuộc “…” trong các cuộc họp bạn có quyền xem.` Plugin
  phải chép nguyên câu này; không được nối ký ức cũ như `Workforce`, không hướng
  người dùng sang quản trị hệ thống và không lộ gợi ý về cuộc bị ẩn.
- Quyết định mới nhất lúc 22:21 thay thế riêng luật mời Hapas: sau **mọi cuộc** bot
  phải hỏi người dùng có muốn lấy bản dịch chuẩn từ Hapas không. Nếu file chưa có,
  chỉ khi họ đồng ý mới nâng ưu tiên xử lý và tự gửi khi xong; file đã có thì mời
  nhận ngay. Phân loại DB vẫn giữ nguyên để không nói nhầm bản chưa có là đã sẵn sàng.
- Phân loại nguồn dùng trạng thái `jobs` trong SQLite: Hapas có file thật, Lark có
  dữ liệu/link ở trạng thái đọc được, còn lại là chưa đọc được bản nào. Nhãn và lời
  giải thích của ba nhóm không trộn hai nguồn và chỉ phát một tin.
- `/reset`, `/new` và alias `/lammoi` tạo phiên Hermes mới; khi là lệnh chủ động của
  user, plugin còn gọi `python -m v2 chat-reset --union-id ...` để xóa neo cuộc họp
  trong `chat_memory`. OAuth, ACL, job và biên bản không bị xóa.
- Code + test mới nhất: `python -m v2 selftest` = **747 PASS / 0 FAIL**. Plugin
  live là **1.10.0**; config live đã nhận policy mới, Hermes và V2 đã restart.
- Thẻ `Họp xong` bỏ hẳn dòng cuối “Nguồn mặc định: Meeting Note của Lark”. Thay
  bằng câu hỏi Hapas trong mọi trường hợp và nút lấy bản chuẩn. Nút đi qua đúng
  gate, ACL và `send_transcript_file`, nên chưa có sự đồng ý thì không xử lý/gửi.
- Kiểm live 22:23: đúng một V2 process, Scheduled Task `Running`, scanner quét
  thành công lúc 22:23:00, heartbeat mã 0; gateway sống và plugin 1.10.0 enabled.
  Bản config trước thay đổi được giữ ở
  `config.pre-hapas-offer-20260813-222207.yaml`.

### 0b.1 Vì sao cuộc mới nhất không tự gửi — phát hiện thêm lúc 21:04

- Phát tự động không bị gỡ: `enqueue_minute(..., notify=True)` vẫn gọi
  `_notify_minute`, dùng Meeting Note Lark và gửi thẻ cho người dự đã enroll.
  Nhưng lời gọi này chỉ xảy ra sau khi scanner đưa cuộc vào DB.
- Đo live: heartbeat tăng từ `scan=27m` tới **`scan=122m`** trong khi 28 job dài
  đang được xử lý; monitor vẫn báo `OK` vì luật cũ miễn cảnh báo scan cũ khi còn
  backlog. Quét thử-khô 14 ngày thấy 6 cuộc chưa vào DB, gồm
  `08-13 | Workforce AI Weekly Meeting`. Vì chưa có job nên cuộc này không thể
  phát tự động và chatbot cũng không thể tra đúng nó.
- Nguyên nhân code: `scan_once()` và `process_queue()` cùng một thread;
  `process_queue()` duyệt hết snapshot hàng đợi trước khi trả về vòng quét kế.
- Bản vá thêm daemon `v2-scan-watch`: scanner chạy đúng nhịp kể cả khi worker
  đang tải/ffmpeg/whisper. `_scan_lock` chặn quét chồng; heartbeat nay luôn cảnh
  báo scan quá 20 phút, không để backlog che lỗi. Hai test mới khóa đúng ca này.
- Đã backup DB thành `state-2026-08-13-211703.db`, rồi nạp đủ 6 cuộc bằng
  `backfill --priority 0 --yes`; backfill xác nhận **không gửi tin cho ai**. Sau
  restart, scanner quét lại lúc 21:24:57 và heartbeat trả mã 0.
- Cuộc mới nhất `08-13 | Workforce AI Weekly Meeting` hiện ở DB với
  `status=queued`, `priority=1`, nguồn `calendar[verified]`, Meeting Note Lark đọc
  được (82.061 ký tự), chưa có file Hapas. Sau xác nhận riêng của người dùng, thẻ
  nguồn Lark đã gửi thành công **8/8** người dự đã enroll, lỗi 0; 15 người chưa
  enroll không nhận. Khóa chống trùng là `notice-<token>-<union>`.
- Scanner độc lập đã tiếp tục quét đều khoảng 5 phút/lần trong lúc worker còn bận:
  21:35, 21:40, 21:45, 21:50 và 21:55 đều thành công. Heartbeat trả mã 0,
  `V2_Orchestrator` vẫn `Running`, đúng một process; task cập nhật tạm không còn.

## 0c. Hai regression trong chat ngày 14/08 — đã áp live 10:14

Ca thật 09:46–09:47 của Nguyễn Tiến Thẩm:

1. Hỏi `cuộc họp workforce AI mới nhất có bản dịch từ hapas chưa` nhưng bot trả
   cuộc `08-06`, dù DB có cuộc `08-13` mới hơn.
2. Sau khi bot xác nhận `08-13` và hỏi có gửi file Word không, user đáp `có` thì
   `send_transcript_file` bị gate chặn, rồi câu trả lời bị thay bằng
   `_NO_TOOL_REPLY`.

Audit Hermes xác nhận cùng một session, không phải mất phiên:

- lượt đầu agent gọi thẳng `get_meeting` bằng minute token `08-06` lấy từ
  `chat_memory`, không gọi `search_meetings`;
- lượt `13/8` gọi `search_meetings`, kết quả thật có `OFFER_MARK` và agent chọn
  đúng minute token `08-13`;
- lượt `có` agent gọi đúng `send_transcript_file` cho token `08-13`, nhưng plugin
  đã xoá cờ lời mời nên chặn write tool.

Hai nguyên nhân và bản vá:

- `chat_memory` sắp theo lúc cuộc được **nhắc gần đây**, không theo thời gian
  cuộc họp, nhưng prompt cũ gọi mơ hồ là “mới nhất trước”. Nay câu
  `mới nhất/gần nhất` không được bơm memory cũ; plugin còn chặn `get_meeting`
  cho tới khi `search_meetings` chạy thành công trong chính lượt đó.
- Hermes hiện phát hook tên `on_session_end` sau mỗi `run_conversation` (mỗi
  tin nhắn). Plugin 1.10 hiểu nhầm là cuối phiên và đăng ký `_cleanup_session`,
  làm mất `_asked/_offered` ngay sau câu mời Hapas. Plugin 1.11 đăng ký hook này
  vào `_cleanup_turn`; chỉ `on_session_finalize/reset` mới xoá trạng thái phiên.

Self-test mới kiểm cả bảng đăng ký hook và chuỗi
`mới nhất → search → get đúng kết quả → mời Hapas → on_session_end → có → gửi`.
Kết quả code hiện tại: **753 PASS / 0 FAIL**, compile và `git diff --check` sạch.

Đã được người dùng duyệt và áp live lúc 10:13–10:14 ngày 14/08:

- backup config ở
  `config.pre-v2-enroll-gate-1.11.0-20260814-101334.yaml`; backup plugin 1.10 ở
  `backups/v2-enroll-gate-20260814-101334/` trong `%LOCALAPPDATA%\hermes`;
- plugin live là **1.11.0**, hai file live có SHA-256 khớp bản gốc trong repo;
  `feishu-config.yaml` đã deep-merge và giữ các khóa live ngoài phạm vi;
- gateway dừng sạch, restart lúc 10:14, Feishu websocket và API server cổng 8642
  đều kết nối lại; đúng một logical gateway parent/child; `meetings` kết nối và
  thấy đủ **9 tools**;
- `python -m v2 doctor` chạy được; cảnh báo vận hành duy nhất vẫn là backup DB
  cùng ổ `D:`. Không gửi tin nhắn thử nào ra Lark trong lúc triển khai.

## 0d. Regression tiếp theo: search-first vẫn gửi nhầm file 06/08 — đã áp live 13:54

Ca thật 13:34 ngày 14/08: user xin `biên bản cuộc họp mới nhất của mình từ
server Hapas`, nhưng bot gửi file của `work` ngày 06/08. Audit xác nhận agent làm
ba bước: `search_meetings("")` bị trả `Cần từ khoá`; agent tự thay bằng từ chung
chung `họp`; kết quả chỉ giữ các record có chữ đó trong tên/nội dung và đứng đầu
là `work`; sau đó `send_transcript_file` gửi đúng token sai mà agent vừa chọn.

Vì vậy plugin 1.11 chỉ chặn được token lấy thẳng từ memory, chưa chặn được hai lỗ:

1. `search_meetings` là tìm TỪ KHÓA, không phải resolver cuộc mới nhất. Từ `họp`
   còn loại mất tiêu đề tiếng Anh `08-13 | Workforce AI Weekly Meeting`.
2. Gate chỉ kiểm “đã search thành công”, không ràng buộc token tool gửi file với
   record mới nhất. Agent chọn sai sau search vẫn được phép gửi.

Bản code 1.12 thêm tool đọc `latest_meeting`: backend lọc ACL, gộp record/pending,
sắp bằng thời gian cuộc họp và trả đúng một record. `họp`/`meeting` được coi là
từ chung chung và không lọc. Kết quả mang marker nội bộ chứa token; plugin ghi
token đó rồi chặn `get_meeting`, `get_transcript`, `create_task` và
`send_transcript_file` nếu agent dùng token khác. Trước khi có marker, cả
list/search/read/send cho yêu cầu “mới nhất” đều bị chặn và agent được yêu cầu gọi
`latest_meeting`.

Regression mô phỏng đúng ca live: record cũ chứa chữ `họp`, record mới có tiêu đề
tiếng Anh không chứa chữ đó; backend vẫn chọn record mới, token marker không lộ ra
kênh người dùng, gửi token cũ bị chặn và gửi đúng token mới được qua. Kết quả:
**761 PASS / 0 FAIL**, compile + diff-check sạch, MCP mới thấy **10 tools**.
Đo DB live không đọc transcript: `08-13` lúc 18:05 có file Hapas thật và mới hơn
`08-06 Workforce`/`work`.

Đã áp live lúc 13:53–13:54 sau khi user duyệt: config cũ được giữ tại
`config.pre-v2-enroll-gate-1.12.0-20260814-135334.yaml`, plugin cũ tại
`backups/v2-enroll-gate-20260814-135334/`. Plugin live là **1.12.0**, config đã
deep-merge policy `latest_meeting`; gateway dừng sạch rồi lên PID 33820, trạng
thái `running`, Feishu + API đều `connected`. MCP discover **10 tools** và
self-test sau triển khai **761 PASS / 0 FAIL**. Doctor chạy được; cảnh báo duy
nhất vẫn là backup cùng ổ `D:`. Không gửi tin thử ra Lark.
Phép kiểm chỉ-đọc trên đúng ACL live của user: cả keyword rỗng và từ chung `họp`
đều chọn `08-13`; token `work` 06/08 bị loại.

## 0e. Phiên 18/08/2026 — cuộc họp rơi khỏi hệ thống, và ba lỗi phân loại

Ca thật: cuộc `Chat bot Nhân sự` (chủ trì Vũ Thị Thu Hiền - GĐ Nhân Sự, 16:00
18/08, minute `obsgh6g3m9fu3w542u9lw5l9`) có ghi hình, chủ bản ghi đã enroll, mà
không ai được thông báo và chatbot cũng không tra ra. Không liên quan auth, cũng
không liên quan "cuộc cũ".

**Gốc:** scanner tìm cuộc bằng `minutes/search` lọc `participant_ids`. Đo cả 22
người đã enroll: bản ghi đó KHÔNG có trong danh sách lọc-theo-người của bất kỳ ai
— kể cả chính chủ bản ghi (`p=0`). Nó chỉ hiện ở danh sách bỏ-lọc-người của chị
Hiền. Cờ `V2_SCAN_ALL_VISIBLE` (nguồn thứ hai, `orchestrator.scan_once`) đang TẮT
nên V2 mù hoàn toàn. Đo rộng 7 ngày: **49/53 bản ghi có người dự được Lark ghi
nhận, 4/53 (~8%) thì không** — tức đây không phải ca hiếm.

**Đã làm:** nạp tay cuộc đó (thẻ tới 3/3 người đã enroll, nay `held`, có
transcript); user chốt **bật lại `V2_SCAN_ALL_VISIBLE=true`** trong `v2/.env` dù
`config.py` ghi rõ cờ này từng bật rồi tắt trong cùng ngày 04/08. `.env` ngoài
git — xem `v2/.env.bak-20260818-scanall` để biết bản trước.

Sáu phút sau khi bật, cái giá hiện ra: vòng quét vớt 3 bản ghi cũ và **gửi 5 thẻ
"họp xong" cho cuộc đã họp 5–6 ngày trước**. Ba lỗi được vá trong phiên, tất cả
cùng một hình dạng — **xếp sai giỏ / cắt sai đầu rồi im lặng kết luận sai**:

1. **Thẻ "họp xong" cho cuộc cũ.** Đường quét không biết tuổi cuộc họp và để
   `notify=True` mặc định. Thêm `NOTIFY_MAX_AGE_HOURS` (6h) + chần tuổi trong
   `enqueue_minute` — chỗ duy nhất có `meta.start` thật, nên che cả ca bản ghi cũ
   được share muộn. Quá tuổi thì VẪN nạp và phiên âm, chỉ thôi báo. Thiếu `start`
   thì fail-open. Kiểm live: cuộc họp 18:04 phát hiện 19:03 vẫn báo 1/1 người.
2. **Một cú rate limit biến job đáng-park thành `failed` vĩnh viễn.** `99991400`
   về kèm HTTP 200 nên `_RetryTransport` không đỡ; nó rơi vào giỏ `other` làm
   `denied and not other` sai → lỗi chung → đốt 5 lần thử. Job thật: `08-17 | HỌP
   ĐỊNH KÌ THỨ 2` (56 người) mà sự thật chỉ là chủ bản ghi (Nguyễn Thảo Phương)
   chưa enroll. Thêm giỏ `throttled` + lớp `RateLimited` + orchestrator
   `unbump_attempts`. **Tầng sâu hơn:** cú bóp KHÔNG tự khỏi vòng sau — chính
   thang ứng viên gọi `minutes_media_url` ~19 lần liên tiếp rồi tự đụng hạn mức,
   nên "thử lại vòng sau" = job nằm `queued` vô hạn mà không ai báo. Nay có lượt
   hai sau `MEDIA_THROTTLE_BACKOFF_S` (5s). Live: hỏi lại xong job về
   `waiting_auth` đúng chỗ, `hong_can_sua` 1 → 0.
3. **`restart-v2.ps1` chặn restart 5 ngày vì ĐẾM file trong `work/`.** File 238 MB
   đọng từ 13/08 của một job đã `held` làm MỌI lần restart từ 13→18/08 bị chính lá
   chắn đó huỷ (exit 3) — bản vá nào cũng không vào được máy, im lặng. Nay hỏi
   `v2.bat workfiles` (`pipeline.work_files`): chỉ `queued/transcribing/recapping`
   là đang dùng thật (`queued` phải tính — tải xong mới đặt `transcribing`).
   Python hỏng thì fail-closed về hỏi người. KHÔNG dùng `2>&1` trên native exe
   trong file đó: PS 5.1 bọc stderr thành ErrorRecord, gặp
   `$ErrorActionPreference='Stop'` là chết chính script.

**Lỗi thứ tư, lòi ra khi kiểm luồng lần cuối:** `v2 ask` không bao giờ thấy cuộc
mới. `qa.context` gọi `records(limit=50)`, mà `limit` cắt phía Base trả về TRƯỚC,
tức phía cũ nhất; với 255 record thì nó trả lời bằng cụm 50 cuộc cũ nhất và gọi
một cuộc 30/07 là "gần nhất". Ba đường MCP đều lấy cả Base rồi sắp mới-trước; nay
`context` làm y vậy rồi mới cắt. **Đường bot thật không bị lỗi này** — đã kiểm
`qa.search_meetings` trước khi vá và nó trả đúng cuộc, ACL ẩn 243/255 record.
`v2 ask` và bot khác nguồn: đừng lấy `ask` làm chuẩn kiểm luồng.

Kiểm thử: `selftest` **783 PASS / 0 FAIL** (thêm 12 test cho các ca trên).
`doctor` chỉ còn cảnh báo cũ: backup cùng ổ `D:`. Heartbeat OK, đúng một
orchestrator, task `Running`, `failed=0`.

Việc còn mở: cuộc `08-17` toàn công ty nằm `waiting_auth` tới khi **Nguyễn Thảo
Phương (L&C & HR Ops Manager)** cấp quyền; file rác 238 MB đã dẹp sang
`v2/data/work-stale/`, xoá được.

## 0f. Phiên 19/08/2026 — nguồn quét theo CHỦ bản ghi, và tắt lại cờ quét rộng

User hỏi một câu làm lộ chỗ tối qua làm chưa tới: *"Chat bot Nhân sự thì chị Hiền
là chủ mà?"* Đúng — chị là chủ bản ghi VÀ đã enroll. Vấn đề là scanner chỉ lọc theo
`participant_ids`, mà Lark KHÔNG xếp chủ bản ghi vào danh sách người dự của chính
bản ghi họ tạo. Tối qua tôi bật `SCAN_ALL_VISIBLE` vì nó là nguồn thứ hai *có sẵn
trong code*, không thử xem API còn bộ lọc nào hẹp hơn.

Có: `owner_ids`. Đo 8 ngày trên 22 người đã enroll:

| Nguồn | Số bản ghi |
|---|---|
| Không lọc (`SCAN_ALL_VISIBLE`) | **80** |
| Lọc `participant_ids` | 74 |
| Lọc `owner_ids` | 72 |
| Người dự HOẶC chủ | **80** |

Cả 6 bản ghi mà đường người-dự bỏ sót (`Chat bot Nhân sự`, `Finance weekly`,
`Họp CĐS`, hai cuộc phỏng vấn, một cuộc 11/08 ngoài `LOOKBACK_DAYS`) đều được lượt
chủ vớt về; **0** bản ghi còn cần đường rộng.

Nên: thêm **nguồn (1b)** vào `orchestrator.scan_once` (lọc `owner_ids` cho từng
người enroll), xếp CÙNG HẠNG với nguồn (1) — có cơ sở vì viewer vẫn chỉ được ghi
khi `_explicit_viewer` xác nhận qua metadata, và `meta.owner_open_id` đến từ
`minutes_get`, độc lập với bộ lọc. Không luật ACL mới. Rồi **TẮT lại**
`V2_SCAN_ALL_VISIBLE` trong `v2/.env` (dòng bị comment, giữ lý do).

Kiểm live sau restart: log có `+1 minute do CHÍNH họ làm CHỦ` cho đúng chị Hiền,
0 dòng `MỞ XEM ĐƯỢC`, heartbeat OK, `failed=0`. `selftest` **786 PASS / 0 FAIL**.

**⚠️ Bẫy phải nhớ khi thêm bộ lọc mới cho `minutes/search`:** Lark ÂM THẦM BỎ QUA
khoá filter nó không biết. Đo 19/08: `owner_id`, `creator_ids`, `user_ids` đều trả
`code=0` và ra y hệt danh sách KHÔNG lọc — chỉ `owner_ids` mới thật sự lọc. `code=0`
KHÔNG chứng minh bộ lọc có tác dụng; phải kiểm nó THU HẸP kết quả, không thì tưởng
đang lọc mà thực ra đang quét mở toang, im lặng.

## 0g. Phiên 19/08/2026 (sáng) — chọn nguồn bản dịch, và luật người nhận giữ nguyên

**Ca thật 07:45.** User hỏi *"phân tích qua bản dịch từ servẻr của hapas"*, bot đáp
*"Mình chưa thể phân tích trực tiếp từ bản dịch server của Hapas"*. Bot nói ĐÚNG:
`qa.get_transcript` đóng cứng thứ tự nguồn (Lark trước, chỉ rơi xuống Hapas khi
Lark không đọc được) và tool không có tham số nào chọn nguồn — bản Hapas chỉ ra
ngoài dưới dạng file Word mà agent không đọc nổi. Thiếu ĐƯỜNG, không phải agent kém.

**Đã làm (phần 1+2 của đánh giá; phần 3 chưa làm):**

- `qa.get_transcript(..., source=)`: `""`/`"lark"` = như cũ (mặc định Lark, giữ
  quyết định 10/08 của anh Thiện); `"hapas"` = trả đúng bản chuẩn và **không** lặng
  lẽ rơi về Lark. Giá trị lạ thì fail-soft về mặc định. Chưa có file Hapas thì nói
  tình trạng, tuyệt đối không tráo bản Lark vào.
- Bản Hapas nay có dòng `- Bản: **bản dịch từ server của Hapas** (bản chuẩn)`, để
  agent khỏi nói "faster-whisper/medium" ra chat.
- MCP: `source` vào `inputSchema` (`enum: [lark, hapas]`) + mô tả dạy khi nào dùng.
- Plugin `_POLICY`: mục **HỌ CHỈ ĐỊNH NGUỒN THÌ PHẢI ĐỌC ĐÚNG NGUỒN ĐÓ** và luật
  **MỘT NGUỒN MỘT LƯỢT** (hai bản gộp ~64.000 ký tự — Hapas 33.980 / Lark 29.991
  cho cuộc `Chat bot Nhân sự`, tức 7 + 5 phần). `POLICY_VERSION` -> `2026-08-19.1`,
  plugin -> **1.13.0**, đã cài sang `%LOCALAPPDATA%\hermes\plugins` và restart gateway.

Kiểm live qua đúng vé gate: mặc định ra bản Lark `Phần 1/5`; `source=hapas` ra
`bản dịch từ server của Hapas` `Phần 1/7`. `selftest` **796 PASS / 0 FAIL** (+10).

**Phần 3 chưa làm — người dùng gửi file lên cho bot bàn luận.** Plugin hiện KHÔNG
có đường nhận file: không một chỗ nào xử lý `file_key`/`media_key`. Cần sửa phía
Hermes (nhận message dạng file -> tải qua Lark API -> đưa vào ngữ cảnh phiên), kèm
giới hạn định dạng/kích thước và rào chặn đường vòng ACL: bot chỉ được phân tích
CHÍNH file đó, không được trộn với dữ liệu họp người ta không có quyền xem.

**Luật người nhận: GIỮ NGUYÊN (user chốt).** `_recipients` = `meta.attendees` giao
người đã enroll; quyền xem bản ghi trên Lark không được hỏi tới, chỉ loại người bấm
Từ chối và phòng họp. Hệ quả đã được nêu rõ trước khi chốt: người còn tên trên lời
mời mà bị cố ý không cho xem bản ghi vẫn nhận đủ biên bản — kể cả cuộc `Phỏng vấn`.

## 0h. Phiên 19/08/2026 (trưa) — nút thẻ là đồ trang trí, và săn đường chưa từng chạy

**User báo: "nút nhận bản Hapas mình ấn không được".** Đúng, và nó hỏng ở HAI chỗ
độc lập — nút đó là đồ trang trí từ 13/08 tới 19/08:

1. **Cú bấm chưa từng tới Hermes.** Tìm trong MỌI log của Hermes (`gateway.log`,
   `agent.log`, `gateway-stdio.log` 14,7 MB, `errors.log`): **0 dòng** `card action`
   / `Routing card` / `/card`. Adapter Feishu CÓ `register_p2_card_action_trigger`
   và CÓ hàm dựng `/card button {json}` (đúng dòng 3038 như tài liệu ghi), nhưng
   chưa được gọi lần nào → Lark không đẩy `card.action.trigger` về app. Nghi do
   event chưa bật trong Lark Console; **không kiểm được từ máy**, cần mở Console.
2. **Kể cả tới được thì vẫn bị bỏ.** Adapter gọi
   `_resolve_source_chat_type(chat_info, event_chat_type="group")` — GHIM CỨNG cho
   card action — mà hàm đó chỉ trả `"dm"` khi tham số ấy là `"p2p"`. Nên cú bấm
   trong chat 1-1 bị dán nhãn `group`, và plugin bỏ đúng theo luật chỉ-trả-lời-DM.

Tài liệu 13/08 ghi "Nút đi qua đúng gate, ACL và send_transcript_file" — câu đó
CHƯA TỪNG được kiểm live. Nay **bỏ nút** (user chốt): thẻ vẫn hỏi, và nói rõ
"trả lời **có**" — đường gõ chữ đã chạy thật (07:41 sáng nay). Lý do bỏ thay vì
sửa: nút phụ thuộc hai thứ NGOÀI repo (event Console + nội bộ adapter Hermes),
không test nào phủ được, và hỏng thì im lặng. `selftest` nay CHẶN việc dựng lại
nút callback; thẻ enroll vẫn được có nút vì `open_url` không cần callback.

### Săn các đường CHƯA TỪNG chạy thật (dữ liệu, không phỏng đoán)

| Đường | Số lần chạy thật | Ghi chú |
|---|---|---|
| `search_meetings` / `get_transcript` | 82 / 82 | đường chính |
| `get_meeting` / `list_meetings` / `latest_meeting` | 29 / 27 / 10 | |
| `send_transcript_file` | 8 | |
| `glossary_pending` / `glossary_approve` | 1 / 1 | mỏng |
| **`create_task`** | **0** | tạo việc trên Lark Task — CHƯA TỪNG chạy |
| **`glossary_reject`** | **0** | |
| **nút callback trên thẻ** | **0** | đã bỏ |
| "hứa tự gửi khi xong" (`transcript_requests`) | **1** (11/08) | bảng rỗng vì XOÁ sau khi gửi — không phải code chết |

Bài học ghi lại: **bảng rỗng không chứng minh đường chết** — phải tìm dấu trong log
(`CỰC CAO -> gửi transcript`) trước khi kết luận.

### Cảnh báo mới cho `doctor`: lệch bản LIVE ngoài repo

`_check_live_plugin` so sha256 `__init__.py` + `plugin.yaml` giữa repo và
`%LOCALAPPDATA%\hermes\plugins2-enroll-gate`. Vì sao cần: hôm nay
`install-plugin.bat` in `Access is denied` giữa lúc cài. Lần đó cả hai file vẫn
sang được, nhưng nếu chỉ `plugin.yaml` sang mà `__init__.py` thì không, hệ thống
báo "1.13.0" trong khi luật vẫn là luật cũ — và không ai biết. 3 test phủ: chưa
cài → WARN; khớp → OK; **yaml mới + code cũ → WARN LỆCH**.

`selftest` **801 PASS / 0 FAIL**. Heartbeat OK, doctor chỉ còn cảnh báo backup
cùng ổ `D:`.

## 0i. Phiên 19/08/2026 (09:00) — heartbeat WARN: hai báo động GIẢ, một chặn oan

User báo "heartbeat có vấn đề". Đúng, nhưng không phải V2 hỏng — là **hệ đo** sai
ở hai chỗ, và phía sau còn một cổng chặn oan. Cả ba đều đã vá.

### 1. `gwerr15m=2` — cổng chặn đúng nhưng ghi ở mức ERROR

08:50 có hai dòng `[v2-gate] CHẶN write tool không do user yêu cầu:
send_transcript_file`. Cổng làm ĐÚNG việc của nó, nhưng `logger.error` khiến
`tools/heartbeat.py` đếm vào `gwerr15m` → WARN ba nhịp liền. Nay là `logger.info`.
Báo động phải có nghĩa, không thì người vận hành học cách bỏ qua nó.

### 2. Cổng chặn oan: chỉ dò DANH TỪ, không dò ĐỘNG TỪ

08:50:42 user nhắn `Gửi cho tôi chatbot nhân sự` — rõ ràng xin gửi — nhưng câu đó
không chứa danh từ nào trong `_TRANSCRIPT_NOUNS`, nên bị chặn và bot bắt gõ lại
đúng khuôn `Gửi file Word bản dịch cuộc họp ...`. Chính chú thích trong plugin gọi
việc đó là "đẩy cái dở của backend ra thành việc của họ", và trái luật user chốt
10/08 (cách nói vô hạn, cổng phải mặc định CHO QUA).

Thêm `_ASK_TO_SEND = ("gui", "tai ve", "tai xuong", "download")`. **Chỉ động từ,
KHÔNG lấy đại từ**: bản đầu có `cho minh`/`cho toi` và làm câu "cho mình cuộc 2"
lọt cổng — phá luật CẢ HAI VẾ (phải có lời mời VÀ có câu đáp) ở đường 3/4, tức
agent tự mời rồi tự coi là được đồng ý. Test cũ bắt được ngay. Plugin -> **1.14.0**.

### 3. `scan_cu=1440.0m` — một cú đua MỘT GIÂY thành "scanner chết 24 giờ"

07:19 heartbeat báo vòng quét cũ 24 giờ. Sự thật: cả đêm `scan` chỉ 0,2–4,3m, và
40 phút sau lại 1,2m — **đúng một mẫu** sai. Gốc: `scan_age_min` regex
`[HH:MM:SS] scan xong` trong log, **không có NGÀY**, rồi:

```python
if t > now: t -= timedelta(days=1)     # coi "sớm hơn now" là HÔM QUA
```

Scanner ghi thêm dòng `[07:19:01]` trong lúc heartbeat đã chụp `now = 07:19:00.9`
→ "tương lai 1 giây" → trừ trọn một ngày → 1439,98 ≈ **1440,0m**.

Vá hai lớp: (a) `alerts.note_scan_ok()` ghi mốc `scan_ok` epoch ms vào
`alert_state`, gọi ở cuối `scan_once()`; (b) `heartbeat.scan_age_min` đọc MỐC
trước, log chỉ còn là dự phòng, và đường log không còn biến lệch ≤300s thành một
ngày. Heartbeat KHÔNG import `v2.*` (phải chạy được cả khi V2 hỏng) nên nó đọc
thẳng bảng `alert_state`; tên khoá `scan_ok` là hợp đồng giữa hai bên.

5 test mới cho riêng ca này, gồm "lệch 1 giây về tương lai → tuổi 0" VÀ "log hôm
qua lệch 2 giờ → vẫn ~22 giờ" (đừng vá cái này mà làm mù cái kia).

### Tin tốt từ audit sáng nay

`create_task` **chạy lần đầu tiên** (08:55) — trước đó 0 lần. `source=hapas` đọc
đủ 7/7 phần với nhãn `bản dịch từ server của Hapas`, và user còn so sánh được chất
lượng hai bản dịch trong cùng một phiên.

`selftest` **812 PASS / 0 FAIL**. Heartbeat OK, `gwerr15m=0`, mốc `scan_ok` chạy.

## 0j. Phiên 25/08/2026 (10:20) — luật ĐỘ DÀI chặn oan câu "bạn làm được gì"

User hỏi "sao đây" khi heartbeat 10:12 báo `WARN` vì `gwerr15m=1`. Cùng hình dạng
với §0i: **hệ đo bật báo động vì cổng làm đúng việc**, và phía sau là một cú chặn
oan thật.

**Ca thật.** Nguyễn Nam Khánh - TN Facebook Ads enroll xong 09:56, hỏi hai lượt
liền `Ngoài ra bạn làm được tất cả những gì` (09:57:16) rồi `ý là bạn làm được
những công việc gì` (09:57:44). Đó là câu hỏi VỀ NĂNG LỰC BOT, không cần tra một
dòng dữ liệu nào. Cả hai lượt đều bị `_claims_data` chặn và thay bằng
`_NO_TOOL_REPLY` ("mình phải tra dữ liệu mới dám trả lời"). Người dùng mới toanh,
phút thứ hai dùng bot.

**Gốc:** luật thứ năm của `_claims_data` — `len(body) > 600` **VÀ** có chữ trong
`_BUSINESS_HINTS`. Một đoạn bot tự giới thiệu năng lực thì đương nhiên vừa dài vừa
nhắc "cuộc họp/biên bản/file" nên tự dính. Bốn luật kia đều là **dấu vết cụ thể**
(ngày giờ, số + danh từ nghiệp vụ, link, nhãn máy) — thứ chỉ tool mới cấp được;
riêng luật này đo ĐỘ DÀI, tức đoán mò, và mặc định CHẶN khi câu trả lời dài. Ngược
đúng nguyên tắc user chốt 10/08 (đo câu trả lời, mặc định cho qua) — cùng họ với
bản `_claims_data` đầu tiên đoán từ câu hỏi, và với §0i.2.

**Đã làm (plugin -> 1.15.0, đã áp live 10:25):**

- Bỏ hẳn luật độ dài; xoá `_NO_DATA_MAX_CHARS`. `_BUSINESS_HINTS` GIỮ LẠI vì cổng
  write-tool còn dùng. Giá phải trả, ghi rõ thành lỗ (3) trong docstring: bài dài
  kể chuyện cuộc họp mà không có số/ngày/link/nhãn máy thì nay LỌT — có test khoá
  cả lỗ đó lẫn ca "thêm một con số vào chính bài đó thì chặn lại ngay".
- Ba cú chặn còn lại của cổng hạ từ `logger.error` xuống **`logger.warning`**
  (`CHẶN … trước latest_meeting`, `CHẶN … sai token cuộc mới nhất`, `CHẶN câu
  khẳng định dữ liệu`). §0i.1 hạ cú chặn write-tool xuống `info`; chỗ này cố ý
  dừng ở `warning` — ba ca này là dấu hiệu agent đang cư xử sai, cần soi được khi
  nổ hàng loạt, chỉ không được tính là sự cố hệ thống. `heartbeat.real_errors`
  chỉ đếm dòng có chữ `ERROR` nên cả hai mức đều hết báo động giả.
- Test: `selftest` **816 PASS / 0 FAIL** (+4 test cho ca này, trong nhóm 34j),
  gồm một test đọc mã nguồn plugin để chặn việc lặng lẽ đưa `logger.error` trở lại.

**Kiểm live sau khi áp:** backup bản 1.14 ở
`%LOCALAPPDATA%\hermes\backups\v2-enroll-gate-20260825-102432\`; hai file live có
SHA-256 khớp repo; gateway dừng sạch rồi lên lại 10:25 (feishu websocket +
api_server 8642 đều connected, 43 channel), đúng một logical gateway, MCP `v2 mcp`
respawn. **V2 orchestrator KHÔNG bị đụng** (vẫn PID cũ từ 20/08). `doctor`:
`plugin Hermes khớp repo — phiên bản 1.15.0`, cảnh báo duy nhất vẫn là backup cùng
ổ `D:`. Không gửi tin thử nào ra Lark; hàm `_claims_data` của **bản live** được
gọi trực tiếp để kiểm 1 ca cho-qua + 4 ca phải-chặn.

## 0k. Phiên 26/08/2026 — recap RỖNG được coi là xong, và đường tự vá mù với `held`

**Ca thật.** Codex trả `HTTP 400 — model 'gpt-5.6-sol' not supported when using
Codex with a ChatGPT account` rải rác 16:44–17:36. Ba cuộc rơi vào cửa sổ đó có
thẻ "họp xong" đi ra với **phần tóm tắt trống**: `08-26 | HỌP ĐỊNH KỲ DỰ ÁN 20.10
HAPAS` (7 người), `CĐS: Training` (3), `Overview IDI tết` (2) — 12 người nhận thẻ
rỗng.

**Hai lỗ, cùng một sự cố:**

1. Vài lượt Codex trả *thành công* với nội dung rỗng. `_parse` dựng
   `Recap(summary="")`, pipeline coi là xong -> job `held`, ghi Base, thẻ đi ra,
   `recap_fails` = 0 suốt. Lớp bảo vệ 31/07 chỉ bắt `RecapUnavailable`, tức chỉ
   bắt lỗi MẠNG — nó bỏ lọt đúng cái nguy hiểm hơn: một câu trả lời hợp lệ mà
   rỗng. Nay `summarize._reject_empty` quy cả hai về cùng một loại lỗi.
   `recap_from_text` vẫn KHÔNG BAO GIỜ ném (thẻ phải gửi được), nhưng ra CÂU GIỮ
   CHỖ NÓI RÕ LÝ DO thay vì ô trống — để nó mang dấu vết của một lần hỏng.
2. **Tệ hơn:** `_backfill_recaps` viết 02/08, chỉ quét `by_status("delivered")`.
   Mô hình KÉO (03/08) đổi trạng thái cuối thành `held` mà không ai sửa chỗ lọc.
   Nó không gãy — nó **im lặng ngừng chạy** gần hai tháng. Đo 27/08: **7 cuộc có
   transcript mà tóm tắt rỗng, TẤT CẢ đều `held`**, cũ nhất từ 16/07. Nay quét cả
   hai, và trả về ĐÚNG trạng thái cũ kèm đúng cột thời gian (trả nhầm `held`
   thành `delivered` là nói dối rằng đã phát; ngược lại là phát lần hai).

**Đã làm:** ba cuộc hỏng được dựng lại tóm tắt từ nguyên văn rồi gửi bù cho đúng
12 người đã nhận thẻ rỗng (khóa `fixup1-…`, vì khóa cũ `notice-…` bị Lark nuốt).
Sau khi vá, đường tự vá nhặt nốt 7 cuộc tồn từ tháng 7 — **0/447 job còn tóm tắt
rỗng**, không ai nhận thêm tin.

Cùng phiên: đăng tóm tắt `CĐS: Training` vào nhóm `Digital Transformation Chat`
theo yêu cầu (10 người, 7 người ngoài cuộc họp). Thẻ bỏ lời mời "trả lời CÓ" vì
bot không đọc tin nhóm — lúc đó chưa có đường đó.

## 0l. Phiên 27/08/2026 — cuộc họp LẶP mất người dự, và bẫy cấu hình Hermes

### 1. Sự kiện lặp: 19% biên bản chỉ tới tay chủ bản ghi

User hỏi vì sao không nhận biên bản `Team Weekly CĐS` (cuộc họ không dự). Lịch CÓ
mời nhóm `DIGITAL TRANSFORMATION`, mà job chỉ ghi 1 người.

`calendar_events` (instance view) trả `event_id` của MỘT BUỔI, dạng
`<id gốc>_<mốc thời gian>`. Đo thật:

```
…7807_1787814000  (id buổi)      -> event_attendees 193001 event not found
…7807_0           (recurring id) -> OK, 3 khách mời (có cả nhóm chat)
…7807             (id trần)      -> 190014 invalid parameters
```

Mọi cuộc LẶP đều không tra được khách mời rồi rơi về `fallback:owner` — **CHỈ CHỦ
BẢN GHI nhận biên bản, im lặng**. Đo trên 489 job: **94 cuộc (19%)**, riêng từ
14/08 là 36, toàn cuộc lặp hàng ngày. Nay thử id BUỔI trước rồi mới rơi về id
CHUỖI (khách mời có thể sửa riêng cho một buổi).

Hai lệnh mới, cả hai **không gửi tin cho ai**:
- `v2 backfill-chats` — nạp bù `invited_chats`: **68 cuộc**, 25 nhóm.
- `v2 reresolve` — tra lại người dự: 26/71 cuộc, +122 lượt người,
  `fallback:owner` 94 -> 76. 45 cuộc còn lại không có nguồn để tra (cuộc VC tự
  mở, hoặc sự kiện đã xoá).

Trần giãn nhóm 60 -> **80** (`V2_MAX_CHAT_INVITE_MEMBERS`): cuộc
`08-27 | Workforce AI Weekly Meeting` mời nhóm 71 người, vượt trần cũ. 80 chứ
không phải 100 để vẫn chặn `CĐS_Data Mindset` (106 người).

### 2. Bẫy mất nửa ngày: `group_rules` đặt sai chỗ trong config Hermes

Đặt `group_rules` dưới khối `feishu:` cấp cao nhất thì **adapter không bao giờ
đọc** — `gateway/config.py` chỉ merge `extra` từ khoá `platforms:`. Hậu quả: mọi
nhóm rơi về `FEISHU_GROUP_POLICY=disabled`, tin bị bỏ với lý do
`group_policy_rejected`, mà cú bỏ đó **chỉ log ở mức DEBUG** (adapter.py:2594)
nên `gateway.log` trắng trơn.

Tôi đã kết luận SAI hai lần ("Lark không đẩy tin nhóm về máy") trước khi chịu
chạy `hermes gateway run -vv` để nhìn thấy dòng DEBUG đó. Trước đó còn suýt đẩy
user đi mở ticket với Lark. **Log trắng không phải bằng chứng.**

Đúng chỗ là `platforms.feishu.extra.group_rules`. Cũng cần biết:
`hermes config set` đặt đúng giá trị nhưng **nuốt sạch chú thích** trong
`config.yaml` (47 dòng, 23.463 -> 20.803 byte) — sao lưu trước khi dùng.

## 0m. Phiên 28/08/2026 — hỏi đáp TRONG NHÓM, và băng không có tiếng nói

### 1. Hỏi đáp trong nhóm chat — bốn lớp, đã bật cho một nhóm

Từ 02/08 bot chỉ trả lời chat 1-1, lý do ghi ở `gate._refuse_group`: `qa._may_see`
cấp quyền cho người HỎI, còn câu trả lời thì cả phòng ĐỌC. Thiết kế mở lại không
phá luật đó mà **đổi ĐƠN VỊ quyền**: trong nhóm X chỉ trả lời về cuộc mà chính
nhóm X được mời (`meta.invited_chats`, bằng chứng do Lark cấp).

- `config.GROUP_QA_CHATS` (`V2_GROUP_QA_CHATS` trong `v2/.env`, **ngoài git**) —
  danh sách trắng, mặc định RỖNG = tắt hoàn toàn. Vì sao phải là danh sách trắng:
  MỘT nhóm (`PHÁT TRIỂN SẢN PHẨM TÚI XÁCH LAM SON RETAIL`) kéo theo **41 cuộc**,
  trong đó có `HỌP ĐỊNH KÌ THỨ 2 - ALL CÔNG TY`.
- Khớp `chat_id` CHÍNH XÁC, không đoán theo tên: workspace này có HAI nhóm cùng
  tên `DIGITAL TRANSFORMATION`, khác thành viên, bot chỉ ở một.
- Phạm vi buộc vào **VÉ PHIÊN** (`qa_sessions.room_chat_id`), không phải tham số:
  agent không cầm cái nó không được trao. Vé phòng và vé chat 1-1 KHÔNG dùng
  chung, kể cả cùng một người.
- Trong phòng, luật phòng **THAY THẾ** luật cá nhân. Người CÓ dự cuộc B mà nhóm
  không được mời B thì hỏi trong phòng vẫn không xem được B. `see_all` cũng không
  mở cửa phòng.
- Plugin 1.16.0 THÔI tự bỏ tin nhóm — luật nằm một chỗ ở V2; plugin gửi kèm
  `chat_id`, thiếu thì V2 fail-closed.

**Phương án B (user chốt):** người CHƯA enroll vẫn hỏi được trong nhóm trắng —
quyền đến từ việc ở trong phòng, và trả lời được vì dữ liệu đã có sẵn trong DB
(lấy bằng token của CHỦ BẢN GHI lúc phiên âm). Hai đường ghi vẫn đóng:
`tasks` tự chặn (cần token của chính họ), còn `sendfile` **không tự chặn** (bot
upload bằng danh tính app) nên có chốt riêng — chưa enroll thì từ chối file, kèm
lời mời cấp quyền. **Chat 1-1 KHÔNG mở**: ở đó không có phòng nào định nghĩa phạm
vi.

⚠️ Commit `c724d47` viết "Người trong nhóm KHÔNG cần enroll để hỏi" — lúc đó
**SAI** (cổng chạy lớp enroll trước lớp phòng, đo live ra `decision=invite`).
Commit `a28eb7d` mới làm câu đó thành đúng. Đừng đọc commit cũ rồi tưởng nó luôn đúng.

Hiện bật cho đúng `Digital Transformation Chat` (mở được 1 cuộc: `CĐS: Training`).
`Technical & AI Automation` có bot nhưng **0 cuộc** nào mời nhóm đó.

### 2. Băng không có tiếng nói bị báo nhầm là "hỏng"

Hai job `failed` chặn `doctor`. Đo bằng ffmpeg: peak −7.1 / mean −48.3, và peak
−44.2 / mean −81.9. Cả hai KHÔNG có tiếng nói (whisper xử lý 25 phút audio hết 24
giây — VAD lọc sạch). Lưới cũ chỉ đo PEAK nên cái đầu bị coi là "rất to".

- `_volume_db` nay trả cả (peak, mean) — ffmpeg in cả hai dòng trong cùng một lời
  gọi, trước đây code chỉ đọc `max_volume`. Mean ≤ −45 dB KÈM whisper 0 chữ =
  `silent_recording` (discarded). **Chỉ dùng để PHÂN LOẠI**, không dùng để bỏ qua
  whisper: cuộc 25 phút mà chỉ nói 2 phút cũng có mean thấp.
- Chặn **PROMPT VỌNG**: whisper chép thẳng `initial_prompt` ra khi không nghe
  được gì (12 "từ" thu được chính là câu prompt). Prompt dài hơn thì một băng câm
  có thể sinh transcript ĐỦ DÀI để pipeline coi là thành công rồi gửi cho người
  dự một biên bản dựng từ chính câu prompt của mình.

### 3. Việc còn mở

- **Mời bot vào nhóm** (chạy khô đã liệt kê 24 nhóm được mời họp mà bot chưa
  vào): `PHÁT TRIỂN SẢN PHẨM TÚI XÁCH LAM SON RETAIL` 43 cuộc,
  `CĐS_AI & Automation_Workforce AI Team` 7, `Sharing ai thích học cái mới` 4,
  `DIGITAL TRANSFORMATION` 3. Không mời bot thì người trong nhóm vẫn mất biên bản.
- **Phạm Hoàng Phúc - Tiktok Ads**: refresh token đã bị thu hồi (`20064`) nhưng
  DB vẫn ghi `active` — đang âm thầm không nhận gì. `v2 scopes` cũng chết giữa
  chừng vì người này, bỏ dở phần danh sách còn lại.
- **Backup vẫn cùng ổ VẬT LÝ với `state.db`** — và `C:` với `D:` là cùng một SSD
  (Lexar 1TB chia hai phân vùng), nên chuyển sang `C:` là vô nghĩa. Nặng hơn:
  khoá Fernet (`C:\Users\PC\.meetingxlark\`), `v2/.env`, `config.yaml` và `.env`
  của Hermes cũng nằm cùng ổ và KHÔNG có trong git — mất ổ là mất hết. Phương án
  đề xuất (chưa chốt): `V2_BACKUP_DIR` -> OneDrive, khoá Fernet để chỗ khác, sao
  lưu cả bốn thứ ngoài git, và `doctor` so **ổ vật lý** thay vì ký tự ổ.
- **Nghiệm thu hỏi đáp trong nhóm bằng người thật** — chưa làm.

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
- Hệ thống không chủ động gửi link OAuth; chỉ khi người đó nhắn bot thì gate mới
  gửi link như luồng mặc định. (28/08: con số `waiting_auth` là **30**, không
  phải 6 như dòng viết ngày 05/08.)
- Transcript không tự bị đổ vào chat. Job đã dịch có thể ở `held`, chờ người dự hỏi;
  yêu cầu file đi qua tool có ACL.
- Self-test hiện tại: **873 PASS, 0 FAIL** (28/08/2026). Cloudflare Worker: **6/6 PASS**.
  Plugin Hermes live: **1.16.0**. (Con số PASS trong các mục `0a`–`0m` là ghi chép
  của từng phiên, đừng lấy làm mốc hiện tại.)
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

- self-test: `PASS 747 FAIL 0` (13/08/2026);
- Worker tests: 6/6;
- `git diff --check`: không có lỗi whitespace; cảnh báo CRLF của Windows không phải
  lỗi nội dung;
- Doctor: chạy được, chỉ warning backup cùng ổ;
- queue live sau triển khai: `waiting_auth=42`; đây là backlog quyền hiện hữu, không
  được tự mở bằng Base, `minute_viewers` hoặc ghép lịch gần giờ.

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
  **BỎ HẲN 10/08/2026** (plugin 1.3.1): không còn câu xác nhận nào, vì cũng
  không còn tin nhắn nào do V2 tự gửi để mà xác nhận. `list_meetings` trả danh
  sách qua khối GỬI NGUYÊN VĂN và `transform_llm_output` lấy đúng khối đó làm
  câu trả lời — một câu hỏi, một tin nhắn. Xem §11e.
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

## 11e. Phiên 10/08/2026 — một câu hỏi, một tin nhắn

Người dùng gửi hai ảnh chụp chat của anh Thiện kèm nhận xét: **"nhận feedback
rất tệ, trả lời 1 tin thôi đừng trả lời song song"**.

### 11e.1 Vì sao bot trả lời hai tin

Đúng thiết kế 04/08: `sendlist.send_list` gửi THẲNG một thẻ danh sách vào khung
chat rồi trả cho agent một lời dặn "hãy nói một câu ngắn". Agent nói câu đó, và
Hermes gửi nó như một tin nữa. Người dùng nhận: thẻ danh sách + "Danh sách các
cuộc họp tuần trước của bạn ở trên nhé." Đo trong `gateway.log` 15:10:37 — phần
Hermes gửi chỉ 53 ký tự, phần dài là thẻ do V2 tự gửi ngoài luồng.

Lý do phải tự gửi khi đó vẫn đúng ở thời điểm đó: LLM viết lại con số ("Bạn có 8
cuộc họp" → "hệ thống tìm thấy 22 cuộc họp"). Nhưng **09/08 đã dựng lớp chặn ở
đúng chỗ**: `transform_llm_output` lấy khối GỬI NGUYÊN VĂN của tool làm câu trả
lời cuối và vứt bản LLM viết. Từ lúc đó, đường tự gửi chỉ còn để lại cái giá của
nó — tin nhắn thứ hai — mà không mua thêm gì.

Không có cách nào bỏ tin thứ hai bằng cách chặn câu của agent: Hermes không có
hook nào huỷ được tin nhắn đi ra (`transform_llm_output` trả chuỗi rỗng = giữ
nguyên bản cũ, xem `agent/turn_finalizer.py`). Nên vế phải bỏ là vế tự gửi.

### 11e.2 Đã sửa

- Gỡ `v2/sendlist.py`. `mcp_server._tool_list_meetings` gọi lại
  `qa.list_meetings` → khối GỬI NGUYÊN VĂN → plugin lấy đúng chuỗi đó làm tin
  nhắn. Không tool nào còn tự gửi trong một lượt hỏi đáp.
- Gỡ theo: `qa.agent_only`, bảng `outbound_dedup` + `db.try_reserve_outbound` /
  `release_outbound` (chống gửi lặp cho đường tự gửi), và nhánh `_DIRECT_CONFIRM`
  / `_safe_confirm` bên plugin. Plugin lên **1.3.1**.
- Định dạng không mất: danh sách in đậm bằng markdown, và câu trả lời của agent
  đi kiểu `post` nên Hermes render markdown (thẻ `lark_md` trước đây chỉ cần vì
  tin `text` của Lark không render). Trần 8.000 ký tự/tin của adapter Feishu vẫn
  rộng hơn danh sách 50 cuộc.
- `"alo em"` (tin thật 15:09) bị xử như câu hỏi dữ liệu rồi trả về câu chặn
  "chưa tra được dữ liệu": `_SMALLTALK` so khớp NGUYÊN CÂU nên bắt "alo" mà
  trượt "alo em". Chuyển "alo " sang nhánh tiền tố xã giao cạnh "chao ", vẫn giữ
  chốt cũ — có động từ nghiệp vụ thì vẫn phải gọi tool.

### 11e.2b Hệ quả phải biết: gọi `list_meetings` là DỘI LẠI CẢ BẢNG

Đo end-to-end bằng dữ liệu thật 10/08: người dùng hỏi *"còn cuộc họp nào nữa
không"*, agent định trả lời "Hết rồi bạn nhé, chỉ có 9 cuộc thôi" — nhưng vì nó
gọi `list_meetings`, `transform_llm_output` thay câu đó bằng nguyên bảng 1.286
ký tự. Đây là mặt trái CỦA CHÍNH cơ chế ghi đè: khi tool đã dựng tin thành phẩm
thì agent không còn quyền nói gì khác trong lượt đó.

Không sửa bằng cách nới ghi đè (danh sách là đúng thứ không được viết lại), mà
bằng cách chọn tool: mô tả tool nay nói rõ `list_meetings` CHỈ dùng khi người ta
muốn XEM danh sách; câu hỏi VỀ danh sách ("còn cuộc nào nữa không", "cuộc nào
gần nhất", "tuần trước họp mấy buổi") thì đi `search_meetings` — kênh DỮ LIỆU,
agent đọc rồi trả lời bằng lời của nó, và `transform` không đụng vào.

### 11e.2c Trần hiển thị — để một câu hỏi luôn là một tin nhắn

Adapter Feishu cắt tin ở 8.000 ký tự rồi gửi làm NHIỀU tin. Danh sách tốn ~143
ký tự/cuộc (đo thật, 9 cuộc = 1.286), nên `limit=50` mặc định ≈ 7.100 ký tự —
lọt, nhưng chỉ dư ~12%: một người có tên cuộc họp dài hơn trung bình là vượt, và
lúc đó "một câu hỏi một tin nhắn" tự vỡ. Cộng thêm chuyện đọc được: 50 cuộc
trong một bong bóng chat thì không ai đọc.

`qa._fit_list` (10/08/2026): trần **12 dòng** và **3.500 ký tự**, cắt từ đuôi
(cuộc cũ nhất) vì hai danh sách vào đây đã sắp mới-nhất-trước. Khối CHƯA CÓ BIÊN
BẢN được giữ chỗ tối thiểu 4 — đó là những cuộc hệ thống còn nợ người dùng, bỏ
chúng để nhường chỗ cho cuộc cũ đã xong là ngược thứ tự quan tâm.

Hai thứ KHÔNG được đổi khi cắt: **tổng số đầu câu vẫn là tổng thật** (hỏi "tôi
có bao nhiêu cuộc họp" thì con số phải đúng, số dòng hiện ra là chuyện hiển
thị), và **cắt thì phải nói** — `_FOOT_MORE` báo còn N cuộc kèm cách xem tiếp.
Đây không phải câu "Còn N cuộc họp khác" user đã bỏ ngày 04/08: câu đó đếm cuộc
họp của NGƯỜI KHÁC bị ACL ẩn — một con số người đọc không làm gì được; câu này
đếm cuộc của CHÍNH HỌ và nói luôn cách lấy tiếp.

Kèm theo, giảm ma sát gõ phím: chân trang nay là "gửi nguyên văn **&lt;số hoặc
tên&gt;**" — gõ `gửi nguyên văn 2` thay vì gõ lại "08-06 | Workforce AI Weekly
Meeting". Cổng write-tool nhận vì câu đó vẫn có "nguyên văn"; `_POLICY` thêm một
luật: số thứ tự chỉ danh sách VỪA gửi trong phiên, không còn trong ngữ cảnh thì
phải hỏi lại tên chứ không được đoán.

### 11e.3 Cổng "không bịa" đổi chỗ đo: từ CÂU HỎI sang CÂU TRẢ LỜI

User nói tiếp: *"người ta hỏi thì thiên biến vạn hoá sao mà cứng nhắc... giờ alo
mày hoặc hỏi gì khác thì lại trả lời cứng nhắc thế hả?"* — đúng gốc.

`_requires_tool` đoán từ TIN NHẮN VÀO xem câu này có buộc phải gọi tool không,
**mặc định là CÓ**, rồi trừ ra bằng ba danh sách chuỗi: `_SMALLTALK` (so khớp
nguyên câu), `_SELF_PHRASES` (chủ ngữ × đuôi câu), `_HELP_PHRASES`. Tập câu chào
và câu tán gẫu là vô hạn, danh sách thì hữu hạn — nên mọi cách nói chưa ai nghĩ
ra đều rơi vào nhánh "phải gọi tool", và vì không tool nào trả lời được một
tiếng "alo" nên người dùng nhận câu chặn. Đã vá ba lần bằng cách thêm chuỗi
("alo" 05/08, `_SELF_PHRASES` 06/08, "alo em" 10/08) — cả ba đều là vá triệu
chứng, và lần nào cũng có tin nhắn thật rơi vào đúng cái bẫy đó.

Điều thật sự phải chặn không phải "câu hỏi loại nào" mà là **"câu trả lời có
khẳng định gì về dữ liệu cuộc họp mà lượt đó không hề tra không"**. Vế "không hề
tra" là sự thật do `post_tool_call` ghi; vế "khẳng định" để lại dấu vết trong
chính câu văn. Nên cổng chuyển sang đo hai thứ đó, và **mặc định đảo lại thành
CHO QUA**:

```
if not tool_called and _claims_data(response):  -> thay bằng _NO_TOOL_REPLY
```

`_claims_data` bắt: ngày giờ (`06/08`, `18:11`, `2026`); số lượng gắn danh từ
nghiệp vụ (`8 cuộc họp`, `3 biên bản`); link hoặc `minute_token`; nhãn máy
(`CHƯA CÓ BIÊN BẢN`, `held`…); và bài dài >600 ký tự có nhắc phạm vi nghiệp vụ.

Bản đầu tiên chặn **mọi chữ số và mọi danh sách** — thử lại thì chính câu bot tự
giới thiệu ("mình làm được 3 việc: 1. … 2. …") bị chặn, tức lại đúng cái cứng
nhắc đang sửa. Đã siết lại thành mẫu cụ thể, và selftest khoá luôn ca đó.

Hai lỗ **biết và chấp nhận**: (1) câu bịa không số không ngày ("tuần trước bạn có
vài cuộc họp"); (2) danh sách ngắn chỉ có tên cuộc họp, không kèm ngày/link. Bịt
(2) phải chặn mọi danh sách — chặn nhầm nhiều hơn bắt đúng. Đổi lại không đổi:
`qa._may_see` vẫn là cửa duy nhất của dữ liệu thật, tool vẫn phải có vé phiên.

Kèm theo: `POLICY_VERSION` → `2026-08-10.1`, và `_POLICY` viết lại theo cùng
nguyên tắc ("ranh giới nằm ở CÂU TRẢ LỜI, không ở loại câu hỏi"). `_NO_TOOL_REPLY`
rút từ bốn dòng xuống một câu — cổng mới hiếm khi chạm tới, nhưng chạm thì cũng
không nên là một bài giảng.

Đã gỡ hẳn: `_requires_tool`, `_SMALLTALK`, `_SELF_SUBJECTS/_SELF_TAILS/`
`_SELF_PHRASES`, `_HELP_PHRASES`, `_ACTION_HINTS`. `_BUSINESS_HINTS` và
`_TRANSCRIPT_NOUNS` giữ lại vì cổng write-tool và `_is_yes` vẫn dùng.

### 11e.4 Tên gọi: đặt theo NGUỒN, không theo tính chất

User chốt 10/08/2026: phải phân biệt được **"bản dịch từ Lark"** và **"bản dịch
từ server của Hapas"** (chất lượng hơn). Tên cũ "BẢN NGUYÊN VĂN" nói đúng TÍNH
CHẤT (đúng từng chữ) nhưng không nói gì về NGUỒN — mà cả hai bản đều là chép
từng chữ, nên người dùng nhìn "bản nguyên văn" cạnh "bản chép sẵn của Lark"
không suy ra được cái nào tốt hơn. Đúng thứ họ cần để chọn thì lại không có
trong tên.

Đổi ở MỌI chỗ người dùng đọc: chân trang danh sách, `fmt_record`, dòng nguồn của
`get_transcript`, câu mời trong thẻ biên bản, câu báo sau khi gửi file, hồ sơ
bot, và các câu từ chối/lỗi. Hai hằng số `qa.BAN_LARK` / `qa.BAN_HAPAS` để không
lệch chữ giữa các chỗ. Prompt (`_POLICY` + `platform_hints`) có khối riêng dạy
agent gọi đúng tên và không nói lẫn hai bản.

Chân trang nay mời gõ `gửi bản dịch <số hoặc tên>`, nên `_TRANSCRIPT_NOUNS` bên
plugin thêm `"ban dich"`/`"hapas"` — đổi tên hiển thị mà quên mở cổng cho chính
câu lệnh mình vừa mời là bot bảo gõ A rồi chặn A. **Lệnh cũ `gửi nguyên văn` giữ
nguyên hiệu lực**, và selftest khoá cả hai chiều.

### 11e.4b Lời mời, không phải cú pháp lệnh — và cổng phải nhận câu đáp

Chân trang bản đầu dạy người ta gõ `gửi bản dịch <số hoặc tên>` kèm ví dụ — đọc
như hướng dẫn dùng máy. User chốt đổi thành lời mời: *"Bạn cần **bản dịch chuẩn**
của cuộc nào thì cứ nói với mình nhé."*

Nhưng đổi lời mời mà không đổi cổng thì **bot mời rồi bot chặn chính câu trả lời
cho lời mời của mình**: đo thật, `cần` / `cho mình cuộc 2` / `ừ có` đều không gọi
tên bản ghi nên `_on_pre_tool_call` chặn `send_transcript_file`. User: *"sao lại
bị chặn, thêm vào backend cũng được mà"* — đúng, bắt người dùng gõ đúng khuôn để
lọt cổng là đẩy cái dở của backend ra thành việc của họ.

Chữa bằng đúng cơ chế đã có sẵn cho lời mời một-cuộc-họp (07/08):
`qa.list_meetings` nay kèm `OFFER_MARK` ở kênh NỘI BỘ, nên plugin ghi nhận "bot
vừa mời" (`_offered_recently`), và cổng nhận thêm **đường vào thứ 4** —
`_picks_item`: câu NGẮN (≤8 từ) có SỐ, tức người ta đang chỉ vào một dòng vừa
đọc. Vẫn cần CẢ HAI vế (có lời mời VÀ có câu đáp) nên agent không tự mời rồi tự
duyệt. Kèm lời dặn ở kênh nội bộ: câu đáp không chỉ rõ cuộc nào thì HỎI LẠI,
tuyệt đối không đoán — mở cổng là để nhận câu trả lời của người ta, không phải
để agent đoán bừa rồi gửi nhầm file.

Đo lại trên dữ liệu thật, ba nhóm đều đúng: sau danh sách thì `cần`, `cho mình
cuộc 2`, `ừ có`, `cái 2`, `2`, `số 3 nhé` đều QUA; `cuộc họp hôm qua bàn gì`,
`tóm tắt cuộc họp Workforce`, câu dài có số đều CHẶN; và khi CHƯA có danh sách
nào thì cả `cần` lẫn `cho mình cuộc 2` đều CHẶN.

### 11e.5 Trạng thái

Self-test **713 PASS / 0 FAIL** (trước 693). Hai nhóm phép kiểm bị THAY chứ
không phải bỏ: 9 phép kiểm của `sendlist` → nhóm `34c` đo hợp đồng "một
tin nhắn"; ~20 phép kiểm liệt kê câu hỏi của `_requires_tool` → nhóm `34j` đo
`_claims_data` trên CÂU TRẢ LỜI, cộng ba phép end-to-end qua đúng hook.

Đã áp lên hệ đang chạy 10/08 (lần cuối 20:19, plugin 1.5.0): chép plugin, deep-merge
`platform_hints.feishu.append` vào `config.yaml` (backup
`config.yaml.bak-20260810-154239`), `hermes gateway restart` — MCP server chạy
trong tiến trình Hermes nên `qa`/`mcp_server` mới có hiệu lực từ bước này.
Orchestrator KHÔNG cần khởi động lại: bản vá không chạm đường phát biên bản.

## 11f. Phiên 10/08/2026 (tối) — phân loại theo NGUỒN BẢN DỊCH

Anh Thiện đọc danh sách thật rồi chốt bốn điều, và cả bốn đều nói về **cách phân
loại**, không phải câu chữ:

1. *"đã có biên bản => dễ bị hiểu nhầm nhé"* — kèm *"ví dụ 11 biên bản kia server
   xử lý rồi"*: người đọc hiểu nhãn đó là "server đã xử lý xong 11 cuộc".
2. *"phân biệt: bản dịch từ Lark / bản dịch từ server của Hapas (chất lượng hơn)
   … bám cái này mà build luồng hệ thống"*.
3. *"hiển thị ghi chú ở dưới cùng là user đó đã có bao nhiêu biên bản được dịch
   từ server của hapas"*.
4. *"e phải phân được luồng xử lý trong từng trường hợp … logic tư duy mọi trường
   hợp xảy ra => phương án trả lời và xử lý như thế nào"*.

### 11f.1 Vì sao nhãn cũ sai từ gốc

`ĐÃ CÓ BIÊN BẢN / CHƯA CÓ BIÊN BẢN` chia theo **"đã có record trên Base hay
chưa"** — một sự thật SỔ SÁCH NỘI BỘ. Nó lệch với thứ người dùng cần biết ở cả
hai chiều: một cuộc có thể đã có bản dịch đầy đủ mà chưa lên Base (`held`), và
ngược lại Base có record cho cả job đang chờ. Tệ hơn, "biên bản" trong hệ thống
này nghĩa là BẢN TÓM TẮT, nên dùng nó để nói "server xong chưa" là trộn hai
chuyện khác nhau vào một chữ.

### 11f.2 Trục mới, đọc từ DB chứ không đoán

`qa.source_of(job)` — MỘT hàm, và mọi chỗ khác bám vào nó:

| nguồn | điều kiện đọc từ `jobs` | nghĩa với người dùng |
|---|---|---|
| `SRC_HAPAS` | `transcript_path` có file | chép đúng từng câu, lấy được file Word |
| `SRC_LARK` | `lark_chars > 0`, hoặc có link + status ∈ `_CAN_READ_LARK` | đọc/tóm tắt được ngay, tên riêng dễ sai |
| `SRC_NONE` | còn lại | kèm LÝ DO thật (chờ cấp quyền / đang dịch / hỏng) |

Hai cái bẫy đã bịt, và cả hai đều là "hứa thứ mình không lấy được":
* `lark_tried_at` mà `lark_chars = 0` → **không** tính là có bản Lark (đã thử và
  không được — quyền đọc bản chép thuộc CHỦ bản ghi);
* `waiting_auth` **không** nằm trong `_CAN_READ_LARK` dù cuộc nào cũng có link
  Minutes: `coverage.py` đo được 0/27 cuộc `waiting_auth` đọc được bản của Lark.

### 11f.3 Ba con số trong tin nhắn đều phải là số THẬT

Đây là chỗ sinh ra hiểu nhầm "11 biên bản": số trong ngoặc từng là **số dòng
đang hiện**, không phải tổng nhóm. Nay:
* tổng đầu câu = tổng cuộc họp họ thấy được;
* số trong ngoặc mỗi nhóm = tổng của NHÓM đó;
* chân trang = `k/n` cuộc đã có bản dịch từ server Hapas (yêu cầu 3);
* cắt bớt cho vừa một tin thì nói rõ còn bao nhiêu, KHÔNG hạ ba con số trên.
`_fit_list` thêm sàn `_MIN_PER_GROUP = 2`: nhóm đông không được đè cho nhóm
"chưa có bản dịch nào" biến mất — đó đúng là nhóm hệ thống còn nợ người dùng.

### 11f.4 Luồng trả lời theo từng trường hợp (yêu cầu 4)

Dạy trong `_POLICY` + `platform_hints`, và `fmt_record` nay ghi rõ tóm tắt dựng
từ nguồn nào:

| tình huống | bot phải làm |
|---|---|
| có bản Lark, bản Hapas **chưa xong** | trả lời bằng bản Lark, rồi hỏi MỘT câu: có cần bản từ server Hapas không, kèm ước tính tool đưa |
| có bản Lark, bản Hapas **đã sẵn sàng** | vẫn trả lời bằng bản Lark, rồi hỏi: bản server Hapas **đã sẵn sàng, muốn nhận luôn không** — cấm nói "chờ phiên âm" |
| chỉ có bản Hapas (Lark không đọc được) | trả lời bằng bản Hapas, nói rõ đây là bản chuẩn |
| chưa có bản nào | nói thẳng tình trạng + bước tiếp theo, không bịa nội dung |

Hàng thứ hai là **đảo lại quyết định 09/08** ("đã có whisper thì không phục vụ
bản Lark kém hơn"), theo anh Thiện 10/08: *"cứ để bản Lark rồi mời người ta theo
nhu cầu ấy, hỏi là có bản dịch trên server Hapas đã sẵn sàng có muốn nhận luôn
không"*. Đảo có lý do: cái sai hồi 09/08 không phải thứ tự phục vụ mà là **âm
thầm** đưa bản kém — lời mời khi đó còn hứa "phiên âm lại, mất khoảng 10 phút"
trong khi file đã nằm sẵn trên đĩa. Nay `_lark_note` có HAI biến thể và selftest
khoá đúng phần đó: bản sẵn sàng thì tuyệt đối không được hứa chờ.

Lời mời đổi thì cổng write-tool phải theo: `_YES_HEADS` nhận thêm động từ của
chính lời mời ("cho mình luôn", "nhận nhé", "lấy giúp mình"), và `_record_notes`
phát `OFFER_MARK` khi cuộc đó có bản Hapas — mời mà không mở cổng thì bot lại tự
chặn câu trả lời cho lời mời của mình.

Kèm một lệnh cấm: **đừng dùng chữ "biên bản" để nói hệ thống xử lý xong hay
chưa**. Nhãn tình trạng đổi sang "BẢN TÓM TẮT" ở cả `fmt_pending`,
`_pending_block`, `search_meetings` và bản admin ở terminal.

### 11f.5 Trạng thái

Self-test **730 PASS / 0 FAIL** (trước 713), nhóm mới khoá: 9 phép kiểm cho
`source_of` (gồm cả hai cái bẫy trên), 3 nhóm hiển thị đúng tổng, đánh số liên
tục qua ba nhóm, chân trang `k/n`, đủ bản xịn thì thôi mời, và sàn mỗi nhóm khi
cắt. Đo trên dữ liệu thật của anh Thiện: 34 + 1 = 35, chân trang `34/35`, tin
nhắn 2.149 ký tự — vẫn một tin.

Đã áp 11/08 00:25: plugin **1.7.0**, deep-merge `platform_hints.feishu.append`
(backup `config.yaml.bak-20260810-235832`), `hermes gateway restart`, và
`restart-v2.ps1` cho orchestrator (PID 12932) để thẻ tự gửi cũng nói cùng ngôn
ngữ.

### 11f.6 Quyết định mới 13/08 — thay thế lời mời Hapas ở 11f.4

Yêu cầu mới đảo riêng hàng “có Lark, Hapas chưa xong” ở bảng 11f.4: bot vẫn trả
Lark ngay nhưng **không hỏi có cần Hapas không, không hứa tự xử lý và không đưa
ETA**. Chỉ khi file Hapas thật sự đã tồn tại mới được nói bản chất lượng cao đã
sẵn sàng và mời nhận. Quy tắc mới này được khóa ở `qa._has_hapas`, `source_of`,
`_lark_note`, footer danh sách, policy plugin và self-test.

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
