// Rút gọn link enroll: /e/<code>  ->  302 tới URL authorize đầy đủ của Lark.
//
//   POST /api/enroll-link   Authorization: Bearer <STATUS_PUSH_SECRET>
//        body {code, url, ttl_s}      máy local ĐẨY link đầy đủ lên
//   GET  /e/<code>                    người dùng bấm -> chuyển hướng
//
// Vì sao cần: URL authorize dài ~3.800 ký tự (xin trọn họ scope). Dán vào chat
// Lark thì hay bị xuống dòng làm gãy link, và người nhận không dám bấm một chuỗi
// dài như vậy. Link ngắn ~50 ký tự thì dán đâu cũng được.
//
// Vì sao KHÔNG dùng bit.ly/tinyurl: link enroll không nên đi qua bên thứ ba.
// Ở đây nó nằm trên chính hạ tầng của dự án, cùng bearer với dashboard.
//
// Rủi ro đã cân: blob để `public` nên ai có <code> là đọc được URL authorize.
// KHÔNG phải rò rỉ mới — chính link enroll vốn đã như vậy: ai cầm link cũng chỉ
// enroll được CHÍNH HỌ, và phải ở trong tenant (xem OAUTH_NONCE_TTL trong
// v2/config.py). `code` là chuỗi ngẫu nhiên, không suy ra được từ `state`.
//
// Đọc bằng list() + fetch(no-store) chứ không get(): cạm bẫy cache 60s của Blob,
// ghi ở đầu api/status.js.

import { put, list, del } from "@vercel/blob";

const PREFIX = "enroll-link/";

/**
 * URL cong khai suy ra tu BLOB_READ_WRITE_TOKEN (`vercel_blob_rw_<STORE>_<...>`).
 * Ghi bang addRandomSuffix:false nen pathname co dinh => doan duoc URL, tai
 * thang la thao tac DON GIAN thay vi `list()` DAT. Xem ghi chu o api/status.js.
 */
function publicBlobUrl(pathname) {
  const tok = process.env.BLOB_READ_WRITE_TOKEN || "";
  const m = tok.match(/^vercel_blob_rw_([^_]+)_/);
  if (!m) return "";
  return `https://${m[1].toLowerCase()}.public.blob.vercel-storage.com/${pathname}`;
}
const MAX_TTL_MS = 48 * 60 * 60 * 1000;

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

function page(title, body) {
  return `<!doctype html><meta charset="utf-8"><title>${title}</title>
<style>body{font-family:system-ui;max-width:560px;margin:80px auto;
line-height:1.7;color:#222;padding:0 20px}h2{margin:0 0 12px}
.m{color:#666}</style><h2>${title}</h2><p class="m">${body}</p>`;
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Referrer-Policy", "no-referrer");

  if (!process.env.BLOB_READ_WRITE_TOKEN) {
    return res.status(503).json({ ok: false, error: "chưa nối Blob store" });
  }

  // ---------- máy local đẩy link lên ----------
  if (req.method === "POST") {
    const want = process.env.STATUS_PUSH_SECRET || "";
    if (!want) {
      return res.status(503).json({ ok: false, error: "chưa đặt STATUS_PUSH_SECRET" });
    }
    if (!secretEqual(bearer(req), want)) {
      return res.status(401).json({ ok: false, error: "unauthorized" });
    }

    const b = req.body || {};
    const code = String(b.code || "");
    const url = String(b.url || "");
    // Chỉ nhận đúng host của Lark: nếu không, endpoint này thành một cái
    // open-redirect miễn phí cho bất kỳ ai lấy được bearer.
    if (!/^[A-Za-z0-9_-]{6,32}$/.test(code)) {
      return res.status(400).json({ ok: false, error: "code không hợp lệ" });
    }
    if (!/^https:\/\/(open\.larksuite\.com|open\.feishu\.cn)\//.test(url)) {
      return res.status(400).json({ ok: false, error: "url phải trỏ tới Lark" });
    }

    const ttl = Math.min(Number(b.ttl_s || 86400) * 1000, MAX_TTL_MS);
    try {
      await put(PREFIX + code,
        JSON.stringify({ url, exp: Date.now() + ttl }),
        { access: "public", addRandomSuffix: false,
          contentType: "application/json" });
    } catch (e) {
      return res.status(500).json({ ok: false, error: `lưu hỏng: ${e.message}` });
    }
    return res.status(200).json({ ok: true, code });
  }

  // ---------- người dùng bấm link ngắn ----------
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET, POST");
    return res.status(405).json({ ok: false, error: "method_not_allowed" });
  }

  const code = String(req.query?.c || "");
  if (!/^[A-Za-z0-9_-]{6,32}$/.test(code)) {
    return res.status(400).send(page("Link không hợp lệ",
      "Đường dẫn thiếu mã hoặc mã sai định dạng. Xin lại link mới."));
  }

  // Tải THẲNG bằng URL suy ra từ token (04/08/2026) — ghi bằng
  // `addRandomSuffix:false` nên pathname cố định. `list()` là thao tác ĐẮT và
  // đây là đường MỌI người mới đều đi; xem ghi chú dài ở `api/status.js`.
  let item = null;
  let blobUrl = "";                 // URL thật của blob, để dọn khi hết hạn
  const direct = publicBlobUrl(PREFIX + code);
  if (direct) {
    try {
      const r = await fetch(direct, { cache: "no-store" });
      if (r.ok) { item = await r.json(); blobUrl = direct; }
    } catch { /* rơi xuống đường cũ */ }
  }
  if (!item) {
    let blobs = [];
    try {
      ({ blobs } = await list({ prefix: PREFIX + code, limit: 5 }));
    } catch (e) {
      return res.status(500).send(page("Lỗi tạm thời", "Thử lại sau ít phút."));
    }
    const hit = blobs.find((x) => x.pathname === PREFIX + code);
    if (!hit) {
      return res.status(404).send(page("Link đã hết hạn hoặc không tồn tại",
        "Nhắn cho quản trị hệ thống để xin link mới."));
    }
    try {
      const r = await fetch(hit.url, { cache: "no-store" });
      item = await r.json();
      blobUrl = hit.url;
    } catch {
      return res.status(500).send(page("Lỗi tạm thời", "Thử lại sau ít phút."));
    }
  }

  if (!item?.url || (item.exp && Date.now() > item.exp)) {
    if (blobUrl) { try { await del(blobUrl); } catch { /* dọn được thì dọn */ } }
    return res.status(410).send(page("Link đã hết hạn",
      "Nhắn cho quản trị hệ thống để xin link mới."));
  }

  // KHÔNG xoá sau khi bấm: người ta hay mở nhầm rồi quay lại bấm tiếp. Nonce ở
  // phía V2 mới là thứ dùng-một-lần, và nó tự chặn lần thứ hai.
  res.setHeader("Location", item.url);
  return res.status(302).end();
}
