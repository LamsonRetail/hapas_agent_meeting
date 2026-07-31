# Runbook — Test luồng (a): bot-join có mở được minute người khác không

Đây là phép thử **make-or-break** cho Giai đoạn 2. Trả lời đúng một câu:

> Khi bot vào một cuộc họp mà **Thẩm không tổ chức và không được mời**, thì
> user token của Thẩm có **đọc được minute** cuộc đó không?

Nếu **có** → dựng auto-add bot vào mọi cuộc họp calendar, phủ được cả công ty
mà không cần enroll từng người. Nếu **không** → quay về OAuth từng người
(`multi_poller.py` + `enroll_user.py` đã có sẵn).

Công cụ: `test_bot_join.py` (ghi mọi kết quả thô vào `out/test_bot_join_log.txt`).

---

## Vì sao không gộp làm một phép thử

`minutes +detail` và `+apply-permission` **chỉ chạy `--as user`** — người *đọc*
minute luôn là user token (Thẩm). Còn `+meeting-join` chạy được **cả `--as bot`
lẫn `--as user`**. Nên phải tách ba giả thuyết, mỗi cái cho một kết luận khác:

| GT | Cơ chế | Kỳ vọng | Nếu đúng thì |
|---|---|---|---|
| **H1** | Bot (`--as bot`) vào họp → **user token của Thẩm** đọc minute | Nghi ngờ **KHÔNG** (bot và user là hai danh tính khác nhau; quyền cấp cho bot-người-dự không chảy sang user token) | Silver bullet: 1 bot phủ mọi cuộc |
| **H2** | Chính Thẩm (`--as user`) vào họp → Thẩm đọc minute | Nghi ngờ **CÓ** (Thẩm thành người dự thật) | Chỉ chứng minh "người dự thì đọc được" — **không** scale, vì một người không thể dự mọi cuộc cùng lúc |
| **H3** | `apply-permission` trên minute token bất kỳ → mở được | **Chưa rõ** — có thể cần chủ minute duyệt | Nếu tự động mở: khỏi cần join, chỉ xin quyền. Nếu cần duyệt: hợp consent nhưng không tự động |

**Điểm mấu chốt là H1.** H2 và H3 chỉ để giải thích *vì sao* H1 đúng hay sai.

---

## Vai trò

- **Người chủ trì (B)** — một đồng nghiệp bất kỳ **KHÔNG phải Thẩm**. B tạo cuộc
  họp qua Calendar, **không mời Thẩm**, và **bấm ghi hình**. (Anh Thiện hoặc ai
  cũng được.)
- **Người vận hành (Thẩm)** — chạy `test_bot_join.py` trên máy đã cài lark-cli,
  đã đăng nhập (chính là máy đang chạy MCP). Không vào phòng họp bằng người thật.

Không có ghi hình thì **không sinh Minutes**, cả bài test vô nghĩa. Nhắc B bấm
record ngay khi vào.

---

## Chuẩn bị (làm trước, một lần)

```
python test_bot_join.py whoami
```

Xác nhận `user: ready/needs_refresh` và có scope `vc:meeting.bot.join:write`,
`minutes:minutes.basic:read`. Ghi lại `openId` của Thẩm để đối chiếu.

---

## Các lượt chạy

Chạy **4 cuộc họp ngắn** (2–3 phút mỗi cuộc là đủ, cần vài câu nói để có
transcript). B đặt tên cuộc họp khác nhau để dễ tìm minute.

### Lượt 0 — CONTROL (không có bot)  →  chứng minh baseline

B tạo cuộc "TestJoin Control", **không mời Thẩm, không cho bot vào**, bấm record,
nói vài câu, kết thúc. Đợi ~5 phút.

```
python test_bot_join.py find --keyword "TestJoin Control"
python test_bot_join.py read --token <minute_token>
```

**Kỳ vọng:** `find` không thấy, hoặc `read` trả **lỗi quyền** (vd code
`1254043` / `permission` / `403`). Đây là mốc "minute người khác vốn đóng".
Nếu bước này *đọc được* thì tenant vốn đã mở minute cho mọi người — test dừng,
báo lại ngay (thay đổi toàn bộ giả định).

### Lượt 1 — H1: BOT join

B tạo cuộc "TestJoin H1", không mời Thẩm, bấm record. **Ngay khi cuộc bắt đầu:**

```
python test_bot_join.py active                       # lấy meeting number + id
python test_bot_join.py join --number <so> --as bot
python test_bot_join.py events --meeting-id <id>     # xác nhận bot trong phòng
```

Kiểm tra: trên màn hình B, có thấy một người dự tên bot ("AI Agent Assistant")
không? Ghi lại. B nói vài câu rồi kết thúc, dừng record. Đợi ~5 phút.

```
python test_bot_join.py find --keyword "TestJoin H1"
python test_bot_join.py read --token <minute_token>
```

**Đây là câu trả lời chính.** `read` OK → **H1 đúng, luồng (a) sống.**
`read` lỗi quyền → H1 sai, đọc tiếp H2/H3.

### Lượt 2 — H2: USER join

B tạo cuộc "TestJoin H2", không mời Thẩm, bấm record. Ngay khi bắt đầu:

```
python test_bot_join.py active
python test_bot_join.py join --number <so> --as user
```

B nói vài câu, kết thúc, dừng record. Đợi ~5 phút.

```
python test_bot_join.py find --keyword "TestJoin H2"
python test_bot_join.py read --token <minute_token>
```

**Kỳ vọng CÓ.** Nếu H1 sai mà H2 đúng → rào cản là *danh tính* (bot ≠ user),
đúng như nghi ngờ; phủ toàn công ty bằng bot-join không khả thi.

### Lượt 3 — H3: apply-permission

Dùng lại **minute_token của Lượt 0 (Control)** — cái đang bị chặn:

```
python test_bot_join.py apply --token <token_control> --perm view
```

Xem phản hồi: mở luôn, hay tạo một yêu cầu chờ B duyệt? Nếu chờ duyệt, bảo B
vào Lark bấm đồng ý, rồi:

```
python test_bot_join.py read --token <token_control>
```

**Ý nghĩa:** nếu `apply` tự mở (không cần duyệt) → có đường phủ toàn công ty
không cần join, nhưng cân nhắc consent. Nếu cần B duyệt → hợp consent nhưng
không tự động được.

---

## Bảng ghi kết quả (điền vào rồi gửi lại)

| Lượt | minute_token | find thấy? | read OK / mã lỗi | Ghi chú (bot có trong phòng?) |
|---|---|---|---|---|
| 0 Control |  |  |  |  |
| 1 H1 bot |  |  |  |  |
| 2 H2 user |  |  |  |  |
| 3 H3 apply |  | — |  | mở luôn / chờ duyệt |

Kèm luôn file `out/test_bot_join_log.txt` — có raw JSON đầy đủ.

---

## Ma trận quyết định

| Control | H1 | Kết luận & bước tiếp |
|---|---|---|
| đóng | **OK** | **Luồng (a) sống.** Dựng auto-add bot vào cuộc họp calendar (nghe event lịch → lấy meeting number → `+meeting-join --as bot`). |
| đóng | lỗi, **H2 OK** | Bot-join không mở cho user token. Phủ toàn công ty phải theo OAuth từng người (`multi_poller`). Cân nhắc H3. |
| đóng | lỗi, H2 lỗi | `+meeting-join` không cấp quyền đọc minute cho ai qua API. Chỉ còn OAuth từng người. |
| **mở sẵn** | — | Tenant vốn mở minute cho mọi người — kiểm tra lại cấu hình bảo mật tenant trước khi đi tiếp. |
| — | — | **H3 tự mở** (bất kể H1): có đường phủ nhanh qua `apply-permission`, nhưng phải bàn consent với anh Thiện trước khi dùng. |

---

## Lưu ý

- **Consent:** bot lặng lẽ vào + ghi mọi cuộc là chuyện riêng tư. Dù test có
  thành công, nên cho người dự biết có bot ghi hình (Lark vốn hiện thông báo khi
  record). README §11 đã cảnh báo.
- **Chỉ họp đặt qua Calendar** mới lấy được meeting number để join tự động; họp
  ad-hoc nằm ngoài phạm vi.
- **Vẫn phải có người bấm record** — không đổi.
- Múi giờ, thời điểm minute sẵn sàng (chờ ~5 phút), v.v. — xem `docs/TECHNICAL.md`.
