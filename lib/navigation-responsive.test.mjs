import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const styles = new URL("../app/globals.css", import.meta.url);

test("responsive fixed sidebar keeps expanded subnavigation above the main content", async () => {
  const source = await readFile(styles, "utf8");
  const fixedNavigation = source.slice(source.indexOf("/* Fixed primary navigation */"));
  assert.match(fixedNavigation, /@media \(max-width: 1000px\)[^}]*\.sidebar\s*\{[^}]*overflow:\s*visible/);
  assert.match(fixedNavigation, /\.image-generation-task-detail \.task-actions[^}]*position:\s*relative[^}]*z-index:\s*40/);
  assert.match(fixedNavigation, /@media \(max-width: 700px\)[\s\S]*\.subnav\s*\{\s*position:\s*static/);
});
