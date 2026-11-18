import { expect, test } from "@playwright/test";
import { authenticationStatus, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";

test("Models shows and saves audio padding in milliseconds, including zero", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  let audioPaddingSeconds = 0.1;
  const saved: number[] = [];
  await page.route("**/api/settings/models/scans", (route) => {
    if (route.request().method() === "PUT") {
      audioPaddingSeconds = route.request().postDataJSON().audio_padding_seconds;
      saved.push(audioPaddingSeconds);
    }
    return json(route, { audio_padding_seconds: audioPaddingSeconds });
  });
  await page.goto("/#/settings/general");
  await page.getByRole("button", { name: "Open navigation" }).click();
  const navigation = page.getByRole("dialog", { name: "Navigation menu" });
  const modelsLink = navigation.getByRole("button", { name: "Models", exact: true });
  await expect(modelsLink).toBeVisible();
  await modelsLink.click();
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "Models", level: 1 })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Scan Settings", exact: true })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Storage", exact: true })).toBeVisible();
  const headings = await main.getByRole("heading").allTextContents();
  expect(headings.indexOf("Scan Settings")).toBeLessThan(headings.indexOf("Storage"));
  const padding = main.getByRole("spinbutton", { name: "Audio padding (milliseconds)" });
  const save = main.getByRole("button", { name: "Save scan settings" });
  await expect(padding).toHaveValue("100");
  await expect(save).toBeDisabled();
  await expect(main.getByText("Estimated Total Storage Used")).toBeVisible();
  await expectNoHorizontalPageOverflow(page);

  await padding.fill("1001");
  await save.click();
  await expect(main.getByText("Scan settings saved.")).toBeVisible();
  expect(saved).toEqual([1.001]);
  await page.reload();
  await expect(padding).toHaveValue("1001");

  await padding.fill("0");
  await save.click();
  await expect(main.getByText("Scan settings saved.")).toBeVisible();
  expect(saved).toEqual([1.001, 0]);
  await page.reload();
  await expect(padding).toHaveValue("0");
  await expect(main.getByText("Estimated Total Storage Used")).toBeVisible();
});

test("Models shows read-only audio padding for inventory viewers without deployment editing", async ({ page }) => {
  await mockApi(page);
  const signedIn = authenticationStatus("authenticated");
  signedIn.access!.capabilities = signedIn.access!.capabilities.filter((capability) => capability !== "deployment.edit");
  await page.route("**/api/auth/status", (route) => json(route, signedIn));
  await page.route("**/api/settings/models/scans", (route) => json(route, { audio_padding_seconds: 0.3505 }));
  await page.goto("/#/settings/models");
  const main = page.getByRole("main");
  const padding = main.getByRole("spinbutton", { name: "Audio padding (milliseconds)" });
  await expect(padding).toHaveValue("350.5");
  await expect(padding).not.toBeEditable();
  await expect(main.getByRole("button", { name: "Save scan settings" })).toHaveCount(0);
});
