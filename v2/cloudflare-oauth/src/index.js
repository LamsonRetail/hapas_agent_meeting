const STATE_VERSION = "v1";
const MAX_FUTURE_SECONDS = 2 * 24 * 60 * 60;

function noStore(extra = {}) {
  return {
    "cache-control": "no-store, max-age=0",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    ...extra,
  };
}

function textResponse(body, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: noStore({ "content-type": "text/plain; charset=utf-8", ...extra }),
  });
}

function htmlResponse(title, message, status = 200) {
  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
  const body = `<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title><style>
body{font:16px/1.55 system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#17202a}
.box{border:1px solid #d9e2ec;border-radius:14px;padding:24px;box-shadow:0 6px 24px #0000000d}
h1{font-size:24px;margin:0 0 12px}p{margin:0}</style></head>
<body><main class="box"><h1>${esc(title)}</h1><p>${esc(message)}</p></main></body></html>`;
  return new Response(body, {
    status,
    headers: noStore({
      "content-type": "text/html; charset=utf-8",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    }),
  });
}

function decodeBase36(raw) {
  if (!/^[0-9a-z]+$/.test(raw)) return NaN;
  return Number.parseInt(raw, 36);
}

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function signature(secret, payload) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const out = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return base64url(new Uint8Array(out));
}

function constantTimeEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

async function diagnoseState(state, secret, nowSeconds = Math.floor(Date.now() / 1000)) {
  const result = { ok: false, reason: "shape", now_seconds: nowSeconds };
  if (!secret || typeof state !== "string" || state.length > 256) return result;
  const parts = state.split(".");
  if (parts.length !== 4 || parts[0] !== STATE_VERSION) return result;
  const [version, expiryRaw, nonce, supplied] = parts;
  const expiry = decodeBase36(expiryRaw);
  result.expiry_seconds = expiry;
  if (!Number.isSafeInteger(expiry) || expiry < nowSeconds || expiry > nowSeconds + MAX_FUTURE_SECONDS) {
    result.reason = "expiry";
    return result;
  }
  if (!/^[A-Za-z0-9_-]{24,64}$/.test(nonce) || !/^[A-Za-z0-9_-]{43}$/.test(supplied)) {
    result.reason = "characters";
    return result;
  }
  const expected = await signature(secret, `${version}.${expiryRaw}.${nonce}`);
  result.ok = constantTimeEqual(supplied, expected);
  result.reason = result.ok ? "ok" : "signature";
  return result;
}

export async function verifyState(state, secret, nowSeconds = Math.floor(Date.now() / 1000)) {
  return (await diagnoseState(state, secret, nowSeconds)).ok;
}

function larkAuthorizeUrl(requestUrl, env, state) {
  const domain = env.LARK_DOMAIN === "feishu" ? "https://open.feishu.cn" : "https://open.larksuite.com";
  const query = new URLSearchParams({
    client_id: env.LARK_APP_ID,
    redirect_uri: `${requestUrl.origin}/oauth/callback`,
    scope: env.OAUTH_SCOPES,
    state,
    response_type: "code",
  });
  return `${domain}/open-apis/authen/v1/authorize?${query}`;
}

function configured(env) {
  return Boolean(env.OAUTH_STATE_SECRET?.length >= 32 && env.OAUTH_QUEUE && env.LARK_APP_ID && env.OAUTH_SCOPES);
}

async function handleShortLink(requestUrl, env) {
  const state = requestUrl.pathname.slice(3);
  if (!(await verifyState(state, env.OAUTH_STATE_SECRET))) {
    return htmlResponse("Liên kết không hợp lệ", "Link cấp quyền đã hết hạn hoặc bị thay đổi. Hãy nhắn lại bot để lấy link mới.", 400);
  }
  return new Response(null, {
    status: 302,
    headers: noStore({ location: larkAuthorizeUrl(requestUrl, env, state) }),
  });
}

async function handleCallback(requestUrl, env) {
  const state = requestUrl.searchParams.get("state") || "";
  if (!(await verifyState(state, env.OAUTH_STATE_SECRET))) {
    return htmlResponse("Phiên cấp quyền không hợp lệ", "Phiên này đã hết hạn hoặc không khớp. Hãy quay lại bot để bắt đầu lại.", 400);
  }

  const code = requestUrl.searchParams.get("code") || "";
  const error = requestUrl.searchParams.get("error") || "";
  const errorDescription = requestUrl.searchParams.get("error_description") || "";
  if (!code && !error) {
    return htmlResponse("Thiếu dữ liệu", "Lark không gửi mã hoàn tất. Hãy quay lại bot và thử lại.", 400);
  }

  const body = code
    ? { version: 1, code, state, received_at: Date.now() }
    : { version: 1, state, received_at: Date.now(), fail: { error, error_description: errorDescription } };

  try {
    await env.OAUTH_QUEUE.send(body, { contentType: "json" });
  } catch {
    return htmlResponse("Chưa lưu được kết quả", "Hạ tầng đang bận. Vui lòng tải lại trang này để hệ thống thử lưu lần nữa.", 503);
  }

  if (code) {
    return htmlResponse("Đã cấp quyền thành công", "Bạn có thể đóng trang này và quay lại nhắn bot. Hệ thống sẽ tự hoàn tất trong ít phút.");
  }
  return htmlResponse("Chưa cấp quyền", "Bạn chưa đồng ý cấp quyền. Khi cần, hãy mở lại link mà bot đã gửi.", 200);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET") return textResponse("Method Not Allowed", 405, { allow: "GET" });
    if (url.pathname === "/health") {
      const health = {
        ok: true,
        configured: configured(env),
      };
      return Response.json(health, { headers: noStore() });
    }
    if (!configured(env)) return textResponse("Relay is not configured", 503);
    if (url.pathname.startsWith("/e/")) return handleShortLink(url, env);
    if (url.pathname === "/oauth/callback") return handleCallback(url, env);
    if (url.pathname === "/") return textResponse("MeetingxLark OAuth relay is running");
    return textResponse("Not Found", 404);
  },
};
