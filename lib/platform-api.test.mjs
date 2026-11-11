import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const sourceUrl = new URL("./platform-api.ts", import.meta.url);
const contractUrl = new URL("./platform-contracts.ts", import.meta.url);

test("platform API client exposes normalized requests, cancellation, and error parsing", async () => {
  const [source, contracts] = await Promise.all([
    readFile(sourceUrl, "utf8"),
    readFile(contractUrl, "utf8"),
  ]);

  assert.match(source, /export async function platformRequest/);
  assert.match(source, /AbortSignal/);
  assert.match(source, /PlatformRequestError/);
  assert.match(source, /requestId/);
  assert.match(source, /retryable/);
  assert.match(contracts, /export type Modality/);
  assert.match(contracts, /resourceState/);
  assert.match(contracts, /cleanupState/);
});
