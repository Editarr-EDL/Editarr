import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FilterSelectionRule, McfRegistry } from "../../api/api-mcf-types";
import { newFilterRule } from "../filter-rule-fields";
import { RuleList } from "./rule-list";

const registry: McfRegistry = {
  families: [
    { token: "language", label: "Language", categories: [{ token: "swearing", label: "Swearing" }] },
    { token: "sex", label: "Sex", categories: [{ token: "kissing", label: "Kissing" }] },
  ],
};

const rule = (id: string, patch: Partial<FilterSelectionRule>): FilterSelectionRule => ({ ...newFilterRule(), id, ...patch });
const swearing = rule("swearing", { disposition: "deny", category: "language", subcategory: "swearing", severity: "high", evidence_source: "audio" });
const sex = rule("sex", { disposition: "deny", category: "sex" });
const kissing = rule("kissing", { disposition: "allow", category: "sex", subcategory: "kissing" });

describe("RuleList", () => {
  afterEach(cleanup);

  it("numbers rules, names their action, and summarizes what they match", () => {
    render(<RuleList registry={registry} entries={[
      { rule: swearing, badge: "server" }, { rule: kissing, badge: "off" }, { rule: sex, badge: "required" }, { rule: rule("mine", { disposition: "deny", category: "sex" }), badge: "yours" },
    ]} />);
    const first = screen.getByRole("listitem", { name: "Rule 1" });
    expect(first).toHaveTextContent("1Select");
    expect(first).toHaveTextContent("Server default");
    expect(first).toHaveTextContent("Language › Swearing · High severity · Audio");
    expect(screen.getByRole("listitem", { name: "Rule 2" })).toHaveTextContent("KeepServer default · turned off");
    expect(screen.getByRole("listitem", { name: "Rule 3" })).toHaveTextContent("Required by an admin");
    expect(screen.getByRole("listitem", { name: "Rule 4" })).toHaveTextContent("Yours");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers only the actions the caller supplies", () => {
    const onToggle = vi.fn();
    const onDelete = vi.fn();
    const onRestore = vi.fn();
    const onDuplicate = vi.fn();
    render(<RuleList registry={registry} entries={[
      { rule: swearing, badge: "server", onToggle }, { rule: kissing, badge: "off", onToggle, onRestore }, { rule: sex, badge: "yours", onDelete, onDuplicate },
    ]} />);
    expect(screen.getByRole("listitem", { name: "Rule 2" })).toHaveTextContent("Use server's rule");
    fireEvent.click(screen.getByRole("button", { name: "Turn off rule 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Turn on rule 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Use server's rule for rule 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Duplicate rule 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete rule 3" }));
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onDuplicate).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /^Use server's rule for rule [13]$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Move/ })).not.toBeInTheDocument();
  });

  it("expands a card into the rule fields for editing and collapses it again", () => {
    const onChange = vi.fn();
    render(<RuleList registry={registry} entries={[{ rule: swearing, badge: "yours", onChange }]} />);
    const edit = screen.getByRole("button", { name: "Edit rule 1" });
    expect(edit).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("group", { name: "Rule 1 fields" })).not.toBeInTheDocument();
    fireEvent.click(edit);
    const fields = screen.getByRole("group", { name: "Rule 1 fields" });
    fireEvent.change(within(fields).getByRole("combobox", { name: "Rule 1 severity" }), { target: { value: "low" } });
    expect(onChange).toHaveBeenCalledWith({ severity: "low" });
    fireEvent.click(screen.getByRole("button", { name: "Done editing rule 1" }));
    expect(screen.queryByRole("group", { name: "Rule 1 fields" })).not.toBeInTheDocument();
  });

  it("keeps a new rule open until it matches something", () => {
    function Harness() {
      const [draft, setDraft] = useState(rule("new", {}));
      return <RuleList registry={registry} entries={[{ rule: draft, badge: "yours", onChange: patch => setDraft({ ...draft, ...patch }) }]} />;
    }
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Rule 1 fields" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done editing rule 1" })).toBeDisabled();
    fireEvent.change(screen.getByRole("combobox", { name: "Rule 1 family" }), { target: { value: "sex" } });
    expect(screen.getByRole("group", { name: "Rule 1 fields" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done editing rule 1" }));
    expect(screen.queryByRole("group", { name: "Rule 1 fields" })).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Rule 1" })).toHaveTextContent("Sex");
  });

  it("moves rules up and down within the list", () => {
    const onMove = vi.fn();
    render(<RuleList registry={registry} onMove={onMove} entries={[{ rule: swearing }, { rule: sex }, { rule: kissing }]} />);
    expect(screen.getByRole("button", { name: "Move rule 1 up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move rule 3 down" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Move rule 2 up" }));
    expect(onMove).toHaveBeenLastCalledWith(1, 0);
    fireEvent.click(screen.getByRole("button", { name: "Move rule 2 down" }));
    expect(onMove).toHaveBeenLastCalledWith(1, 2);
  });

  it("disables every action while disabled", () => {
    render(<RuleList registry={registry} disabled onMove={vi.fn()} entries={[{ rule: swearing, onChange: vi.fn(), onToggle: vi.fn(), onDelete: vi.fn() }]} />);
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });
});
