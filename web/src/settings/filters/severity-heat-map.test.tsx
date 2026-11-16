import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { McfCategory, Severity } from "../../api/api-mcf-types";
import type { HeatCell } from "./heat-cells";
import { SeverityHeatMap } from "./severity-heat-map";

const cell = (token: string, label: string, family: string, value: Severity, extra: Partial<HeatCell> = {}): HeatCell =>
  ({ token, label, family, value, server: value, changed: false, locked: false, ...extra });

const cells: HeatCell[] = [
  cell("language", "Language (General)", "Language", "medium"),
  cell("swearing", "Swearing", "Language", "medium", { server: "high", changed: true }),
  cell("arachnophobia", "Arachnophobia", "Fear", "medium"),
  cell("rape", "Rape", "Violence", "high", { locked: true }),
];

function Harness({ onChange = vi.fn(), onReset, showPersonalState = true, disabled }: {
  onChange?: (token: McfCategory, severity: Severity) => void; onReset?: (token: McfCategory) => void;
  showPersonalState?: boolean; disabled?: boolean;
}) {
  const [current, setCurrent] = useState(cells);
  return <SeverityHeatMap cells={current} showPersonalState={showPersonalState} disabled={disabled} onReset={onReset}
    onChange={(token, severity) => { onChange(token, severity); setCurrent(current.map(item => item.token === token ? { ...item, value: severity } : item)); }} />;
}

const chip = (name: RegExp) => screen.getByRole("button", { name });

describe("SeverityHeatMap", () => {
  afterEach(cleanup);

  it("groups chips by family with counts, changes, and display labels", () => {
    render(<Harness />);
    const language = screen.getByRole("region", { name: "Language" });
    expect(language).toHaveTextContent("2 categories · 1 changed by you");
    expect(within(language).getByRole("button", { name: "Language (all): Medium" })).toBeInTheDocument();
    expect(within(language).getByRole("button", { name: "Swearing: Medium, changed by you" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Fear" })).toHaveTextContent("1 category");
    expect(chip(/^Rape: High, required by an admin$/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("changed by you");
  });

  it("opens a labelled level picker from a chip and changes level with the arrow keys", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const arachnophobia = chip(/^Arachnophobia/);
    arachnophobia.focus();
    expect(arachnophobia).toHaveFocus();
    fireEvent.click(arachnophobia);
    expect(arachnophobia).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "Arachnophobia" });
    expect(dialog).toHaveTextContent("Fear · Server default Medium");
    expect(within(dialog).getByRole("radio", { name: "Medium" })).toHaveFocus();

    fireEvent.keyDown(within(dialog).getByRole("radio", { name: "Medium" }), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("arachnophobia", "high");
    expect(within(dialog).getByRole("radio", { name: "High" })).toHaveFocus();
    expect(within(dialog).getByRole("radio", { name: "High" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(within(dialog).getByRole("radio", { name: "High" }), { key: "ArrowDown" });
    expect(onChange).toHaveBeenLastCalledWith("arachnophobia", "low");
    expect(within(dialog).getByRole("radio", { name: "Low" })).toHaveFocus();
    expect(chip(/^Arachnophobia: Low$/)).toBeInTheDocument();
  });

  it("leaves a category alone when its current level is picked again", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(chip(/^Arachnophobia/));
    const dialog = screen.getByRole("dialog", { name: "Arachnophobia" });
    fireEvent.click(within(dialog).getByRole("radio", { name: "Medium" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("radio", { name: "High" }));
    expect(onChange).toHaveBeenCalledWith("arachnophobia", "high");
  });

  it("closes the picker on Escape or an outside press and returns focus to the chip", () => {
    render(<Harness />);
    fireEvent.click(chip(/^Arachnophobia/));
    fireEvent.keyDown(screen.getByRole("radio", { name: "Medium" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(chip(/^Arachnophobia/)).toHaveFocus();

    fireEvent.click(chip(/^Swearing/));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(chip(/^Swearing/)).toHaveFocus();
  });

  it("offers a reset only on changed chips when a reset handler is given", () => {
    const onReset = vi.fn();
    const { unmount } = render(<Harness onReset={onReset} />);
    fireEvent.click(chip(/^Arachnophobia/));
    expect(screen.queryByRole("button", { name: "Reset to server default" })).not.toBeInTheDocument();
    fireEvent.click(chip(/^Swearing/));
    expect(screen.getByRole("dialog", { name: "Swearing" })).toHaveTextContent("Language · Server default High");
    fireEvent.click(screen.getByRole("button", { name: "Reset to server default" }));
    expect(onReset).toHaveBeenCalledWith("swearing");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(chip(/^Swearing/)).toHaveFocus();
    unmount();

    render(<Harness />);
    fireEvent.click(chip(/^Swearing/));
    expect(screen.queryByRole("button", { name: "Reset to server default" })).not.toBeInTheDocument();
  });

  it("explains a locked chip instead of letting it change", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(chip(/^Rape/));
    const dialog = screen.getByRole("dialog", { name: "Rape" });
    expect(dialog).toHaveTextContent("Required by an admin at High");
    expect(within(dialog).queryByRole("radio")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disables the level choices while disabled", () => {
    render(<Harness disabled />);
    fireEvent.click(chip(/^Arachnophobia/));
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  });

  it("filters chips by search and hides families without a match", () => {
    render(<Harness />);
    const search = screen.getByRole("searchbox", { name: "Search categories" });
    expect(search).toHaveAttribute("placeholder", "Search 4 categories");
    fireEvent.change(search, { target: { value: "ARACH" } });
    expect(chip(/^Arachnophobia/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Language" })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "(all)" } });
    expect(chip(/^Language \(all\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Swearing/ })).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText("No categories match.")).toBeInTheDocument();
  });

  it("shows only changed chips under the Changed toggle", () => {
    render(<Harness />);
    const changed = screen.getByRole("button", { name: "Changed (1)" });
    fireEvent.click(changed);
    expect(changed).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("region")).toHaveLength(1);
    expect(chip(/^Swearing/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getAllByRole("region")).toHaveLength(3);
  });

  it("hides personal state for server defaults", () => {
    render(<Harness showPersonalState={false} />);
    expect(screen.queryByRole("button", { name: /^Changed/ })).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Legend" })).not.toHaveTextContent("changed by you");
    expect(screen.getByRole("region", { name: "Language" })).not.toHaveTextContent("changed by you");
    fireEvent.click(chip(/^Swearing: Medium$/));
    expect(screen.getByRole("dialog", { name: "Swearing" })).toHaveTextContent("Language · Default for everyone");
  });
});
