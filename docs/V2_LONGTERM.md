# V2 — Chiến lược dài hạn: đổi ruột mà không đụng tới nhân sự

Kèm theo `docs/V2_ARCHITECTURE.md`. Tài liệu này trả lời: làm sao để sau này
đổi nơi chạy, đổi engine phiên âm, đổi LLM — mà không ai trong công ty phải
làm lại bất cứ thứ gì.

---

## 1. Nguyên tắc trung tâm

> **Chi phí của một thay đổi tỉ lệ thuận với số người đã enroll.**

Sửa gì đó khi mới có 1 người dùng là chuyện của 5 phút. Sửa đúng thứ đó khi
đã có 40 người là một chiến dịch: nhắn từng người, nhắc, theo dõi ai chưa làm,
xử lý người đang nghỉ phép.

Hệ quả thực tế: **mọi quyết định khó đảo phải chốt xong TRƯỚC khi enroll người
thứ hai.** Danh sách những quyết định đó nằm ở mục 3.

---

## 2. Cái gì thực sự buộc nhân sự vào hệ thống

Nhân viên không biết và không quan tâm code chạy ở đâu. Thứ duy nhất họ có
quan hệ trực tiếp là:

| Thứ | Vì sao nó dính vào họ |
|---|---|
| **`app_id`** | Đó là con bot trong danh sách chat của họ, và là thứ họ đã cấp quyền |
| **Bộ scope đã duyệt** | Thêm scope mới = tất cả phải bấm duyệt lại |
| **Refresh token trong SQLite** | Mất nó = tất cả phải enroll lại |

Hết. Ba thứ. **Mọi thứ khác đều thay được mà họ không biết** — miễn là ba thứ
này giữ nguyên.

Ghi lại cho rõ, vì đây là điều quan trọng nhất trong tài liệu này:

| Thay đổi | Nhân sự có phải làm gì không? |
|---|---|
| Local → VPS | Không |
| Whisper CPU → GPU | Không |
| OpenAI → Hermes cho recap | Không |
| Đổi `redirect_uri` | Không, với người đã enroll |
| Đổi schema DB, đổi ngôn ngữ, viết lại toàn bộ | Không |
| Đổi layout card, đổi văn phong tin nhắn | Không |
| **Thêm một scope mới** | **Có — cả công ty duyệt lại** |
| **Đổi `app_id`** | **Có — enroll lại, mất luôn lịch sử chat với bot** |
| **Mất file SQLite** | **Có — enroll lại toàn bộ** |

Ba dòng cuối là ba cách duy nhất làm phiền nhân sự. Thiết kế sao cho không bao
giờ phải làm chúng.

### Đính chính một hiểu lầm hay gặp

Đổi `redirect_uri` **không** làm mất hiệu lực token đang có. `redirect_uri`
chỉ được kiểm tra tại thời điểm cấp quyền. Người đã enroll không bị ảnh hưởng;
chỉ người enroll *sau* đó mới đi qua URL mới. Nên việc di trú hạ tầng nhẹ hơn
cảm giác ban đầu nhiều.

---

## 3. Bốn quyết định phải chốt trước người thứ hai

### 3.1 Mua một domain riêng. Đây là quyết định giá trị nhất trong cả dự án.

Đừng đăng ký `redirect_uri` là `https://<project>.vercel.app/...`. Đăng ký
`https://meet.<tên-miền-công-ty>/oauth/callback`, trỏ CNAME vào Vercel.

Vì sao đáng vài trăm nghìn một năm: sau này chuyển sang VPS, bạn **đổi bản ghi
DNS**, không đụng vào Lark Developer Console, không chờ admin duyệt lại, không
có khoảnh khắc nào enroll bị gãy. Domain chính là ranh giới trừu tượng giữa
"địa chỉ hệ thống" và "máy nào đang chạy".

Không có domain thì mỗi lần đổi hạ tầng là một lần vào console sửa tay — đúng
cái bẫy ngrok mà chúng ta đã tránh, chỉ chậm hơn.

### 3.2 Xin đủ scope ngay lần đầu

Đây là thứ **đắt nhất** để sửa sau. Thêm scope = màn hình duyệt mới = cả công
ty phải bấm lại, và bạn không có cách nào ép ai bấm.

Nên ngồi nghĩ trước 18 tháng tới sẽ cần gì, xin hết ngay:

| Scope | Cho việc | Cần ngay? |
|---|---|---|
| `minutes` | đọc, tải bản ghi | ✅ |
| `calendar` | dò người được mời | ✅ |
| `vc` | tra thông tin cuộc họp | ✅ |
| `contact` | phân giải tên, phòng ban | ✅ |
| `task` | tạo task từ action item | Chưa dùng ở v1 nhưng **xin luôn** |
| `docs` / `drive` | ghi biên bản thành tài liệu Lark | Nếu thấy có khả năng làm → xin luôn |

Cân bằng: đừng xin những thứ chắc chắn không dùng (`mail`, `okr`, `bitable`
ghi). Màn hình duyệt càng dài thì admin càng ngại và nhân viên càng nghi ngại.
Xin đúng phạm vi "meeting và những gì quanh nó", không hơn.

### 3.3 Orchestrator giữ WebSocket vĩnh viễn. Hermes không bao giờ làm gateway.

**Đây là cái bẫy lớn nhất khi tính chuyện cắm Hermes sau này.**

Doc của Hermes ghi rõ: một `app_id` chỉ một instance Hermes dùng được cùng lúc.
Mà orchestrator của bạn cũng cần chính `app_id` đó để giữ WebSocket nhận event.
**Hai process không thể cùng giữ long connection của một app.**

Có hai lối đi, và chỉ một lối đúng:

| | Ai giữ WebSocket | Hệ quả |
|---|---|---|
| ❌ | Hermes làm gateway, orchestrator thành skill của Hermes | Bỏ Hermes sau này = bot chết. Nhân sự thấy ngay. |
| ✅ | Orchestrator giữ WS mãi mãi, Hermes nằm sau lưng như một dịch vụ | Cắm vào, rút ra, đổi sang thứ khác — không ai biết |

Chọn lối thứ hai và không bao giờ đổi ý. Hermes khi đó chạy ở chế độ **API
server** (endpoint OpenAI-compatible trên localhost) và chỉ được gọi qua hàm
`summarize()` ở mục 4.2 — nó không thấy Lark, không giữ kết nối nào, không có
quyền gì với app. Rút ra hay cắm vào chỉ là đổi một biến môi trường.

Muốn có thêm bot Q&A "hỏi về các cuộc họp của tôi" thì đó là **app thứ hai**,
tách hoàn toàn, tách quyền, tách rủi ro. Không nhét vào app đang chạy pipeline.

### Vì sao KHÔNG nên cho Hermes viết recap ở v1

Dù nối rất dễ, cân nhắc kỹ chuyện có nên hay không:

| | GPT gọi thẳng | Qua Hermes |
|---|---|---|
| Số lần gọi model cho 1 recap | 1, đoán trước được | vòng lặp agent, không đoán trước |
| Cùng transcript, chạy lại sau 6 tháng | ra gần như cũ | **có thể khác** — memory và skill tự thay đổi |
| Bề mặt prompt injection từ lời nói trong họp | chỉ là văn bản | agent có tool, có memory ghi lâu dài |
| Process phải trông | 0 | +1, trên đúng cái máy đang là điểm chết duy nhất |

"The agent that grows with you" là tính năng cho trợ lý cá nhân và là **lỗi**
cho một dây chuyền sinh biên bản — ở đó bạn muốn kết quả ổn định và tái lập
được.

Chỗ Hermes thật sự hơn hẳn GPT trần là **Q&A xuyên nhiều cuộc họp** ("tuần
trước chốt gì về giá?") — nơi memory và session search mới có đất dùng. Đó là
app thứ hai, làm sau, không phải tầng recap.

Cần kiểm chứng nếu vẫn muốn thử: API server của Hermes cô lập session tới đâu.
Nếu mọi cuộc họp đổ chung vào một memory store thì đó là đường rò dữ liệu giữa
các phòng ban.

### 3.4 Chốt sớm định dạng lưu transcript

Transcript sẽ sống lâu hơn mọi thành phần khác của hệ thống. Lưu dạng JSON có
timestamp theo đoạn (`segments`), không phải văn bản thuần:

```json
{"minute_token": "...", "lang": "vi", "duration": 4820,
 "engine": "faster-whisper/large-v3", "created_at": 1753900000,
 "segments": [{"start": 0.0, "end": 4.2, "text": "..."}]}
```

Có `segments` thì sau này làm được: nhảy tới đoạn nói, tìm kiếm có ngữ cảnh,
diarization, chấm điểm engine mới so với cũ. Lưu text thuần thì mất vĩnh viễn,
và **không thể tạo lại** vì bản ghi gốc trên Lark sẽ bị dọn.

Trường `engine` là để sau này biết bản nào phiên âm bằng CPU `tiny` (rác) và
bản nào bằng GPU `large-v3` (dùng được).

---

## 4. Ba seam phải định nghĩa từ hôm nay

"Seam" = chỗ cắt sẵn để sau này thay ruột mà không rách ra ngoài. Định nghĩa
chúng lúc viết code lần đầu tốn thêm nửa ngày; nhét vào sau tốn cả tuần.

### 4.1 Seam phiên âm

```python
def transcribe(audio_path: str, lang: str = "vi") -> Transcript:
    """Trả về {text, segments, duration, engine}. Đồng bộ, có thể chậm."""
```

Đằng sau nó, lần lượt theo thời gian: CPU `tiny` → CPU `small` → GPU `large-v3`
→ (nếu cần) API cloud. Pipeline không được biết cái nào đang chạy.

May mắn: seam này **đã tồn tại sẵn** dưới dạng HTTP. `transcribe_server` nhận
`POST /transcribe` và trả `job_id`. Chỉ cần đừng bao giờ gọi `faster_whisper`
trực tiếp từ orchestrator để "cho nhanh".

### 4.2 Seam LLM

```python
def summarize(transcript: Transcript, meta: MeetingMeta) -> Recap:
    """Trả về {summary, decisions, action_items[]}. Không nhận, không trả
    bất cứ thứ gì đặc thù của một nhà cung cấp."""
```

Đằng sau: OpenAI hôm nay → Hermes sau → gì đó khác nữa. Điều kiện để seam này
thật sự dùng được: **không để khái niệm riêng của một provider rò ra ngoài** —
không truyền `messages`, không truyền `tools`, không truyền `model` từ pipeline
vào. Chỉ transcript vào, cấu trúc ra.

**Hermes phơi được chính nó thành endpoint OpenAI-compatible.** Nghĩa là seam
này rẻ hơn dự tính rất nhiều — đổi từ GPT sang Hermes chỉ là đổi `base_url`:

```python
# hôm nay
client = OpenAI(base_url="https://api.openai.com/v1", api_key=OPENAI_KEY)
# đổi sang Hermes: một biến môi trường, không đụng dòng code nào khác
client = OpenAI(base_url="http://127.0.0.1:8600/v1", api_key="local")
```

Hermes vẫn dùng GPT làm model phía sau, chỉ thêm lớp agent (memory, tool,
skill) lên trên. Nên thí nghiệm "Hermes có viết recap tốt hơn GPT trần không"
là việc của một buổi chiều: chạy cả hai trên cùng 20 transcript cũ, đọc và so.
Không phải một dự án.

Điều kiện tiên quyết: `LLM_BASE_URL` phải là biến môi trường ngay từ commit đầu.
Hardcode `api.openai.com` thì mất luôn món quà này.

### 4.3 Seam "nơi chạy"

Toàn bộ trạng thái di trú được phải gói trong đúng hai thứ:

```
data/state.db        SQLite — token, khoá, hàng đợi
.env                 secret — Fernet key, private key OAuth, app secret
```

Kỷ luật đi kèm, phải giữ từ commit đầu tiên:

- Không hardcode đường dẫn Windows. Mọi path từ env, mặc định tương đối.
- Không giả định múi giờ hệ thống. Lưu UTC, hiển thị +07.
- Không phụ thuộc thứ chỉ có trên Windows (`.bat` chỉ là lớp mỏng gọi Python).
- Không đọc gì từ registry, từ ổ `E:`, từ profile người dùng.

**Kiểm chứng sớm, đừng đợi:** trong tháng đầu, thử copy `state.db` + `.env`
sang một máy khác, chạy lên, rồi quay về. Làm một lần lúc rẻ. Nếu để đến lúc
thật sự cần chuyển VPS mới thử, bạn sẽ phát hiện ra sự phụ thuộc vào đúng lúc
tệ nhất.

---

## 5. Lộ trình CPU → GPU (giai đoạn bạn đang ở)

### Tin tốt: không cần sửa code

`transcribe_server/server.py` dòng 117–120 **đã tự phát hiện và rơi về CPU**:

```python
cuda_ok = torch.cuda.is_available()
if not cuda_ok:
    DEVICE, COMPUTE_TYPE = "cpu", "int8"
```

Và `WHISPER_MODEL` đã là biến môi trường. Nên chạy test bằng CPU chỉ là:

```cmd
set WHISPER_MODEL=tiny
python server.py
```

Nên thêm 2 dòng cho `WHISPER_DEVICE` để **ép CPU ngay cả khi máy có GPU** —
hữu ích khi muốn so sánh hoặc khi GPU đang bận:

```python
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
```

### Chọn model cho giai đoạn test

| Model | Trên CPU | Dùng để |
|---|---|---|
| `tiny` | nhanh hơn thời lượng audio | **Test pipeline.** Chất lượng tiếng Việt kém, không sao |
| `base` | xấp xỉ thời lượng audio | Test có đọc được kết quả |
| `small` | chậm hơn audio 1–2 lần | Ngưỡng cuối còn chịu được trên CPU |
| `large-v3` | chậm hơn audio 5–10 lần | **Không dùng trên CPU** |

Mục tiêu giai đoạn CPU là **chứng minh byte chảy đúng đường**, không phải chất
lượng bản dịch. Dùng `tiny`.

Mẹo làm vòng lặp test nhanh: cắt 2 phút đầu thay vì chạy cả cuộc họp.

```cmd
ffmpeg -i full.mp4 -t 120 -vn -ac 1 -ar 16000 test.mp3
```

### Cái bẫy khi chuyển lên GPU

**Đừng lấy số ETA đo ở giai đoạn CPU rồi tưởng đó là số thật.** Tỉ lệ realtime
chênh nhau một bậc. Khi lên GPU:

1. Đo lại tỉ lệ realtime thật trên chính GPU đó, với `large-v3`, với audio
   tiếng Việt thật.
2. Ghi vào cột `whisper_seconds` / `audio_seconds` trong bảng `Meetings` — nó
   đã có sẵn trong thiết kế chính là để làm việc này.
3. Cập nhật ETA hiện trong card "đang xử lý" từ số đo thật, không từ hằng số.

Và giữ lại vài file audio test cố định. Mỗi lần đổi model hay đổi máy, chạy lại
đúng bộ đó để so — nếu không sẽ không bao giờ biết bản mới tốt lên hay tệ đi.

---

## 6. Lộ trình local → VPS

Nếu đã làm đúng mục 3.1 và 4.3, việc này là một buổi tối:

```
1. Dựng VPS, cài Python + ffmpeg
2. Copy data/state.db + .env sang
3. Trỏ DNS meet.<domain> từ Vercel sang VPS   ← không đụng Lark console
4. Bật orchestrator trên VPS, tắt ở local
5. Máy GPU giữ nguyên transcribe_server, giờ orchestrator gọi qua mạng
   → lúc này seam 4.1 chuyển từ localhost:8502 sang <ip-nội-bộ>:8502
```

Bước 5 là chỗ duy nhất phát sinh: mp4 sẽ tải về VPS rồi đẩy sang máy GPU, hoặc
quay lại mô hình worker-pull như bản thiết kế đầu. Đó là lúc kiến trúc ba thành
phần ban đầu quay lại — nên đừng xoá hẳn ý tưởng đó khỏi đầu.

**Ngưỡng để biết lúc nào nên chuyển** (nhắc lại từ doc chính):
quá ~30 người dùng thật, hoặc biên bản trễ quá một ngày làm việc, hoặc downtime
quá 2 lần/tháng.

---

## 7. Rủi ro dài hạn ít ai nghĩ tới

### 7.1 Nhân viên nghỉ việc

Token của họ vẫn nằm trong store và vẫn hoạt động cho đến khi Lark thu hồi tài
khoản. Cần đối soát định kỳ: mỗi tuần quét danh sách `Users` với directory
công ty, ai không còn thì `status = revoked` và xoá token. Không làm thì sau
hai năm bạn có một kho token của người đã nghỉ.

### 7.2 Token chết âm thầm

Ai nghỉ phép 5 tuần → refresh token hết hạn 30 ngày → lần họp đầu tiên sau khi
về, biên bản của họ không có, và **không ai được báo gì**. Đây là kiểu lỗi tệ
nhất: im lặng.

Xử lý: portal đã hiện "còn N ngày". Thêm tự động — còn 7 ngày thì bot chủ động
nhắn "phiên đăng nhập sắp hết, bấm đây gia hạn". Chi phí gần bằng không, tránh
được cả một lớp khiếu nại.

### 7.3 Transcript chất đống

Mỗi cuộc họp 1 tiếng ≈ vài trăm KB text. Không nhiều. Nhưng vấn đề không phải
dung lượng mà là **trách nhiệm pháp lý**: sau 3 năm bạn đang giữ toàn bộ lời
nói của mọi người trong công ty. Chốt `transcript_retention_days` ngay từ đầu
(đề xuất 90) và **thực sự chạy job xoá**, đừng chỉ để giá trị đó trong bảng
Config cho đẹp.

### 7.4 Lark đổi API

Việc này sẽ xảy ra. Giảm đau bằng cách gom mọi lời gọi API vào một module
`lark_api.py` duy nhất, không rải `requests.post` khắp nơi. Khi Lark đổi, bạn
sửa một file.

### 7.5 Rủi ro lớn nhất không nằm trong code

Đây là dự án của một người thực tập. Trong 12 tháng tới, khả năng cao nhất
khiến hệ thống này chết **không phải** lỗi kỹ thuật — mà là hết kỳ thực tập và
không ai khác đọc nổi nó.

Cách duy nhất chống lại, làm dần từ bây giờ chứ không phải tuần cuối:

- README nói được **vì sao** chứ không chỉ **thế nào**. Doc hiện tại của bạn
  đang làm tốt điều này — giữ tiếp.
- `check_config.py` mở rộng thành công cụ chẩn đoán đủ tốt để người không viết
  code này vẫn tìm ra chỗ hỏng.
- Runbook cho 5 sự cố hay gặp nhất: máy chết, token hết, GPU không lên,
  card lỗi 200340, job kẹt.
- Có ít nhất một người nữa đã từng chạy `enroll` và xử lý một job lỗi bằng tay.

Một hệ thống chỉ một người hiểu là một hệ thống đang đếm ngược.

---

## 8. Tóm tắt cần nhớ

1. **Mua domain riêng trước khi enroll người thứ hai.** Rẻ nhất, giá trị nhất.
2. **Xin đủ scope ngay lần đầu** — kể cả `task` chưa dùng tới.
3. **Orchestrator giữ WebSocket vĩnh viễn.** Hermes vào sau lưng, không bao giờ
   làm gateway.
4. **Lưu transcript có `segments` và `engine`.** Không tạo lại được.
5. **Ba seam:** `transcribe()`, `summarize()`, và "toàn bộ state gói trong
   `state.db` + `.env`".
6. **Thử di trú sang máy khác trong tháng đầu**, lúc còn rẻ.
7. **Backup `state.db` hàng ngày.** Mất nó là cả công ty enroll lại.
8. CPU `tiny` để test pipeline, và **đo lại ETA từ đầu** khi lên GPU.
