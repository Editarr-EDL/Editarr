import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { defaultModelId, ModelOptions, modelOptionLabel, resolveModelChoice } from "./model-options";

const models = [
  { id: "nudenet", display_name: "NudeNet", recommended: false },
  { id: "qwen3-vl-8b", display_name: "Qwen3-VL 8B", recommended: true },
];

describe("model options", () => {
  it("marks only recommended models in option labels", () => {
    expect(models.map(modelOptionLabel)).toEqual(["NudeNet", "Qwen3-VL 8B (recommended)"]);
    render(<select aria-label="Model"><ModelOptions models={models} /></select>);
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["NudeNet", "Qwen3-VL 8B (recommended)"]);
  });

  it("defaults to the recommended model, otherwise the first offered", () => {
    expect(defaultModelId(models)).toBe("qwen3-vl-8b");
    expect(defaultModelId([{ id: "nudenet", display_name: "NudeNet" }, { id: "shieldstral", display_name: "Shieldstral" }])).toBe("nudenet");
    expect(defaultModelId([])).toBeNull();
  });

  it("keeps a valid choice and replaces an empty or unavailable one", () => {
    expect(resolveModelChoice("nudenet", models)).toBe("nudenet");
    expect(resolveModelChoice("", models)).toBe("qwen3-vl-8b");
    expect(resolveModelChoice("d1-3b", models)).toBe("qwen3-vl-8b");
    expect(resolveModelChoice("", [])).toBe("");
  });
});
