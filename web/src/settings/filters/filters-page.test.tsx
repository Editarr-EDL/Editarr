import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Capability } from "../../auth/capabilities";
import { FiltersPage } from "./filters-page";

let granted = new Set<Capability>();
vi.mock("../../auth/auth-gate", () => ({ useCan: (capability: Capability) => granted.has(capability) }));
vi.mock("./my-filters", () => ({ MyFilters: () => <input aria-label="My draft" /> }));
vi.mock("./new-account-mode", () => ({ NewAccountMode: () => <h2>For new accounts</h2> }));
vi.mock("./default-filters", () => ({ DefaultFilters: () => <h2>Default rules</h2> }));

const everything: Capability[] = ["filters.preferences.edit.own", "filters.mode.choose.own", "filters.instance_defaults.edit", "filters.instance_mode.edit"];

describe("FiltersPage", () => {
  beforeEach(() => { granted = new Set(everything); window.location.hash = "#/settings/filters"; });
  afterEach(cleanup);

  it("shows My filters first and moves to Server defaults through the address", () => {
    const { rerender } = render(<FiltersPage />);
    expect(screen.getByRole("tab", { name: "My filters" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "My filters" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Default rules" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("My draft"), { target: { value: "unsaved" } });
    fireEvent.click(screen.getByRole("tab", { name: "Server defaults" }));
    expect(window.location.hash).toBe("#settings/filters/server");
    rerender(<FiltersPage tab="server" />);
    expect(screen.getByRole("tab", { name: "Server defaults" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "For new accounts" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Default rules" })).toBeVisible();
    rerender(<FiltersPage />);
    expect(screen.getByLabelText("My draft")).toHaveValue("unsaved");
  });

  it("moves between tabs with the arrow keys", () => {
    render(<FiltersPage />);
    act(() => { fireEvent.keyDown(screen.getByRole("tab", { name: "My filters" }), { key: "ArrowRight" }); });
    expect(window.location.hash).toBe("#settings/filters/server");
    expect(screen.getByRole("tab", { name: "Server defaults" })).toHaveFocus();
  });

  it("shows only My filters, with no tab bar, to an account without server capabilities", () => {
    granted = new Set(["filters.preferences.edit.own"]);
    render(<FiltersPage tab="server" />);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByLabelText("My draft")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Default rules" })).not.toBeInTheDocument();
  });

  it("shows only Server defaults, with no tab bar, to an account without personal capabilities", () => {
    granted = new Set(["filters.instance_defaults.edit"]);
    render(<FiltersPage />);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Default rules" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "For new accounts" })).not.toBeInTheDocument();
  });
});
