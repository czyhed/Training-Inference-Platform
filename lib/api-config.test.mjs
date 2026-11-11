import assert from "node:assert/strict";
import test from "node:test";

import { resolveApiBase, resolveApiWebSocketUrl } from "./api-config.ts";

test("routes same-host absolute API builds through the current gateway", () => {
  assert.equal(
    resolveApiBase("http://203.0.113.11:18010/api", "http://203.0.113.11:13101"),
    "/api",
  );
});

test("keeps cross-host API configuration unchanged", () => {
  assert.equal(
    resolveApiBase("https://api.example.com/api", "http://203.0.113.11:13101"),
    "https://api.example.com/api",
  );
});

test("keeps websocket query parameters outside the encoded pathname", () => {
  assert.equal(
    resolveApiWebSocketUrl(
      "/api",
      "/inference/services/svc-1/stream?afterId=15114",
      "http://203.0.113.11:13101",
    ),
    "ws://203.0.113.11:13101/api/inference/services/svc-1/stream?afterId=15114",
  );
});

test("uses wss for an https origin", () => {
  assert.equal(
    resolveApiWebSocketUrl(
      "/api",
      "/tasks/ft-1/stream?afterId=42",
      "https://platform.example.com",
    ),
    "wss://platform.example.com/api/tasks/ft-1/stream?afterId=42",
  );
});
