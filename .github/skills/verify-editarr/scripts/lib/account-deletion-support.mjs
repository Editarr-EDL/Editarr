import assert from "node:assert/strict";

/** A minimal MovieContentFilter document for synthetic reviews. */
export const MCF = "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Account deletion proof\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n00:00:01.000 --> 00:00:02.000\nviolence=low=video\n";

export const nameOf = account => account.display_name || account.username || `Account ${account.id}`;

/** Search the Users directory so a row is on screen whatever the page size. */
export async function findAccount({ ui, browser, origin }, query, name) {
  const { page } = browser;
  if (!page.url().endsWith("#/settings/users")) await page.goto(`${origin}/#/settings/users`);
  await page.getByRole("searchbox", { name: "Search users", exact: true }).fill(query);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  if (name) await ui.expect(page.getByRole("button", { name: `View ${name}`, exact: true })).toBeVisible();
}

/** Press Tab until `target` has focus, failing if it is never reached. */
export async function tabTo(page, target, limit = 120) {
  for (let press = 0; press < limit; press++) {
    if (await target.evaluate(element => element === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  assert.fail(`Tab never reached ${await target.textContent()}`);
}

/** Every control and consequence in `dialog` lies inside a viewport of `width` once scrolled to. */
export async function unclipped(page, dialog, width) {
  const boxes = await dialog.locator("button, input, select, dd, li").evaluateAll(elements => elements.map(element => {
    element.scrollIntoView({ block: "nearest" });
    const box = element.getBoundingClientRect();
    return { name: element.getAttribute("aria-label") || element.textContent.trim().slice(0, 60) || element.type, left: box.left, right: box.right, width: box.width };
  }));
  for (const box of boxes) assert.ok(box.width > 0 && box.left >= 0 && box.right <= width + 0.5, `${box.name} lies inside the ${width}px viewport: ${JSON.stringify(box)}`);
  return boxes.length;
}

export async function bothWidths({ ui, browser }, dialog, name) {
  const { page } = browser;
  await ui.evidence(page, `${name}-1440`);
  await page.setViewportSize({ width: 320, height: 900 });
  const checked = await unclipped(page, dialog, 320);
  await ui.evidence(page, `${name}-320`);
  await page.setViewportSize({ width: 1440, height: 900 });
  return checked;
}

async function respond(page, matches, action) {
  const pending = page.waitForResponse(response => matches(response.request().method(), new URL(response.url()).pathname));
  await action();
  const response = await pending;
  return { status: response.status(), body: await response.json().catch(() => null) };
}

/** Confirm the open deletion with the owner's password in the fresh-authentication dialog. */
export async function confirmDeletion({ ui, browser, owner }, title, path) {
  const { page } = browser;
  const fresh = page.getByRole("dialog", { name: title, exact: true });
  await ui.checkTrap(page, fresh);
  await page.keyboard.type(owner.password);
  const answer = await respond(page, (method, pathname) => method === "POST" && pathname === path, () => page.keyboard.press("Enter"));
  if (answer.status === 200) await ui.expect(fresh).toHaveCount(0);
  return answer;
}

export async function choose(dialog, mode) {
  await dialog.getByRole("radio", { name: new RegExp(mode) }).check();
  await dialog.getByRole("button", { name: "Review impact", exact: true }).click();
}

/** Hold the browser's preview request for `userId` until `release()`; the server answers it unchanged afterwards. */
export async function holdPreview(page, userId) {
  const matches = url => url.pathname === `/api/users/${userId}/deletion`;
  let requested, release, held = true;
  const seen = new Promise(resolve => { requested = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  await page.route(matches, async route => {
    if (held && route.request().method() === "GET") {
      requested();
      await gate;
    }
    await route.fallback();
  });
  return { requested: seen, release: () => { held = false; release(); } };
}
