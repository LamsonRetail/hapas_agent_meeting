// Hộp thư code OAuth — máy local KÉO về, Vercel không bao giờ gọi vào.
//
//   GET /api/oauth-pending   Authorization: Bearer <STATUS_PUSH_SECRET>
//        -> { ok, pending: [{code, state, at}] }  rồi XOÁ những cái đã trả.
//
// Vì sao an toàn để code nằm đây vài phút (V2_ARCHITECTURE §3 vẫn đúng —
// "token không bao giờ chạm Vercel"):
//   * Vercel KHÔNG có app_secret nên tự nó không đổi được code lấy token.
//   * code của Lark dùng-MỘT-lần và hết hạn ~5 phút.
//   * pathname có random suffix, KHÔNG suy ra được từ `state` (state hiện trên
//     URL trình duyệt của người dùng, nên lấy nó làm tên file là hở).
//   * đọc xong xoá ngay; cái quá hạn cũng bị dọn.
//
// Đọc bằng list() + fetch(no-store) chứ không get(): xem cạm bẫy cache 60s ghi
// ở đầu api/status.js.

import { list, del } from "@vercel/blob";

const PREFIX = "oauth-pending/";
const MAX_AGE_MS = 10 * 60 * 1000;      // code Lark ~5 phút; cho gấp đôi rồi dọn
const MAX_ITEMS = 20;

function secretEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function bearer(req) {
  const h = req.headers?.authorization || req.headers?.Authorization || "";
  const m = /^Bearer\s+(.+)$/i.exec(String(h).trim());
  return m ? m[1] : "";
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Referrer-Policy", "no-referrer");

  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }

  const want = process.env.STATUS_PUSH_SECRET || "";
  if (!want) {
    return res.status(503).json({
      ok: false, error: "chưa đặt STATUS_PUSH_SECRET trên Vercel",
    });
  }
  if (!secretEqual(bearer(req), want)) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }
  if (!process.env.BLOB_READ_WRITE_TOKEN) {
    return res.status(503).json({ ok: false, error: "chưa nối Blob store" });
  }

  let blobs = [];
  try {
    ({ blobs } = await list({ prefix: PREFIX, limit: 1000 }));
  } catch (e) {
    return res.status(500).json({ ok: false, error: `list hỏng: ${e.message}` });
  }

  const now = Date.now();
  const pending = [];
  const toDelete = [];

  for (const b of blobs.slice(0, MAX_ITEMS)) {
    const age = now - new Date(b.uploadedAt).getTime();
    if (age > MAX_AGE_MS) { toDelete.push(b.url); continue; }
    try {
      const r = await fetch(b.url, { cache: "no-store" });
      if (r.ok) {
        const item = await r.json();
        if (item && item.code && item.state) {
          pending.push({ code: item.code, state: item.state, at: item.at || null });
        } else if (item && item.fail) {
          // Lark từ chối ở bước Đồng ý. Không có code để đổi, nhưng máy local
          // PHẢI biết là có người đã bấm mà hỏng — im lặng ở đây nghĩa là
          // "tưởng chưa ai bấm", và đó là một vòng chẩn đoán bị mất.
          pending.push({ fail: item.fail, state: item.state || null,
                         at: item.at || null });
        }
      }
    } catch { /* bỏ qua cái đọc hỏng, vẫn xoá để không tắc hộp thư */ }
    toDelete.push(b.url);
  }

  if (toDelete.length) {
    try { await del(toDelete); } catch { /* không xoá được thì lần sau dọn */ }
  }

  return res.status(200).json({ ok: true, pending, count: pending.length });
}
