// Dashboard trạng thái V2 — "local đẩy snapshot, Vercel chỉ hiển thị".
//
//   POST /api/status   Authorization: Bearer <STATUS_PUSH_SECRET>   body = JSON
//        -> ghi đè snapshot vào Vercel Blob (một key duy nhất).
//   GET  /api/status   (hoặc /status, /)  -> render HTML.
//   GET  /api/status?json=1              -> trả lại snapshot thô.
//
// Vì sao Vercel chỉ nhận snapshot chứ không tự query: V2_ARCHITECTURE §3 —
// token/state.db KHÔNG BAO GIỜ chạm Vercel. Snapshot đã ẩn danh sẵn ở máy
// local (không open_id, không token, không tên cuộc họp), nên hiển thị công
// khai không rò rỉ gì dùng được.
//
// Vì sao Blob mà không phải KV: KV trên Vercel giờ là integration Marketplace
// (Upstash) phải bấm qua dashboard; Blob là store gốc, tạo bằng CLI.
//
// CẠM BẪY ĐÃ ĐO (2026-07-30): ghi đè MỘT pathname cố định thì đọc bị cũ tới
// ~60s — cả `get(..., {useCache:false})` cũng không thoát, vì SDK không cho
// cacheControlMaxAge < 60s.
//
// ĐÃ ĐỔI LẠI (2026-08-02) — và đây là đánh đổi có chủ ý, không phải quên bẫy
// trên. Mỗi snapshot ghi pathname RIÊNG thì POST tốn BA thao tác Blob
// (put + list + del để dọn), mà "Advanced Requests" của gói free chỉ có
// **2.000 thao tác/THÁNG**. Đo từ log: 109 lần đẩy/ngày x 3 = ~327 thao
// tác/ngày, cộng hộp thư OAuth ~100 nữa. Vercel đã gửi thư báo dùng hết 75%
// sau ~3,5 ngày. Cạn hạn mức thì hộp thư OAuth chết theo -> KHÔNG AI ENROLL
// ĐƯỢC, tức mất thứ quan trọng hơn hẳn cái dashboard này.
//
// Nay ghi đè MỘT pathname cố định: POST còn ĐÚNG MỘT thao tác. Giá phải trả là
// đúng cạm bẫy trên — trang có thể hiện dữ liệu cũ tới 60 giây. Với nhịp đẩy
// 30 phút/lần thì 60 giây là vô nghĩa; hồi 30/07 nó mới đáng lo vì lúc đó đẩy
// mỗi vòng và người ta F5 để xem thay đổi tức thì.
//
// Hệ quả nữa: không còn giữ lịch sử KEEP bản. Lịch sử thật nằm ở `state.db` và
// log trên máy local, không phải ở đây.

import { put, list } from "@vercel/blob";

const PREFIX = "status/";
const LATEST = `${PREFIX}latest.json`;   // MỘT file, ghi đè — xem ghi chú trên
const MAX_BODY = 256 * 1024;          // snapshot lành mạnh chỉ vài KB

// ------------------------------------------------------------------ helpers

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** So sánh secret không rò rỉ thời gian (tránh timing attack). */
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

/** Body có thể đã được Vercel parse sẵn, hoặc còn là stream/string. */
async function readJson(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body);
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > MAX_BODY) throw new Error("body quá lớn");
    chunks.push(c);
  }
  if (!chunks.length) throw new Error("body trống");
  return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
}

/** Các bản snapshot, mới nhất trước. */
async function listNewest() {
  const { blobs } = await list({ prefix: PREFIX, limit: 1000 });
  return blobs
    .filter((b) => b.pathname.endsWith(".json"))
    .sort((a, b) => new Date(b.uploadedAt) - new Date(a.uploadedAt));
}

/**
 * URL công khai của `status/latest.json` — SUY RA, không hỏi Blob.
 *
 * Vì sao (04/08/2026): mỗi lần MỞ TRANG cũ đều gọi `list()`, mà `list()` là
 * thao tác ĐẮT. Trang lại tự tải lại mỗi 60 giây, nên một tab để quên đốt ~1
 * thao tác/phút VĨNH VIỄN — đo thật trên dashboard Vercel: ~10 thao tác/phút
 * khi có vài tab mở, tức cạn hạn mức tháng trong vài ngày mà không ai đụng vào
 * hệ thống. Đây mới là nguồn tiêu thật, không phải nhịp đẩy 2 giờ/lần của máy.
 *
 * Token có dạng `vercel_blob_rw_<STORE_ID>_<random>`, còn file ghi bằng
 * `addRandomSuffix:false` nên pathname cố định => URL đoán được. Tải thẳng URL
 * đó là thao tác ĐƠN GIẢN (data transfer), không tính vào hạn mức advanced.
 */
function publicBlobUrl(pathname) {
  const tok = process.env.BLOB_READ_WRITE_TOKEN || "";
  const m = tok.match(/^vercel_blob_rw_([^_]+)_/);
  if (!m) return "";
  return `https://${m[1].toLowerCase()}.public.blob.vercel-storage.com/${pathname}`;
}

async function loadSnapshot() {
  // Đường RẺ trước: tải thẳng URL suy ra được.
  const direct = publicBlobUrl(LATEST);
  if (direct) {
    try {
      const r = await fetch(direct, { cache: "no-store" });
      if (r.ok) return await r.json();
      // 404 = chưa có snapshot nào; đừng rơi xuống list() cho tốn thêm.
      if (r.status === 404) return null;
    } catch { /* mạng chớp -> thử đường cũ */ }
  }
  // Đường CŨ, tốn `list()`: chỉ dùng khi không suy ra được URL (đổi định dạng
  // token) hoặc fetch hỏng. Giữ lại để trang không chết, KHÔNG phải để dùng
  // thường xuyên.
  const blobs = await listNewest();
  if (!blobs.length) return null;
  const resp = await fetch(blobs[0].url, { cache: "no-store" });
  if (!resp.ok) return null;
  return await resp.json();
}

// -------------------------------------------------------------------- routes

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  res.setHeader("Referrer-Policy", "no-referrer");

  if (req.method === "POST") return handlePost(req, res);
  if (req.method === "GET" || req.method === "HEAD") return handleGet(req, res);

  res.setHeader("Allow", "GET, POST");
  return res.status(405).json({ ok: false, error: "method_not_allowed" });
}

async function handlePost(req, res) {
  const want = process.env.STATUS_PUSH_SECRET || "";
  if (!want) {
    return res.status(503).json({
      ok: false,
      error: "chưa đặt STATUS_PUSH_SECRET trên Vercel",
    });
  }
  if (!secretEqual(bearer(req), want)) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }
  if (!process.env.BLOB_READ_WRITE_TOKEN) {
    return res.status(503).json({
      ok: false,
      error: "chưa nối Blob store (vercel blob create-store)",
    });
  }

  let snap;
  try {
    snap = await readJson(req);
  } catch (e) {
    return res.status(400).json({ ok: false, error: `body xấu: ${e.message}` });
  }
  if (!snap || typeof snap !== "object" || Array.isArray(snap)) {
    return res.status(400).json({ ok: false, error: "snapshot phải là object" });
  }

  // received_at do server đóng dấu: nếu đồng hồ máy local lệch thì vẫn còn
  // một mốc thời gian đáng tin để so.
  snap.received_at = Date.now();

  try {
    await put(LATEST, JSON.stringify(snap), {
      access: "public",
      contentType: "application/json; charset=utf-8",
      addRandomSuffix: false,
      allowOverwrite: true,
      cacheControlMaxAge: 60,      // sàn của SDK; trang chịu cũ tối đa 60s
    });
  } catch (e) {
    return res.status(502).json({ ok: false, error: `ghi Blob hỏng: ${e.message}` });
  }

  // KHÔNG dọn gì ở đây nữa: chỉ có đúng một file nên không có gì tích tụ, và
  // list()+del() mỗi lần POST chính là hai phần ba lượng thao tác Blob đã đốt
  // hết hạn mức tháng trong ~4 ngày.
  const pruned = 0;
  return res.status(200).json({ ok: true, received_at: snap.received_at, pruned });
}

async function handleGet(req, res) {
  // Tuỳ chọn: đặt STATUS_VIEW_TOKEN để khoá trang (snapshot có tên người).
  const view = process.env.STATUS_VIEW_TOKEN || "";
  if (view && !secretEqual(String(req.query?.k || ""), view)) {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    return res.status(401).send(page("Cần khoá xem", `<p class="muted">Thiếu hoặc sai tham số <code>?k=</code>.</p>`));
  }

  let snap = null;
  let err = "";
  try {
    snap = await loadSnapshot();
  } catch (e) {
    err = e.message;
  }

  if (req.query?.json !== undefined) {
    return res.status(snap ? 200 : 404).json(
      snap || { ok: false, error: err || "chưa có snapshot" });
  }

  res.setHeader("Content-Type", "text/html; charset=utf-8");
  if (!snap) {
    return res.status(200).send(page("V2 — chưa có dữ liệu", `
      <p class="muted">Chưa nhận được snapshot nào từ máy local${err ? ` (${esc(err)})` : ""}.</p>
      <p>Ở máy chạy orchestrator:</p>
      <pre>python -m v2 push-status</pre>
      <p class="muted">Cần <code>STATUS_PUSH_URL</code> + <code>STATUS_PUSH_SECRET</code>
         trong <code>v2/.env</code>.</p>`));
  }
  return res.status(200).send(render(snap));
}

// ------------------------------------------------------------------- render

const LEVEL = { OK: "ok", WARN: "warn", FAIL: "fail" };

function render(s) {
  const users = Array.isArray(s.users) ? s.users : [];
  const queue = (s.queue && typeof s.queue === "object") ? s.queue : {};
  const checks = Array.isArray(s.checks) ? s.checks : [];

  const verdict = String(s.verdict || "").toUpperCase();
  const badge = LEVEL[verdict] || "warn";
  const verdictText = { OK: "Sẵn sàng", WARN: "Có cảnh báo", FAIL: "Có lỗi chặn" }[verdict]
    || "Không rõ";

  const peopleRows = users.length ? users.map((u) => {
    const exp = Number(u.refresh_exp_ms) || 0;
    return `<tr>
      <td>${esc(u.name || "(không tên)")}</td>
      <td><span class="pill ${u.status === "active" ? "ok" : "fail"}">${esc(u.status || "?")}</span></td>
      <td class="num" data-exp="${exp}">${exp ? "…" : "—"}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="3" class="muted">Chưa ai enroll.</td></tr>`;

  const qKeys = Object.keys(queue).filter((k) => Number(queue[k]) > 0);
  const queueCards = qKeys.length ? qKeys.map((k) => `
    <div class="qcard ${k === "failed" ? "fail" : k === "expired" ? "warn" : ""}">
      <div class="qn">${esc(String(queue[k]))}</div>
      <div class="ql">${esc(k)}</div>
    </div>`).join("") : `<p class="muted">Hàng đợi trống.</p>`;

  const checkRows = checks.map((c) => `
    <li class="chk ${LEVEL[String(c.level).toUpperCase()] || "warn"}">
      <b>${esc(c.label)}</b>${c.detail ? ` <span class="muted">— ${esc(c.detail)}</span>` : ""}
    </li>`).join("");

  const nextScan = Number(s.next_scan_at_ms) || 0;
  const pushed = Number(s.pushed_at_ms) || 0;

  // `send_mode` là cờ của TIẾN TRÌNH đã đẩy snapshot này, không phải của hệ
  // thống. Vòng thật chạy `run --send` (cờ dòng lệnh) trong khi `.env` vẫn ghi
  // SEND_MODE=0, nên một lần `push-status` gõ tay sẽ hiện "dry-run" DÙ hệ thống
  // đang gửi thật. Tiến trình gõ tay không có cách nào biết cờ của vòng run —
  // nên ở đây không đoán, chỉ nói rõ snapshot này từ đâu ra.
  // Snapshot cũ (v1) không có `pushed_by`: rơi về tín hiệu cũ next_scan_estimated.
  const manualPush = s.pushed_by ? s.pushed_by === "manual"
                                 : !!s.next_scan_estimated;
  const sendPill = manualPush
    ? `<span class="pill warn" title="Snapshot này do lệnh push-status gõ tay đẩy lên. Cờ gửi của tiến trình đó không nói lên vòng run đang gửi thật hay không.">đẩy tay — không rõ vòng run đang gửi thật hay dry-run</span>`
    : `<span class="pill ${s.send_mode ? "ok" : "warn"}">${s.send_mode ? "gửi thật" : "dry-run"}</span>`;

  const body = `
    <div class="head">
      <h2>Orchestrator V2</h2>
      <span class="pill ${badge} big">${esc(verdictText)}</span>
      ${s.paused
        // Không để badge xanh "Sẵn sàng" che việc hệ thống đang không phát gì.
        ? `<span class="pill fail big">ĐANG TẠM DỪNG</span>` : ""}
    </div>
    <p class="muted" id="age" data-pushed="${pushed}">
      snapshot lúc ${esc(s.pushed_at_local || "?")}</p>

    <h3>Người đã enroll <span class="muted">(${users.length})</span></h3>
    <table>
      <thead><tr><th>Tên</th><th>Trạng thái</th><th class="num">Refresh còn</th></tr></thead>
      <tbody>${peopleRows}</tbody>
    </table>

    <h3>Hàng đợi</h3>
    <div class="qrow">${queueCards}</div>

    <h3>Vòng quét</h3>
    <p>Chu kỳ <b>${esc(s.poll_interval_s ?? "?")}s</b> ·
       lượt tới <b id="next" data-next="${nextScan}">${nextScan ? "…" : "—"}</b>
       ${s.next_scan_estimated
         ? `<span class="muted">(dự kiến — snapshot đẩy bằng tay, không phải từ vòng <code>run</code>)</span>`
         : ""}
       ${sendPill}
    </p>

    ${checks.length ? `<h3>Khám sức khỏe</h3><ul class="checks">${checkRows}</ul>` : ""}

    <p class="foot">Máy local đẩy lên; trang này không có token và không gọi
      Lark. Làm mới: <code>?json=1</code> để lấy dữ liệu thô.</p>

    <script>
      function fmt(ms){
        if(ms<=0) return "quá hạn";
        var s=Math.floor(ms/1000), d=Math.floor(s/86400), h=Math.floor(s%86400/3600),
            m=Math.floor(s%3600/60);
        if(d>0) return d+" ngày "+h+"h";
        if(h>0) return h+"h "+m+"m";
        return m+"m "+(s%60)+"s";
      }
      function tick(){
        var now=Date.now();
        document.querySelectorAll('[data-exp]').forEach(function(el){
          var v=+el.dataset.exp; if(!v) return;
          el.textContent=fmt(v-now);
          el.className='num'+((v-now)<3*86400000?' warn-t':'');
        });
        var n=document.getElementById('next'), nv=n&&+n.dataset.next;
        if(nv) n.textContent=(nv-now>0)?"sau "+fmt(nv-now):"đang tới hạn";
        var a=document.getElementById('age'), av=a&&+a.dataset.pushed;
        if(av) a.textContent="snapshot cách đây "+fmt(now-av)
          + " (" + new Date(av).toLocaleString('vi-VN') + ")";
      }
      tick(); setInterval(tick,1000);
      // Tải lại 5 PHÚT/lần, và CHỈ khi tab đang được nhìn (sửa 04/08/2026).
      // Bản cũ reload cứng mỗi 60s kể cả tab nền: một tab để quên là một lượt
      // gọi API mỗi phút mãi mãi. Máy chỉ đẩy snapshot mỗi 2 giờ nên tải lại
      // dày hơn thế cũng chẳng có gì mới để xem.
      setInterval(function(){
        if (document.visibilityState === "visible") location.reload();
      }, 300000);
    </script>`;
  return page("Trạng thái V2", body);
}

function page(title, body) {
  return `<!doctype html><html lang="vi"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>${esc(title)}</title>
    <style>
      :root{--bg:#fff;--fg:#0f172a;--mut:#64748b;--line:#e2e8f0;--card:#f8fafc}
      @media(prefers-color-scheme:dark){:root{--bg:#0b1120;--fg:#e2e8f0;
        --mut:#94a3b8;--line:#1e293b;--card:#111a2e}}
      *{box-sizing:border-box}
      body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:760px;
        margin:32px auto;padding:0 18px;color:var(--fg);background:var(--bg);
        line-height:1.55}
      .head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
      h2{margin:0} h3{margin:26px 0 8px;font-size:15px;letter-spacing:.02em;
        text-transform:uppercase;color:var(--mut)}
      .muted{color:var(--mut)}
      code,pre{background:var(--card);border:1px solid var(--line);
        border-radius:6px;padding:1px 5px;font-size:13px;word-break:break-all}
      pre{padding:10px;white-space:pre-wrap}
      table{width:100%;border-collapse:collapse;font-size:14px}
      th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
      th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
      .num{text-align:right;font-variant-numeric:tabular-nums}
      .warn-t{color:#b45309}
      .pill{display:inline-block;padding:2px 9px;border-radius:999px;
        font-size:12px;font-weight:600;border:1px solid transparent}
      .pill.big{font-size:13px;padding:4px 12px}
      .pill.ok{background:#dcfce7;color:#166534}
      .pill.warn{background:#fef3c7;color:#92400e}
      .pill.fail{background:#fee2e2;color:#991b1b}
      .qrow{display:flex;gap:10px;flex-wrap:wrap}
      .qcard{background:var(--card);border:1px solid var(--line);
        border-radius:10px;padding:10px 16px;min-width:96px}
      .qcard.warn{border-color:#fcd34d} .qcard.fail{border-color:#fca5a5}
      .qn{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
      .ql{font-size:12px;color:var(--mut)}
      ul.checks{list-style:none;padding:0;margin:0;font-size:14px}
      .chk{padding:5px 0 5px 22px;position:relative}
      .chk:before{position:absolute;left:0;font-weight:700}
      .chk.ok:before{content:"✓";color:#16a34a}
      .chk.warn:before{content:"!";color:#d97706}
      .chk.fail:before{content:"✕";color:#dc2626}
      .foot{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
        color:var(--mut);font-size:13px}
    </style></head><body>${body}</body></html>`;
}
