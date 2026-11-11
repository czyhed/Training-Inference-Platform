import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("reference console skin defines the measured navigation and control sizes", async () => {
  const source = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const marker = "/* Final measured reference-console dimensions.";
  const skin = source.slice(source.indexOf(marker));

  assert.match(skin, /\.workspace-reference-skin-shell \.sidebar[^}]*padding:\s*20px 14px 18px/);
  assert.match(skin, /\.workspace-reference-skin-shell \.nav-item[^}]*height:\s*42px[^}]*gap:\s*11px/);
  assert.match(skin, /\.workspace-reference-skin-shell \.nav-icon[^}]*width:\s*18px[^}]*height:\s*18px/);
  assert.match(skin, /\.workspace-reference-skin-shell \.content[^}]*padding:\s*30px 34px 44px/);
  assert.match(skin, /\.workspace-reference-skin-shell \.primary-btn[^}]*min-height:\s*40px[^}]*font-size:\s*13px/);
  assert.match(skin, /\.workspace-reference-skin-shell \.icon-only-btn[^}]*min-width:\s*32px[^}]*min-height:\s*32px/);
});
