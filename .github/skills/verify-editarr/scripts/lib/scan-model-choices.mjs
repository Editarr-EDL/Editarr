import assert from "node:assert/strict";

const DESCRIPTION_HELP = "Describes flagged scenes and rates their severity when the video model doesn't.";
const optionLabel = model => model.recommended ? `${model.display_name} (recommended)` : model.display_name;
const defaultModel = models => (models.find(model => model.recommended) ?? models[0])?.id;

/**
 * Check the New Scan model selects against `/api/capabilities`: recommended labels and the
 * Severity and Description model help. `fresh` names the selects not yet changed on this page
 * (`qwen`, `video`); only those must still show the capabilities default.
 * d1-3B must be offered; Qwen3-VL video models are recommended and default only where MLX is available
 * (scan-ui simulates Linux, so NudeNet stays the default there).
 */
export async function verifyModelChoices({ dialog, capabilities, mode, fresh, expect }) {
  for (const list of [capabilities.video_models, capabilities.qwen_models]) {
    assert.ok(list.every(model => typeof model.recommended === "boolean"), "every model choice declares recommended");
  }
  assert.deepEqual(capabilities.qwen_models.filter(model => model.recommended).map(model => model.id), ["qwen3-vl-8b-instruct-4bit"]);
  const severity = dialog.getByRole("combobox", { name: "Severity and Description model", exact: true });
  await expect(severity).toHaveAccessibleDescription(DESCRIPTION_HELP);
  await expect(severity.locator("option")).toHaveText(capabilities.qwen_models.map(optionLabel));
  if (fresh.has("qwen")) await expect(severity).toHaveValue(defaultModel(capabilities.qwen_models));
  if (mode === "audio") return;
  const ids = capabilities.video_models.map(model => model.id);
  assert.ok(ids.includes("d1-3b"), "video model d1-3b is offered");
  assert.equal(defaultModel(capabilities.video_models), ids.includes("qwen3-vl-8b") ? "qwen3-vl-8b" : ids[0]);
  assert.ok(capabilities.video_models.filter(model => model.recommended).every(model => model.id === "qwen3-vl-8b"), "only Qwen3-VL 8B is recommended");
  const video = dialog.getByLabel("Video model", { exact: true });
  await expect(video.locator("option")).toHaveText(capabilities.video_models.map(optionLabel));
  if (fresh.has("video")) await expect(video).toHaveValue(defaultModel(capabilities.video_models));
}
