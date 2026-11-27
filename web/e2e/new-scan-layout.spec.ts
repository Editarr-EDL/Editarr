import { expect, test, type Locator } from "@playwright/test";
import { json, mockApi } from "./app-fixtures";
import { chooseModel, chooseScanType } from "./new-scan-actions";
import { chooseWorkflowStep } from "./workflow-actions";

const unavailable = (id: string, display_name: string, stage: string) => ({
  id, display_name, recommended: id === "qwen3-vl-8b", stage, availability: "unavailable", reason: "deferred_gap",
  device: "mlx", weight_requirement: "on_demand", download: null, enrichment_behavior: "none",
});

/** Descendants of `scope` (and `scope` itself) whose content is wider than their box, or that leave its horizontal bounds. */
async function horizontalOverflow(scope: Locator) {
  return scope.evaluate((root) => {
    const bounds = root.getBoundingClientRect();
    return [root, ...root.querySelectorAll<HTMLElement>("*")].flatMap((element) => {
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || style.position === "fixed") return [];
      const box = element.getBoundingClientRect();
      // Skip SVG internals, visually hidden text, and form controls, whose own text may scroll.
      if (box.width <= 1 || element.closest("svg") || /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName)) return [];
      const name = `${element.tagName.toLowerCase()}.${element.className}`;
      const issues: string[] = [];
      const clipsText = /(hidden|clip)/.test(style.overflowX) && style.textOverflow === "ellipsis";
      if (element.scrollWidth > element.clientWidth + 1 && !clipsText) issues.push(`${name} scrollWidth ${element.scrollWidth} > ${element.clientWidth}`);
      if (box.left < bounds.left - 1 || box.right > bounds.right + 1) issues.push(`${name} spans ${box.left}-${box.right} outside ${bounds.left}-${bounds.right}`);
      return issues;
    });
  });
}

test("320px New Scan dialog fits the viewport with audio, video, and advanced settings open", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await mockApi(page);
  await page.route(/\/api\/capabilities$/, (route) => json(route, {
    audio_models: [{ id: "whisper", display_name: "Whisper", recommended: false }],
    audio_filters: [{ id: "better_profanity", display_name: "better-profanity", recommended: false }],
    video_models: [{ id: "nudenet", display_name: "NudeNet", recommended: false }, { id: "d1-3b", display_name: "d1-3B", recommended: false }],
    qwen_models: [
      { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true },
      { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B", recommended: false },
    ],
    // Unavailable models sharing a name (mlx-whisper, Qwen3-VL 8B) make the footer summary add variants.
    model_capabilities: [unavailable("mlx_whisper", "Whisper (mlx-whisper)", "audio"), unavailable("qwen3-vl-8b", "Qwen3-VL 8B", "detector"), unavailable("qwen3_vl", "Qwen3-VL", "enrichment")],
  }));
  await page.goto("/#/library/movies/item/movie-1");
  await chooseWorkflowStep(page, "Scans");
  await page.getByRole("button", { name: "New Scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New Scan" });
  await chooseScanType(dialog, "Audio and video");
  await chooseModel(dialog, "Video model", "d1-3B");
  await dialog.locator("summary").filter({ hasText: "Advanced video settings" }).click();
  const frames = dialog.getByLabel("Representative frames per flagged segment", { exact: true });
  await expect(frames).toBeVisible();
  await dialog.getByLabel("Scan name (optional)", { exact: true }).fill("Director’s cut check with a long name");

  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(320);
  expect(await horizontalOverflow(dialog)).toEqual([]);
  // Scrolling fields into view must not shift the dialog or its body sideways.
  await frames.focus();
  await dialog.getByRole("button", { name: "Add to Queue" }).scrollIntoViewIfNeeded();
  expect(await dialog.evaluate((element) => [element.scrollLeft, element.querySelector(".new-scan-body")!.scrollLeft])).toEqual([0, 0]);
});
