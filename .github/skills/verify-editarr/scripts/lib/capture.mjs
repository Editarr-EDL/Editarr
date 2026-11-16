import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

export const MASK_COLOR = "#FF00FF";

/** Replace every run secret of scannable length in text bound for evidence. */
export function redact(text, secrets) {
  let redacted = text;
  for (const secret of secrets) if (secret.length >= 8) redacted = redacted.replaceAll(secret, "[redacted]");
  return redacted;
}

/**
 * Write `<name>.png` with every input painted MASK_COLOR (passwords, API keys, and
 * any other field value) and `<name>.aria.txt` with run secrets replaced, then
 * fail on horizontal page overflow.
 */
export async function capture(page, run, name) {
  await page.screenshot({ path: path.join(run.evidence, `${name}.png`), fullPage: true, mask: [page.locator("input")], maskColor: MASK_COLOR });
  fs.writeFileSync(path.join(run.evidence, `${name}.aria.txt`), redact(await page.locator("body").ariaSnapshot(), run.secrets), { mode: 0o600 });
  const dimensions = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  assert.ok(dimensions.scroll <= dimensions.client, `${name} has horizontal page overflow`);
}
