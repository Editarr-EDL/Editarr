import assert from "node:assert/strict";
import { closeModelPicker, modelOption, openModelPicker } from "./new-scan-controls.mjs";

const SEVERITY = "Severity and Description model";
const DESCRIPTION_HELP = "Describes flagged scenes and rates their severity when the video model doesn't.";
const defaultModel = models => (models.find(model => model.recommended) ?? models[0])?.id;

/**
 * The options a picker must list, derived from `/api/capabilities` alone: offered models are available unless
 * their capability record is unavailable, and video also lists the detectors this host can't run.
 * Description models share the `qwen3_vl` enrichment's availability.
 */
function expectedOptions(capabilities, role) {
  const byId = new Map(capabilities.model_capabilities.map(model => [model.id, model]));
  if (role === "severity") {
    const available = byId.get("qwen3_vl")?.availability !== "unavailable";
    return capabilities.qwen_models.map(model => ({ id: model.id, available, recommended: available && model.recommended }));
  }
  const offered = new Set(capabilities.video_models.map(model => model.id));
  return [
    ...capabilities.video_models.map(model => {
      const available = byId.get(model.id)?.availability !== "unavailable";
      return { id: model.id, available, recommended: available && model.recommended };
    }),
    ...capabilities.model_capabilities
      .filter(model => model.stage === "detector" && model.availability === "unavailable" && !offered.has(model.id))
      .map(model => ({ id: model.id, available: false, recommended: false })),
  ];
}

/** Open one picker and require its options, availability, grouping, and (when `fresh`) default selection. */
async function verifyPicker({ dialog, label, options, defaultId, fresh, expect }) {
  const listbox = await openModelPicker(dialog, label, expect);
  await expect(listbox.getByRole("option")).toHaveCount(options.length);
  const recommended = listbox.getByRole("group", { name: "Recommended", exact: true });
  const unavailable = listbox.getByRole("group", { name: "Unavailable on this host", exact: true });
  for (const option of options) {
    const row = modelOption(listbox, option.id);
    await expect(row, `${label} offers ${option.id}`).toHaveCount(1);
    if (option.available) await expect(row).not.toHaveAttribute("aria-disabled", "true");
    else await expect(row).toHaveAttribute("aria-disabled", "true");
    await expect(modelOption(recommended, option.id)).toHaveCount(option.recommended ? 1 : 0);
    await expect(modelOption(unavailable, option.id)).toHaveCount(option.available ? 0 : 1);
  }
  if (fresh) await expect(modelOption(listbox, defaultId)).toHaveAttribute("aria-selected", "true");
  await closeModelPicker(dialog, label, listbox, expect);
  return options;
}

/**
 * Check the New Scan model pickers against `/api/capabilities`: listed options, aria-disabled unavailable rows,
 * the Recommended group, and the Severity and Description help. `fresh` names the pickers not yet changed on this
 * page (`qwen`, `video`); only those must still select the capabilities default. d1-3B must be offered; Qwen3-VL
 * video models are recommended and default only where MLX is available (scan-ui simulates Linux, so NudeNet stays
 * the default and the Qwen3-VL rows are unavailable there). Returns each role's expected options.
 */
export async function verifyModelChoices({ dialog, capabilities, mode, fresh, expect }) {
  for (const list of [capabilities.video_models, capabilities.qwen_models]) {
    assert.ok(list.every(model => typeof model.recommended === "boolean"), "every model choice declares recommended");
  }
  assert.deepEqual(capabilities.qwen_models.filter(model => model.recommended).map(model => model.id), ["qwen3-vl-8b-instruct-4bit"]);
  const help = dialog.getByRole("button", { name: `About the ${SEVERITY}`, exact: true });
  await help.click();
  await expect(dialog.page().getByRole("tooltip")).toContainText(DESCRIPTION_HELP);
  await help.click();
  await expect(dialog.page().getByRole("tooltip")).toHaveCount(0);
  const severity = await verifyPicker({ dialog, label: SEVERITY, options: expectedOptions(capabilities, "severity"),
    defaultId: defaultModel(capabilities.qwen_models), fresh: fresh.has("qwen"), expect });
  if (mode === "audio") return { severity };
  const ids = capabilities.video_models.map(model => model.id);
  assert.ok(ids.includes("d1-3b"), "video model d1-3b is offered");
  assert.equal(defaultModel(capabilities.video_models), ids.includes("qwen3-vl-8b") ? "qwen3-vl-8b" : ids[0]);
  assert.ok(capabilities.video_models.filter(model => model.recommended).every(model => model.id === "qwen3-vl-8b"), "only Qwen3-VL 8B is recommended");
  const video = await verifyPicker({ dialog, label: "Video model", options: expectedOptions(capabilities, "video"),
    defaultId: defaultModel(capabilities.video_models), fresh: fresh.has("video"), expect });
  return { severity, video };
}
