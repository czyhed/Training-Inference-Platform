import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("shared UI foundation exposes explicit page, status, empty and panel components", async () => {
  const [header, status, empty, panel, types] = await Promise.all([
    readFile(new URL("../components/ui/page-header.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/status-badge.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/empty-state.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/section-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/ui-types.ts", import.meta.url), "utf8"),
  ]);

  assert.match(header, /export function PageHeader/);
  assert.match(header, /aria-labelledby/);
  assert.match(status, /export function StatusBadge/);
  assert.match(status, /role="status"/);
  assert.match(empty, /export function EmptyState/);
  assert.match(empty, /aria-live="polite"/);
  assert.match(panel, /export function SectionPanel/);
  assert.match(panel, /aria-labelledby/);
  assert.match(types, /export type StatusTone/);
  assert.match(types, /export type EmptyStateKind/);
});

test("shared UI foundation defines one focus and responsive contract", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const marker = "/* Antfu UI foundation */";
  const skin = css.slice(css.indexOf(marker));
  assert.match(skin, /:focus-visible/);
  assert.match(skin, /ui-page-header/);
  assert.match(skin, /ui-empty-state/);
  assert.match(skin, /@media \(max-width: 700px\)/);
});
