import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ModelOption } from "./model-catalog";
import { ModelPicker } from "./model-picker";
import { revealActiveOption } from "./model-picker-list";

const base = { detects: ["Nudity"], reports: ["Findings"], badges: [], recommended: false, isDefault: false, available: true };
const options: ModelOption[] = [
  { ...base, id: "nudenet", name: "NudeNet", description: "Flags nudity.", status: { kind: "built_in" }, isDefault: true },
  { ...base, id: "qwen3-vl-8b", name: "Qwen3-VL 8B", variant: "MLX 4-bit", description: "Asks about frames.", bestFor: "Full reviews",
    recommended: true, status: { kind: "ready", bytesTotal: 2048 } },
  { ...base, id: "siglip", name: "SigLIP2 Moderation", variant: "NSFW", description: "Scores frames.",
    status: { kind: "downloading", bytesDownloaded: 512, bytesTotal: 2048 } },
  { ...base, id: "d1-3b", name: "d1-3B", description: "Decides.", available: false,
    status: { kind: "unavailable", reason: "Needs transformers 5.14 or newer on this server" } },
];

function Harness({ onChange = () => {}, initial = "nudenet" }: { onChange?: (id: string) => void; initial?: string }) {
  const [value, setValue] = useState(initial);
  return <ModelPicker label="Video model" options={options} value={value} onChange={(id) => { setValue(id); onChange(id); }} />;
}

const trigger = (name: string) => screen.getByRole("button", { name: `Change Video model: ${name}` });
const activeOption = () => {
  const listbox = screen.getByRole("listbox", { name: "Video model" });
  return document.getElementById(listbox.getAttribute("aria-activedescendant") ?? "");
};

afterEach(cleanup);

describe("ModelPicker", () => {
  it("summarizes the selection and lists grouped options with details and status", () => {
    render(<Harness />);
    const button = trigger("NudeNet");
    expect(button).toHaveAttribute("aria-haspopup", "listbox");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Built in · no download")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const listbox = screen.getByRole("listbox", { name: "Video model" });
    expect(listbox).toHaveFocus();
    expect(within(listbox).getByRole("group", { name: "Recommended" })).toBeInTheDocument();
    expect(within(listbox).getByRole("group", { name: "Other models" })).toBeInTheDocument();
    const unavailable = within(listbox).getByRole("group", { name: "Unavailable on this host" });
    const d1 = within(unavailable).getByRole("option", { name: "d1-3B" });
    expect(d1).toHaveAttribute("aria-disabled", "true");
    expect(d1).toHaveAccessibleDescription(expect.stringContaining("Needs transformers 5.14 or newer"));
    expect(within(listbox).getByRole("option", { name: "NudeNet" })).toHaveAttribute("aria-selected", "true");
    expect(within(listbox).getByRole("option", { name: "Qwen3-VL 8B MLX 4-bit" }))
      .toHaveAccessibleDescription(expect.stringContaining("Detects: Nudity · Reports: Findings · Best for: Full reviews"));
    expect(within(listbox).getByText("Downloading 25%")).toBeInTheDocument();
    expect(activeOption()).toHaveTextContent("NudeNet");
  });

  it("selects with the keyboard and returns focus to the trigger", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.keyDown(trigger("NudeNet"), { key: "ArrowDown" });
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "Home" });
    expect(activeOption()).toHaveTextContent("Qwen3-VL 8B");
    fireEvent.keyDown(listbox, { key: "End" });
    expect(activeOption()).toHaveTextContent("d1-3B");
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(listbox, { key: "ArrowUp" });
    expect(activeOption()).toHaveTextContent("SigLIP2");
    fireEvent.keyDown(listbox, { key: " " });
    expect(onChange).toHaveBeenCalledWith("siglip");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger("SigLIP2 Moderation")).toHaveFocus();
  });

  it("supports typeahead and ignores clicks on unavailable rows", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(trigger("NudeNet"));
    const listbox = screen.getByRole("listbox");
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
    fireEvent.keyDown(listbox, { key: "q" });
    expect(activeOption()).toHaveTextContent("Qwen3-VL 8B");
    now.mockReturnValue(2_000);
    fireEvent.keyDown(listbox, { key: "d" });
    fireEvent.keyDown(listbox, { key: "1" });
    expect(activeOption()).toHaveTextContent("d1-3B");
    now.mockRestore();
    fireEvent.click(screen.getByRole("option", { name: "d1-3B" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("option", { name: "Qwen3-VL 8B MLX 4-bit" }));
    expect(onChange).toHaveBeenCalledWith("qwen3-vl-8b");
    expect(screen.getByText("Recommended")).toBeInTheDocument();
  });

  it("closes on Escape without reaching the dialog, and on Tab or an outside press", () => {
    const dialogEscape = vi.fn();
    render(<div onKeyDown={(event) => { if (event.key === "Escape") dialogEscape(); }}><Harness /><button>Outside</button></div>);
    fireEvent.click(trigger("NudeNet"));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(dialogEscape).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger("NudeNet")).toHaveFocus();

    fireEvent.click(trigger("NudeNet"));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Tab" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger("NudeNet")).toHaveFocus();

    fireEvent.click(trigger("NudeNet"));
    act(() => { fireEvent.pointerDown(document.body); });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("disables the trigger without options and shows an unavailable selection's reason", () => {
    const { rerender } = render(<ModelPicker label="Filter" options={[]} value="" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Change Filter: No models available" })).toBeDisabled();
    rerender(<ModelPicker label="Video model" options={options} value="d1-3b" onChange={() => {}} info={<span>help</span>} />);
    expect(trigger("d1-3B")).toHaveAccessibleDescription(expect.stringContaining("Needs transformers 5.14 or newer"));
    expect(screen.getByText("help")).toBeInTheDocument();
  });

  it("scrolls only the listbox to reveal the active option", () => {
    document.body.innerHTML = `<div role="listbox"><div role="group"><div>Heading</div><div role="option">A</div>
      <div role="option" data-active>B</div></div></div>`;
    const listbox = document.querySelector<HTMLElement>('[role="listbox"]')!;
    const rect = (top: number, bottom: number) => () => ({ top, bottom }) as DOMRect;
    listbox.getBoundingClientRect = rect(100, 300);
    const row = document.querySelector<HTMLElement>("[data-active]")!;
    row.getBoundingClientRect = rect(320, 380);
    revealActiveOption(listbox);
    expect(listbox.scrollTop).toBe(80);
    row.getBoundingClientRect = rect(60, 120);
    revealActiveOption(listbox);
    expect(listbox.scrollTop).toBe(40);
  });
});
