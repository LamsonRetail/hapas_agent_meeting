# MeetingxLark OAuth relay trên Cloudflare

Worker này chỉ làm hai việc:

1. kiểm chữ ký link `/e/<state>` rồi chuyển người dùng sang trang OAuth của Lark;
2. kiểm lại `state` ở callback và ghi `{code, state}` vào Cloudflare Queue.

`LARK_APP_SECRET`, user access token, refresh token và nội dung cuộc họp không được đưa
lên Cloudflare. Máy local dùng HTTP Pull để lấy message và chỉ ACK sau khi đã đổi code,
lưu token thành công. Queue có thể giao trùng; local xử lý state một lần nên lần giao lại
được loại an toàn.

## Cấu hình

- Queue: `meetingxlark-oauth`
- Producer binding: `OAUTH_QUEUE`
- Worker secret: `OAUTH_STATE_SECRET` (phải khớp bản local)
- Biến public trong `wrangler.jsonc`: Lark domain, app id và scope

Không xóa deployment Vercel cũ trước khi Cloudflare canary thành công; nó là đường lùi.
