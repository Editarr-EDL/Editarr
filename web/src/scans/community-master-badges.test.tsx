import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchVersionAliases } from "../api/api-community-masters";
import { LocallyVerifiedIcon, UnconfirmedLabel } from "./community-master-badges";
import { UNCONFIRMED_TOOLTIP } from "./community-master-format";
import { VersionPicker } from "./version-picker";
import { useState } from "react";

vi.mock("../api/api-community-masters", () => ({ fetchVersionAliases: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); });
beforeEach(() => {
  // InfoButton closes tooltips whose anchor is off screen, so give the anchor a real box.
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 120, 24));
});

it("explains Unconfirmed on hover and keyboard focus", async () => {
  render(<UnconfirmedLabel />);
  const label = screen.getByRole("button", { name: "Unconfirmed" });
  expect(label).toHaveTextContent("Unconfirmed");
  fireEvent.mouseEnter(label);
  expect(screen.getByRole("tooltip")).toHaveTextContent(UNCONFIRMED_TOOLTIP);
  expect(UNCONFIRMED_TOOLTIP).toBe("This version hasn't been verified yet. Its filters come from contributors whose files could only be loosely matched (by ID, title, or runtime). It becomes confirmed once several contributors with matching files submit for it, or after moderator review.");
  fireEvent.blur(label);
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  fireEvent.focus(label);
  expect(label).toHaveAttribute("aria-describedby", screen.getByRole("tooltip").id);
});

it("labels the Locally verified icon with a matching tooltip", () => {
  render(<LocallyVerifiedIcon />);
  fireEvent.focus(screen.getByRole("button", { name: "Locally verified" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Locally verified");
});

function Picker({ onValue }: { onValue: (value: unknown) => void }) {
  const [value, setValue] = useState({ selection: "", otherText: "" });
  onValue(value);
  return <VersionPicker value={value} onChange={setValue} required />;
}

it("lists Theatrical first, requires a choice and reveals optional text for Other", async () => {
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [
    { kind: "alias", aliasId: "theatrical", label: "Theatrical" }, { kind: "alias", aliasId: "extended", label: "Extended Edition" },
    { kind: "other", label: "Other" }] });
  const values: unknown[] = [];
  render(<Picker onValue={(value) => values.push(value)} />);
  const select = await screen.findByRole("combobox", { name: "Version" });
  expect(Array.from((select as HTMLSelectElement).options).map((option) => option.text))
    .toEqual(["Choose a version", "Theatrical", "Extended Edition", "Other"]);
  expect(select).toBeRequired();
  expect(screen.queryByLabelText("Version name (optional)")).toBeNull();
  fireEvent.change(select, { target: { value: "__other__" } });
  fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "IMAX" } });
  expect(values.at(-1)).toEqual({ selection: "__other__", otherText: "IMAX" });
  fireEvent.change(select, { target: { value: "theatrical" } });
  expect(screen.queryByLabelText("Version name (optional)")).toBeNull();
});
