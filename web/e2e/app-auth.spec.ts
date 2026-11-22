import { chooseWorkflowStep } from "./workflow-actions";
import { expect, test, type Page } from "@playwright/test";
import { libraries, items, json, authenticationStatus, expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, expectNoAuthenticationSecretsInBrowser, mockApi, signOutThroughMenu } from "./app-fixtures";

async function connectJellyfin(page: Page, password = "jellyfin password") {
  await page.getByLabel("Jellyfin server URL").fill("http://jellyfin.local:8096");
  await page.getByLabel("Jellyfin username").fill("Avery");
  await page.getByLabel("Jellyfin password").fill(password);
  await page.getByLabel("Jellyfin password").press("Enter");
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/settings");
});

test("shows setup without revealing a configured Jellyfin key", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  await expect(page.getByText("API key configured")).toBeVisible();
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Movies" })).toBeVisible();
});

test("connects Jellyfin accessibly as the first owner and restores the requested route", async ({ page }) => {
  await page.goto("/?setup=1#/settings");
  const serverUrl = page.getByLabel("Jellyfin server URL");
  await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
  await expect(serverUrl).toBeFocused();
  await expect(page.getByRole("checkbox", { name: "Remember me" })).toBeChecked();
  await expect(page.getByRole("button", { name: "Restore from backup" })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  await page.setViewportSize({ width: 320, height: 720 });
  await expectNoAccessibilityViolations(page);
  await expectNoHorizontalPageOverflow(page);
  await connectJellyfin(page);
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
});

test("explains that setup needs a Jellyfin administrator and keeps the password out of the browser", async ({ page }) => {
  const password = "browser-setup-canary-password";
  await page.goto("/?setup=1&setup-not-admin=1#/settings");
  await connectJellyfin(page, password);
  await expect(page.getByRole("alert")).toContainText("not an administrator");
  await expect(page.getByLabel("Jellyfin password")).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Connections" })).toHaveCount(0);
  await expectNoAuthenticationSecretsInBrowser(page, [password]);
});

test("signs in with Jellyfin accessibly and restores the requested route", async ({ page }) => {
  await page.goto("/?login=1#/settings");
  const username = page.getByLabel("Jellyfin username");
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  await expect(username).toBeFocused();
  await expect(page.getByLabel("Remember me")).toBeChecked();
  await expect(page.getByRole("radio")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create an account" })).toHaveCount(0);
  await expectNoAccessibilityViolations(page);

  await page.setViewportSize({ width: 320, height: 720 });
  await expectNoAccessibilityViolations(page);
  await expectNoHorizontalPageOverflow(page);
  await username.fill("Admin");
  await page.getByLabel("Jellyfin password").fill("correct horse battery");
  await page.getByLabel("Jellyfin password").press("Enter");
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  await expect(page).toHaveURL(/#\/settings$/);
});

test("keeps every protected view and request behind a pending setup status", async ({ page }) => {
  const protectedRequests: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/") && pathname !== "/api/auth/status") protectedRequests.push(pathname);
  });
  let releaseStatus: (() => void) | undefined;
  const statusHeld = new Promise<void>((resolve) => { releaseStatus = resolve; });
  await page.route("**/api/auth/status", async (route) => {
    await statusHeld;
    await json(route, authenticationStatus("setup_required"));
  });

  await page.goto("/?setup=1#/settings/general");
  await expect(page.getByText("Checking authentication…")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toHaveCount(0);
  expect(protectedRequests).toEqual([]);

  releaseStatus?.();
  await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
  expect(protectedRequests).toEqual([]);
});

test("blocks protected browser requests before setup and completes the setup-login-logout deep-link journey", async ({ page }) => {
  await page.goto("/?setup=1#/settings/general");
  await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toHaveCount(0);

  const beforeSetup = await page.evaluate(async () => {
    const response = await fetch("/api/libraries");
    return { status: response.status, body: await response.json() };
  });
  expect(beforeSetup).toEqual({ status: 423, body: { code: "setup_required" } });

  await connectJellyfin(page);
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toBeVisible();
  await expect(page).toHaveURL(/#\/settings\/general$/);

  await signOutThroughMenu(page);
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toHaveCount(0);
  await page.getByLabel("Jellyfin username").fill("Admin");
  await page.getByLabel("Jellyfin password").fill("correct horse battery");
  await page.getByLabel("Jellyfin password").press("Enter");
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toBeVisible();
  await expect(page).toHaveURL(/#\/settings\/general$/);
});

test("handles a concurrent setup winner by returning to the login gate without rendering protected content", async ({ page }) => {
  await page.goto("/?setup=1&setup-conflict=1#/settings");
  await connectJellyfin(page);
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Connections" })).toHaveCount(0);
});

test("reports safe login failures and keeps credentials out of browser-observable sinks", async ({ page }) => {
  const password = "browser-auth-canary-password";
  const urls: string[] = [];
  const consoleMessages: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  page.on("console", (message) => consoleMessages.push(message.text()));

  await page.goto("/?login=1&login-error=1#/settings");
  await page.getByLabel("Jellyfin username").fill("Admin");
  await page.getByLabel("Jellyfin password").fill(password);
  await page.getByLabel("Jellyfin password").press("Enter");
  await expect(page.getByRole("alert")).toHaveText("Unable to sign in with those credentials. Please try again.");
  await expect(page.getByLabel("Jellyfin password")).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Connections" })).toHaveCount(0);
  await expectNoAuthenticationSecretsInBrowser(page, [password, "test-csrf-token"]);
  expect(urls.join("\n")).not.toContain(password);
  expect(urls.join("\n")).not.toContain("test-csrf-token");
  expect(consoleMessages.join("\n")).not.toContain(password);
  expect(consoleMessages.join("\n")).not.toContain("test-csrf-token");
});

test("unmounts the protected shell when a forms session expires", async ({ page }) => {
  await page.goto("/?session-expiry=1#/settings");
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Connections" })).toHaveCount(0);
});

test("loads protected images with the HTTP-only session cookie and never adds a token to their URL", async ({ page, context }) => {
  const opaqueCookie = "opaque-browser-session";
  await context.addCookies([{
    name: "editarr_session",
    value: opaqueCookie,
    url: "http://127.0.0.1:4173",
    httpOnly: true,
    sameSite: "Lax",
  }]);
  const imageRequest = page.waitForRequest((request) => (
    new URL(request.url()).pathname === "/api/library-items/movie-1/image"
  ));
  await page.getByRole("button", { name: "Movies" }).click();
  await expect(page.getByRole("button", { name: "Open Example Movie" })).toBeVisible();
  const request = await imageRequest;
  expect(request.headers().cookie).toContain(`editarr_session=${opaqueCookie}`);
  expect(request.url()).not.toContain(opaqueCookie);
  expect(request.url()).not.toContain("test-csrf-token");
  await expectNoAuthenticationSecretsInBrowser(page, [opaqueCookie, "test-csrf-token"]);
});

test("has no automated accessibility violations in primary application views", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  await page.getByRole("button", { name: "Movies" }).click();
  await page.getByRole("button", { name: "Open Example Movie" }).click();
  await expect(page.getByRole("heading", { name: "Example Movie" })).toBeVisible();
  await expectNoAccessibilityViolations(page);
});

test("moves, traps, and restores focus for modal confirmations", async ({ page }) => {
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
  await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
  const removeTrigger = page.getByRole("button", { name: "Remove" });
  await removeTrigger.click();

  const dialog = page.getByRole("dialog", { name: "Remove scan #1?" });
  const keepButton = dialog.getByRole("button", { name: "Keep scan" });
  const removeButton = dialog.getByRole("button", { name: "Confirm" });
  await expect(keepButton).toBeFocused();
  await expectNoAccessibilityViolations(page);

  await page.keyboard.press("Shift+Tab");
  await expect(removeButton).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(keepButton).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeAttached();
  await expect(removeTrigger).toBeFocused();
});

test("opens the first library from the root route when configured", async ({ page }) => {
  await page.goto("/#/");
  await expect(page).toHaveURL(/#\/?library\/movies$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Movies" })).toBeVisible();
  await expect(page).toHaveTitle("Editarr — Media Editing — Movies");
});

test("tests and saves a Jellyfin connection without revealing the key", async ({ page }) => {
  await page.getByLabel("Server address", { exact: true }).fill("http://jellyfin.test");
  await page.getByLabel("API key", { exact: true }).fill("browser-never-reads-this");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("region", { name: "Jellyfin" }).getByRole("status")).toContainText("Connection succeeded");
  await page.getByRole("region", { name: "Jellyfin" }).getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("region", { name: "Jellyfin" }).getByRole("status")).toContainText("Jellyfin settings saved");
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
});

test("expands and collapses the top-level Jellyfin Libraries group accessibly", async ({ page }) => {
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(navigation.getByRole("button", { name: "Sources" })).toHaveCount(0);
  const librariesDisclosure = navigation.getByRole("button", { name: "Jellyfin Libraries" });
  await expect(librariesDisclosure).toHaveAttribute("aria-expanded", "true");
  await expect(navigation.getByRole("button", { name: "Movies" })).toBeVisible();
  await librariesDisclosure.click();
  await expect(librariesDisclosure).toHaveAttribute("aria-expanded", "false");
  await expect(navigation.getByRole("button", { name: "Movies" })).not.toBeAttached();
  await librariesDisclosure.click();
  await expect(navigation.getByRole("button", { name: "Movies" })).toBeVisible();
});

test("opens the account menu from the brand row and reaches My Profile", async ({ page }) => {
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await expect(navigation.getByRole("button", { name: "My Profile" })).toHaveCount(0);
  await expect(page.getByText(/Signed in as/)).toHaveCount(0);
  const trigger = page.getByRole("button", { name: /^Account menu for / });
  const brand = page.locator(".brand");
  await expect(brand).toContainText("Editarr");
  const [brandBox, triggerBox] = await Promise.all([brand.boundingBox(), trigger.boundingBox()]);
  expect(triggerBox && brandBox && triggerBox.x + triggerBox.width).toBeGreaterThan((brandBox?.x ?? 0) + (brandBox?.width ?? 0) - 24);
  await trigger.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu");
  await expect(menu).toContainText("owner");
  await expect(menu.getByRole("menuitem", { name: "My Profile" })).toBeFocused();
  await expectNoAccessibilityViolations(page);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.getByRole("menuitem", { name: "My Profile" }).click();
  await expect(page).toHaveURL(/#\/?settings\/profile$/);
  await expect(page.getByRole("menu")).toHaveCount(0);
});

test("scrolls the sidebar without moving the page", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 320 });
  const scroller = page.locator(".sidebar-scroll");
  const overflow = await scroller.evaluate((element) => ({ scroll: element.scrollHeight, client: element.clientHeight, style: getComputedStyle(element).overflowY }));
  expect(overflow.style).toBe("auto");
  expect(overflow.scroll).toBeGreaterThan(overflow.client);
  await scroller.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await expect(page.getByRole("button", { name: /^Account menu for / })).toBeInViewport();
});

test("navigates between Settings subsections", async ({ page }) => {
  const settingsDisclosure = page.getByRole("button", { name: "Settings" });
  await expect(settingsDisclosure).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Connections" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Filters" })).toBeVisible();
  await expect(page.getByRole("button", { name: "General" })).toBeVisible();

  await page.getByRole("button", { name: "Filters" }).click();
  await expect(page).toHaveURL(/#settings\/filters$/);
  await expect(page.getByRole("heading", { name: "Filters", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Jellyfin" })).toHaveCount(0);

  await page.getByRole("button", { name: "General" }).click();
  await expect(page).toHaveURL(/#settings\/general$/);
  await expect(page.getByRole("heading", { name: "Base path mappings" })).toBeVisible();
});

test("manages a mocked model download accessibly on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("dialog", { name: "Navigation menu" }).getByRole("button", { name: "Models" }).click();
  await expect(page.getByRole("heading", { name: "Models", level: 1 })).toBeVisible();
  const audioStage = page.locator("summary").filter({ hasText: /^Audio/ });
  await expect(audioStage).toBeVisible();
  await expect(page.getByText("0 B", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download" })).toBeHidden();
  await audioStage.press("Enter");
  await expect(page.getByRole("link", { name: "Source" })).toHaveCSS("text-decoration-line", "none");
  await page.getByRole("button", { name: "Download" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Cancel download" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "faster-whisper-base download progress" })).toBeVisible();
  await expect(page.getByText("50 B", { exact: true })).toBeVisible();
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  await audioStage.press("Space");
  await expect(audioStage).toContainText("1 downloading");
  await expect(page.getByRole("progressbar", { name: "faster-whisper-base download progress" })).toBeHidden();
});

test("keeps General settings accessible and signs out without a reload", async ({ page }) => {
  await page.getByRole("button", { name: "General" }).click();
  await expect(page.locator("summary").filter({ hasText: "Base path mappings" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Account menu for / })).toBeVisible();
  await expectNoAccessibilityViolations(page);

  await page.setViewportSize({ width: 320, height: 720 });
  await expectNoAccessibilityViolations(page);
  await signOutThroughMenu(page);
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
});

test("browses a library in both views and opens item metadata", async ({ page }) => {
  await page.getByRole("button", { name: "Movies" }).click();
  await expect(page.getByRole("heading", { name: "Movies" })).toBeVisible();
  await expect(page.getByText("EDL · Current").first()).toBeVisible();
  await page.getByLabel("EDL filter").selectOption("not_exported");
  await expect(page.getByRole("heading", { name: "No matching items" })).toBeVisible();
  await page.getByLabel("EDL filter").selectOption("all");
  await expect(page.getByRole("button", { name: "Open Example Movie" })).toBeVisible();
  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByRole("cell", { name: "PG-13" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "4K" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Show exact length: 01:35:00.000" }).first()).toBeVisible();
  await page.getByLabel("Rating filter").selectOption("PG-13");
  await page.getByLabel("Resolution filter").selectOption("4K");
  await page.getByRole("button", { name: "Example Movie" }).click();
  await page.locator("summary").filter({ hasText: "Media files" }).click();
  await expect(page.getByText("A test movie.")).toBeVisible();
  await expect(page).toHaveTitle("Editarr — Media Editing — Example Movie");
  await expect(page.getByText(/^2025 · Rating PG-13/)).toBeVisible();
  await expect(page.getByText("EDL · Current").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Files", exact: true })).toBeVisible();
  await expect(page.getByText("Media sources reported by Jellyfin.")).toHaveCount(0);
  const files = page.getByRole("region", { name: "Media files" });
  await expect(files.getByRole("columnheader")).toHaveText([
    "File name", "Resolution", "Format", "Audio Tracks", "Length", "Size",
  ]);
  const mainFile = files.getByRole("row").filter({ hasText: "example-4k.mkv" });
  await expect(mainFile.getByRole("cell", { name: "2", exact: true })).toBeVisible();
  await expect(mainFile.getByText("EDL · Current")).toBeVisible();
  const alternateFile = files.getByRole("row").filter({ hasText: "example-1080p.mkv" });
  await expect(alternateFile.getByRole("cell", { name: "0", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Manage legacy exports" }).click();
  await expect(page.getByRole("dialog", { name: "Manage exports" })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Delete EDL" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Confirm Delete EDL" }).click();
  await expect(page.getByText("EDL deleted.")).toBeVisible();
  await chooseWorkflowStep(page, "Scans");
  await expect(page.getByRole("list", { name: "Scan history" })).toContainText("#2");
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("button", { name: "Movies" }).click();
  await page.getByPlaceholder("Search titles").fill("missing");
  await expect(page.getByRole("heading", { name: "No matching items" })).toBeVisible();
});

