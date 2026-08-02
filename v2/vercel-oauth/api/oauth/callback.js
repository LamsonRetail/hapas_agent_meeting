// Hộp thư OAuth trên Vercel.
//
// Lark gọi: GET /oauth/callback?code=...&state=...
// Trang này (a) gửi {code,state} vào Blob để máy local KÉO về và enroll tự động
// (api/oauth-pending.js), và (b) vẫn hiển thị code+state làm đường lùi khi chưa
// nối Blob. Vercel KHÔNG có app_secret nên KHÔNG đổi được code lấy token; code
// lại dùng-một-lần và hết hạn ~5 phút, nên §3 V2_ARCHITECTURE vẫn đúng: token
// không bao giờ chạm Vercel.
//
// addRandomSuffix: true là CÓ CHỦ Ý — `state` hiện trên URL trình duyệt của
// người dùng, lấy nó làm tên file là để lộ đường đọc blob.

import { put } from "@vercel/blob";

const PREFIX = "oauth-pending/";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** Gửi vào hộp thư. Hỏng thì trả false — trang vẫn hiện code để dán tay.
 *  `fail` có giá trị khi Lark từ chối: khi đó không có `code`, ta gửi lý do về
 *  để máy local biết CÓ người bấm mà hỏng, thay vì tưởng chưa ai bấm. */
async function toMailbox(code, state, fail) {
  if (!process.env.BLOB_READ_WRITE_TOKEN) return false;
  try {
    await put(
      `${PREFIX}${Date.now()}.json`,
      JSON.stringify({ code, state, at: Date.now(), ...(fail ? { fail } : {}) }),
      {
        access: "public",
        contentType: "application/json; charset=utf-8",
        addRandomSuffix: true,
        cacheControlMaxAge: 60,
      },
    );
    return true;
  } catch {
    return false;
  }
}

export default async function handler(req, res) {
  const { code, state, error, error_description } = req.query || {};

  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Referrer-Policy", "no-referrer");

  if (error) {
    // Ghi lỗi vào hộp thư luôn. Trước đây nhánh này chỉ hiện trang rồi thôi,
    // nên phía máy local MÙ HOÀN TOÀN: người dùng báo "bấm rồi mà lỗi" và
    // không ai biết Lark từ chối vì cái gì. Đã trả giá 31/07/2026 — mất một
    // vòng chẩn đoán chỉ để biết "code chưa từng về tới nơi".
    // `enroll-poll` đọc thấy `error` thì in ra thay vì cố đổi token.
    await toMailbox(null, state, { error, error_description });
    res.status(400).send(page(
      "Từ chối / lỗi",
      `<p>Lark trả lỗi: <b>${esc(error)}</b></p>
       <p>${esc(error_description || "")}</p>
       <p>Lỗi này đã được báo về cho quản trị viên. Bấm lại link enroll để thử
          lại, hoặc gửi ảnh chụp trang này cho họ.</p>`));
    return;
  }
  if (!code || !state) {
    res.status(400).send(page(
      "Thiếu tham số",
      `<p>URL không có <code>code</code> hoặc <code>state</code>.</p>`));
    return;
  }

  const queued = await toMailbox(code, state);

  if (queued) {
    res.status(200).send(page(
      "Đã cấp quyền ✓",
      `<p>Xong rồi. Bạn quay lại Lark và nhắn cho bot là hỏi được ngay —
          không cần làm gì thêm ở đây.</p>
       <p style="margin-top:24px;color:#64748b;font-size:13px">
          Mã kết nối đã được chuyển về máy xử lý (hết hạn sau vài phút).
          Trang này không giữ token.</p>`));
    return;
  }

  // Đường lùi: chưa nối Blob hoặc put hỏng -> hiện code để dán tay như trước.
  const cmd = `python -m v2 complete --code ${esc(code)} --state ${esc(state)}`;
  res.status(200).send(page(
    "Đã nhận mã kết nối ✓",
    `<p>Gửi hai giá trị dưới đây cho <b>quản trị viên</b> (hoặc tự chạy nếu
        bạn là admin). Mã hết hạn sau vài phút.</p>
     <div class="row"><span class="lbl">code</span>
        <code id="code">${esc(code)}</code></div>
     <div class="row"><span class="lbl">state</span>
        <code id="state">${esc(state)}</code></div>
     <p class="lbl" style="margin-top:20px">Lệnh chạy ở máy local:</p>
     <pre id="cmd">${esc(cmd)}</pre>
     <button onclick="cp()">Sao chép lệnh</button>
     <span id="ok" style="margin-left:10px;color:#16a34a"></span>
     <script>
       function cp(){navigator.clipboard.writeText(document.getElementById('cmd').textContent)
         .then(()=>{document.getElementById('ok').textContent='Đã sao chép';});}
     </script>
     <p style="margin-top:24px;color:#64748b;font-size:13px">
       Hộp thư tự động chưa bật nên trang chỉ hiển thị mã; không lưu và không
       đổi được token (app secret nằm ở máy local).</p>`));
}

function page(title, body) {
  return `<!doctype html><html lang="vi"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>${esc(title)}</title>
    <style>
      body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:640px;
        margin:40px auto;padding:0 20px;color:#0f172a;line-height:1.55}
      h2{margin-bottom:4px}
      code,pre{background:#f1f5f9;border-radius:6px;padding:2px 6px;
        word-break:break-all;font-size:14px}
      pre{padding:12px;white-space:pre-wrap}
      .row{margin:8px 0} .lbl{display:inline-block;width:54px;color:#475569}
      button{background:#2563eb;color:#fff;border:0;border-radius:8px;
        padding:9px 16px;font-size:14px;cursor:pointer}
    </style></head><body><h2>${esc(title)}</h2>${body}</body></html>`;
}
