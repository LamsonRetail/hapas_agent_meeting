import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import test from "node:test";
import worker, { verifyState } from "../src/index.js";

globalThis.crypto ??= webcrypto;

const SECRET = "test-secret-that-is-long-enough-for-hmac";
const NOW = 1_786_000_000;

function makeState(expiry = NOW + 3600, nonce = "abcdefghijklmnopqrstuvwxyzABCDEF") {
  const prefix = `v1.${expiry.toString(36)}.${nonce}`;
  const sig = createHmac("sha256", SECRET).update(prefix).digest("base64url");
  return `${prefix}.${sig}`;
}

function env(queue = { send: async () => undefined }) {
  return {
    OAUTH_STATE_SECRET: SECRET,
    OAUTH_QUEUE: queue,
    LARK_DOMAIN: "lark",
    LARK_APP_ID: "cli_test",
    OAUTH_SCOPES: "offline_access minutes:minutes:readonly",
  };
}

test("state HMAC hợp lệ; state sửa hoặc hết hạn bị chặn", async () => {
  const state = makeState();
  assert.equal(await verifyState(state, SECRET, NOW), true);
  assert.equal(await verifyState(`${state}x`, SECRET, NOW), false);
  assert.equal(await verifyState(makeState(NOW - 1), SECRET, NOW), false);
  assert.equal(await verifyState(makeState(NOW + 3 * 86400), SECRET, NOW), false);
});

test("short link hợp lệ redirect đúng callback Worker và không cache", async () => {
  const state = makeState(Math.floor(Date.now() / 1000) + 3600);
  const response = await worker.fetch(new Request(`https://relay.example/e/${state}`), env());
  assert.equal(response.status, 302);
  const target = new URL(response.headers.get("location"));
  assert.equal(target.hostname, "open.larksuite.com");
  assert.equal(target.searchParams.get("redirect_uri"), "https://relay.example/oauth/callback");
  assert.equal(target.searchParams.get("state"), state);
  assert.equal(response.headers.get("cache-control"), "no-store, max-age=0");
});

test("callback chỉ báo thành công sau khi Queue nhận message", async () => {
  const sent = [];
  const queue = { send: async (body) => sent.push(body) };
  const state = makeState(Math.floor(Date.now() / 1000) + 3600);
  const response = await worker.fetch(
    new Request(`https://relay.example/oauth/callback?code=secret-code&state=${encodeURIComponent(state)}`),
    env(queue),
  );
  assert.equal(response.status, 200);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].code, "secret-code");
  assert.equal(sent[0].state, state);
});

test("Queue lỗi trả 503 để người dùng refresh, không báo thành công giả", async () => {
  const queue = { send: async () => { throw new Error("down"); } };
  const state = makeState(Math.floor(Date.now() / 1000) + 3600);
  const response = await worker.fetch(
    new Request(`https://relay.example/oauth/callback?code=c&state=${encodeURIComponent(state)}`),
    env(queue),
  );
  assert.equal(response.status, 503);
  assert.match(await response.text(), /tải lại trang/i);
});

test("callback giả mạo không được đẩy vào Queue", async () => {
  let calls = 0;
  const queue = { send: async () => { calls += 1; } };
  const response = await worker.fetch(
    new Request("https://relay.example/oauth/callback?code=c&state=fake"), env(queue),
  );
  assert.equal(response.status, 400);
  assert.equal(calls, 0);
});

test("Lark từ chối được ghi có cấu trúc để local xử lý và ack", async () => {
  const sent = [];
  const state = makeState(Math.floor(Date.now() / 1000) + 3600);
  const response = await worker.fetch(
    new Request(`https://relay.example/oauth/callback?error=access_denied&error_description=no&state=${encodeURIComponent(state)}`),
    env({ send: async (body) => sent.push(body) }),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(sent[0].fail, { error: "access_denied", error_description: "no" });
});
