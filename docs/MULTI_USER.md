# Giai đoạn 2 — Phủ nhiều nhân sự (central multi-user poller)

Tài liệu này mô tả cách mở rộng luồng meeting note từ **một người** (Thẩm) sang
**nhiều nhân sự** trong công ty, để với mỗi cuộc họp, biên bản tự về tay người
được mời — bất kể ai là chủ cuộc họp.

Đọc `README.md` để nắm luồng gốc trước. Phần gửi vẫn **dùng bot như cũ**; phần
mới chỉ là cơ chế **phát hiện đa người dùng**.

---

## 1. Vì sao cần token của từng người

Scope `minutes:minutes.basic:read` và `vc:record:readonly` là **user-specific**:
một token chỉ đọc được minute của cuộc họp mà **chủ token đó có dự**. App có thể
nhận thông báo mọi cuộc họp trong công ty kết thúc (`vc:meeting.all_meeting`,
scope tenant), nhưng **không đọc được nội dung** cuộc họp mình không dự.

Hệ quả: poller chạy bằng token của Thẩm chỉ phủ được cuộc họp của Thẩm. Muốn phủ
cả công ty thì **bắt buộc phải có token của từng người**. Đây đúng là "Hướng 1 —
OAuth từng người" mà README đã nêu.

Registry Base của MCP (`tblmw9GHm66nJBhY`) **không phải** kho token — nó chỉ theo
dõi máy nào đã cài MCP. Token của mỗi người nằm local trên máy họ. Nên phải gom
token về máy admin bằng một bước enroll.

---

## 2. Cách gom token: một profile lark-cli cho mỗi người

lark-cli cho phép nhiều **profile**, mỗi profile giữ phiên đăng nhập của một
người (dùng chung app `cli_a9bd0ff8d6619ed1`). Ta không tự viết OAuth/refresh —
lark-cli tự gia hạn token theo cửa sổ trượt, y như README mô tả: chỉ cần poller
gọi API trong vòng 7 ngày là token sống vô thời hạn.

Enroll một người = tạo profile + để họ quét QR duyệt **một lần**:

```
copy users.example.json users.json      (chỉ lần đầu)
python enroll_user.py quythien --name "Le Quy Thien"
```

`enroll_user.py` sẽ:

1. `lark-cli profile add --name quythien --app-id cli_a9bd0ff8d6619ed1 ...`
2. `lark-cli auth login --profile quythien --domain minutes,calendar,vc,contact`
   → hiện QR/link. Người đó mở Lark quét mã, bấm **Đồng ý**.
3. Đọc `auth status` lấy `open_id`, ghi vào `users.json`.

Người ở xa: chạy `lark-cli auth login --profile <tên> --no-wait --json` để lấy
link, gửi link cho họ duyệt, rồi hoàn tất bằng `--device-code`.

> **Tuyệt đối không** chạy `lark-cli profile use` hay `profile remove` — sẽ làm
> hỏng MCP cả team đang dùng. Chỉ dùng `profile add` và `auth login --profile`.
> `enroll_user.py` được viết đúng theo nguyên tắc này.

`users.json` là **nguồn sự thật** cho poller. Thêm nó vào `.gitignore` (chứa
danh sách nhân sự). Tắt tạm một người: đặt `"active": false`, không cần xoá.

---

## 3. Luồng của central poller

```
multi_poller.py  (mỗi vòng)
  với TỪNG người trong users.json (active):
    đặt lark-cli --profile = người đó
    [1] quét Minutes của họ  (find_new_minutes, dùng lại y nguyên)
          minute mới → chờ 3 phút cho Lark xử lý xong
    [2] dò lịch → danh sách người được mời  (resolve_participants)
    [3] tải bản ghi bằng TOKEN CỦA HỌ → Whisper → recap
    [4] BOT gửi recap + transcript cho từng người được mời
    → đánh dấu minute đã xử lý TOÀN CỤC
```

Toàn bộ B1–B4 tái dùng code Giai đoạn 1 (`meeting_poller.py`,
`meeting_delivery.py`), chỉ khác là chạy lặp và đổi profile theo từng người.

**Chống trùng toàn cục.** Một cuộc họp 3 người dự sẽ xuất hiện trong Minutes của
cả 3. `poller_state.json` (`seen_tokens`) dùng chung giữa mọi người: ai xử lý
trước thì đánh dấu, những người sau bỏ qua. Nhờ vậy mỗi cuộc họp chỉ phiên âm và
gửi **một lần**, dù nhiều người cùng dự.

**Vì sao đọc minute phải dùng token của họ, nhưng gửi vẫn là bot.** Tải bản ghi
(`minutes +download`) là hành động user-specific → phải dùng token người có dự.
Còn gửi tin (`--as bot`) dùng token **bot của app** — cùng một app cho mọi
profile, nên gửi ai cũng như nhau, không phụ thuộc profile đang đặt.

---

## 4. Cách chạy

Kiểm tra khô (không gửi), một người, cho chắc:

```
python multi_poller.py --once --dry-run --only quythien
```

Chạy thật một vòng cho tất cả:

```
python multi_poller.py --once --send
```

Chạy nền (lặp theo `POLL_INTERVAL`), hoặc bấm `run-multi-poller.bat`:

```
python multi_poller.py --send
```

Ép gửi thử về một người nhận (pilot):

```
python multi_poller.py --once --send --to ou_1bc55b6d5b20ee06cbee1326d5b72715
```

Mặc định an toàn: không truyền cờ nào → **dry-run**. Phải có `--send` mới gửi thật.

Khi khởi động, poller in trạng thái auth của mọi profile: còn bao nhiêu ngày,
ai cần đăng nhập lại.

---

## 5. Bảo mật — đọc kỹ

Máy admin **giữ token toàn-quyền của mọi người đã enroll**. Người vận hành máy
này về mặt kỹ thuật có thể đọc mail, drive, docs, và mọi cuộc họp riêng tư của
những người đó. Đây là đánh đổi cố hữu của Hướng 1, không phải lỗi cấu hình.

Vì vậy:

- Chỉ giao cho **một người quản trị** (đã thống nhất). Không chạy trên máy dùng
  chung.
- Xin **scope tối thiểu** khi enroll. Mặc định `enroll_user.py` chỉ xin
  `minutes,calendar,vc,contact` — vừa đủ để phát hiện cuộc họp, **không** xin
  mail/drive. Đừng dùng `--domain all` trừ khi thật cần.
- `users.json` và credential store của lark-cli là dữ liệu nhạy cảm. Không commit
  `users.json`, không sao chép credential ra ngoài.
- Nói rõ với nhân sự khi enroll: token dùng để tự gửi biên bản họ dự, và ai là
  người quản trị.

Lưu ý: Giai đoạn 1 có một tính chất bảo mật đẹp — "không ai đọc được cuộc họp
mình không dự, kể cả người dựng hệ thống". Gom token tập trung **đánh đổi chính
tính chất đó** lấy độ phủ. Cân nhắc kỹ trước khi mở rộng danh sách enroll.

---

## 6. Kế hoạch triển khai

1. **Pilot 2–3 người** (Thẩm + anh Thiện + một người nữa). Enroll, chạy
   `--dry-run` vài ngày xem có bắt đúng cuộc họp và đúng người được mời không.
2. Bật `--send` cho nhóm pilot, đối chiếu người nhận thực tế.
3. Mở rộng dần theo phòng ban. Mỗi đợt vài người, theo dõi log auth và tỉ lệ lỗi.
4. Đăng ký Task Scheduler cho `run-multi-poller.bat` để tự chạy khi bật máy.

---

## 7. Hạn chế đã biết (kế thừa Giai đoạn 1)

- **Phải có người bấm ghi hình** thì mới sinh Minutes; không record → luồng đứng
  im, không báo lỗi.
- **Cuộc họp phải đặt qua Calendar** mới tra được người được mời. Họp mở trực
  tiếp → rơi về gửi cho chính người đã phát hiện ra minute (fallback).
- **Bot phải có quyền gửi cho người nhận.** Nếu dùng app riêng
  (`SENDER_APP_ID`), app đó phải đặt Availability toàn công ty (README, mã lỗi
  230013). Nếu gửi bằng bot của app MCP thì app đó cũng cần bật Bot + `im:message`
  + phát hành toàn công ty.
- **Xử lý tuần tự.** Một cuộc họp dài chặn cả hàng đợi (kể cả của người khác).
  Nếu enroll đông người, cân nhắc chuyển sang server GPU + hàng đợi song song.
- **Token hết hạn nếu 7 ngày không gọi API.** Poller chạy đều thì tự gia hạn;
  máy tắt lâu → vài người phải `--relogin`. Poller cảnh báo trước khi hết hạn.

---

## 8. File của Giai đoạn 2

| File | Vai trò |
|---|---|
| `enroll_user.py` | Enroll một nhân sự: tạo profile + đăng nhập + ghi `users.json`. |
| `multi_poller.py` | Poller trung tâm: lặp qua từng người, tái dùng B1–B4, dedup toàn cục. |
| `run-multi-poller.bat` | Khởi động poller đa người (tự bật Whisper nếu cần). |
| `users.json` | Danh sách nhân sự đã enroll (nguồn sự thật). Không commit. |
| `users.example.json` | Mẫu để sao chép thành `users.json`. |

`meeting_poller.py` và `meeting_delivery.py` **không đổi** — được import và dùng
lại nguyên vẹn.
