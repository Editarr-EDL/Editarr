import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../../api";
import { DefaultFilters } from "./default-filters";
import { FilterDefaultsChanges } from "../filter-policy-display";
import type { FilterDefaultsSettings, McfRegistry } from "../../api/api-mcf-types";

const registry: McfRegistry = {
  families: [
    { token: "language", label: "Language", categories: [{ token: "language", label: "Language (General)" }, { token: "swearing", label: "Swearing" }] },
    { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }, { token: "weapons", label: "Weapons" }] },
  ],
};

const settings = {
  revision: 4,
  selection_rules: [],
  severity_defaults: [
    { subcategory: "language", severity: "medium" },
    { subcategory: "swearing", severity: "medium" },
    { subcategory: "violence", severity: "medium" },
    { subcategory: "weapons", severity: "medium" },
  ],
};

const chip = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
function pickSeverity(label: string, level: string) {
  fireEvent.click(chip(label));
  fireEvent.click(screen.getByRole("radio", { name: level }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("structured filter defaults settings", () => {
  afterEach(() => {
    cleanup();
    setCsrfToken(null);
    vi.restoreAllMocks();
  });

  it.each(["first", "last"])("ignores an abandoned mount load that finishes %s", async completion => {
    const pending: ((value: Response) => void)[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const path = new URL(String(input), "http://editarr.test").pathname;
      if (path === "/api/mcf-registry") return response(registry);
      return new Promise<Response>(resolve => pending.push(resolve));
    }));
    render(<StrictMode><DefaultFilters /></StrictMode>);
    await waitFor(() => expect(pending).toHaveLength(2));
    if (completion === "first") {
      await act(async () => { pending[0](response(settings)); });
      expect(screen.queryByRole("button", { name: /^Swearing: / })).not.toBeInTheDocument();
      expect(screen.getByText("Loading filter defaults…")).toBeVisible();
    }
    await act(async () => { pending[1](response(settings)); });
    await screen.findByRole("button", { name: /^Swearing: / });
    pickSeverity("Swearing", "High");
    if (completion === "last") {
      await act(async () => { pending[0](response(settings)); });
    }
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: High");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("names added, removed, reordered and edited rules without exposing IDs or audio words", () => {
    const rule = (id: string, label: string) => ({ id, disposition: "deny" as const, evidence_source: "audio" as const,
      category: "language" as const, subcategory: "swearing", label_contains: label, severity: null, mcf_channel: null });
    const previous = { ...settings, selection_rules: [rule("removed-uuid", "fudge"), rule("changed-uuid", "fadge")] } as FilterDefaultsSettings;
    const latest = { ...settings, selection_rules: [rule("changed-uuid", "fudge"), rule("added-uuid", "new")] } as FilterDefaultsSettings;
    render(<FilterDefaultsChanges previous={previous} latest={latest} registry={registry} />);
    const changes = screen.getByRole("list", { name: "Changed instance values" });
    expect(changes).toHaveTextContent("Rule 1: Select Audio, Swearing");
    expect(changes).toHaveTextContent("Rule moved from 2 to 1");
    expect(changes).toHaveTextContent("Rule 2 added");
    expect(changes).toHaveTextContent("Rule 1 removed");
    expect(changes).not.toHaveTextContent("uuid");
    expect(changes).not.toHaveTextContent("fudge");
  });

  it("edits ordered rules and grouped severity defaults through one revisioned save", async () => {
    const request = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/mcf-registry") return response(registry);
      if (url.pathname === "/api/settings/filters" && init?.method === "PUT") {
        return response({ ...JSON.parse(String(init.body)), revision: 5 });
      }
      if (url.pathname === "/api/settings/filters") return response(settings);
      return response({});
    });
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");
    render(<DefaultFilters />);

    expect(await screen.findByText("No rules yet.")).toBeVisible();
    expect(screen.getByText("No unsaved changes")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const first = () => screen.getByRole("listitem", { name: "Rule 1" });
    fireEvent.change(within(first()).getByLabelText("Rule 1 family"), { target: { value: "language" } });
    expect(within(first()).getByLabelText("Rule 1 subcategory")).not.toBeDisabled();
    expect(within(first()).getByRole("option", { name: "Swearing" })).toBeVisible();
    expect(within(first()).queryByRole("option", { name: "Weapons" })).not.toBeInTheDocument();
    fireEvent.change(within(first()).getByLabelText("Rule 1 subcategory"), { target: { value: "swearing" } });
    expect(within(first()).getByRole("option", { name: "Select" })).toBeInTheDocument();
    fireEvent.change(within(first()).getByLabelText("Rule 1 action"), { target: { value: "deny" } });
    pickSeverity("Swearing", "High");
    fireEvent.click(screen.getByRole("button", { name: "Duplicate rule 1" }));
    expect(screen.getAllByRole("listitem", { name: /^Rule \d$/ })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Move rule 2 up" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete rule 2" }));
    expect(screen.getAllByRole("listitem", { name: /^Rule \d$/ })).toHaveLength(1);
    expect(screen.getByText("2 unsaved changes")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Server defaults saved.");
    const saveCall = request.mock.calls.find(([, init]) => init?.method === "PUT");
    const body = JSON.parse(String(saveCall?.[1]?.body));
    expect(body.revision).toBe(4);
    expect(body.selection_rules).toHaveLength(1);
    expect(body.selection_rules[0]).toMatchObject({ disposition: "deny", category: "language", subcategory: "swearing" });
    expect(body.severity_defaults.find((item: { subcategory: string }) => item.subcategory === "swearing").severity).toBe("high");
    expect(new Headers(saveCall?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-token");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("retains visible local changes after conflict until a fresh view is explicitly resolved", async () => {
    let gets = 0;
    const request = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/mcf-registry") return response(registry);
      if (url.pathname === "/api/settings/filters" && init?.method === "PUT") {
        return response({ detail: { code: "stale_settings_revision", message: "Stale", line_number: null, references: null, filter_ids: [], document_fields: [], metadata_candidates: [] } }, 409);
      }
      if (url.pathname === "/api/settings/filters") { gets += 1; return response(gets === 1 ? settings : { ...settings, revision: 5, severity_defaults: settings.severity_defaults.map(value => value.subcategory === "weapons" ? { ...value, severity: "high" } : value) }); }
      return response({});
    });
    vi.stubGlobal("fetch", request);
    render(<DefaultFilters />);

    await screen.findByText("No rules yet.");
    pickSeverity("Swearing", "High");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: Medium");
    pickSeverity("Swearing", "High");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: High");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Read latest filter defaults" }));
    await waitFor(() => expect(gets).toBe(2));
    expect(await screen.findByRole("list", { name: "Changed instance values" })).toHaveTextContent("Weapons severity: Medium to High");
    expect(screen.getByRole("list", { name: "Changed instance values" })).not.toHaveTextContent("Swearing");
    expect(screen.queryByText(/"severity_defaults"/)).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Discard local changes and use latest defaults" }));
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: Medium");
    expect(chip("Weapons")).toHaveAccessibleName("Weapons: High");
  });

  it("retains the revisioned draft and clears each password attempt when enforcement needs proof", async () => {
    const writes: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(String(input), "http://editarr.test").pathname;
      if (path === "/api/mcf-registry") return response(registry);
      if (init?.method !== "PUT") return response(settings);
      const body = JSON.parse(String(init.body));
      writes.push(body);
      if (!body.reauthentication) return response({ code: "reauthentication_required", detail: "Confirm password" }, 403);
      if (body.reauthentication.password === "wrong") return response({ code: "reauthentication_failed", detail: "Wrong password" }, 403);
      return response({ ...settings, severity_defaults: body.severity_defaults, revision: 5 });
    }));
    render(<DefaultFilters />);
    await screen.findByRole("button", { name: /^Swearing: / });
    pickSeverity("Swearing", "High");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm enforced filter changes" });
    fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: "wrong" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save enforced changes" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("That password is incorrect.");
    expect(within(dialog).getByLabelText("Jellyfin password")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: "synthetic-confirmation" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save enforced changes" }));
    await screen.findByText("Server defaults saved.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes).toHaveLength(3);
    expect(writes.map((body) => body.revision)).toEqual([4, 4, 4]);
    expect(writes[0]).not.toHaveProperty("reauthentication");
    expect(writes[2]).toMatchObject({ reauthentication: { password: "synthetic-confirmation" } });
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: High");
  });
});
