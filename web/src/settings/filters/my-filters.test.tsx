import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { personalFilterUpdate } from "../../api/api-personal-filters";
import type { FilterSelectionRule, McfRegistry } from "../../api/api-mcf-types";
import type { FilterPreferences } from "../../api/api-user-access";
import { MyFilters } from "./my-filters";

vi.mock("../../auth/auth-gate", () => ({ useCan: () => true, useOptionalAuthenticationGate: () => null }));

const registry: McfRegistry = { families: [
  { token: "language", label: "Language", categories: [{ token: "swearing", label: "Swearing" }] },
  { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }] },
] };
const rule = (id: string, patch: Partial<FilterSelectionRule>): FilterSelectionRule => ({ id, disposition: "deny", category: null,
  subcategory: null, evidence_source: null, label_contains: null, severity: null, mcf_channel: null, ...patch });
const serverRule = rule("server-swearing", { category: "language", subcategory: "swearing" });
const server = { revision: 2, selection_rules: [serverRule, rule("locked", { category: "language" })],
  severity_defaults: [{ subcategory: "swearing", severity: "high" }, { subcategory: "violence", severity: "medium" }] };
const view: FilterPreferences = { user_id: 2, mode: "live", mode_locked: false, revision: 3, policy_revision: 7, instance_revision: 2,
  enforced_rules: [], enforced_severities: [],
  rules: [{ rule: serverRule, origin: "instance", edited: false, disabled: false, enforced: false }],
  severities: [{ subcategory: "swearing", severity: "high", origin: "instance", enforced: false }, { subcategory: "violence", severity: "medium", origin: "instance", enforced: false }] };
const locked: FilterPreferences = { ...view, mode_locked: true, enforced_rules: ["locked"], enforced_severities: ["swearing"],
  rules: [{ rule: rule("locked", { category: "language" }), origin: "instance", edited: false, disabled: false, enforced: true }],
  severities: [{ subcategory: "swearing", severity: "high", origin: "instance", enforced: true }, view.severities[1]] };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
type Write = { path: string; body: Record<string, unknown> };

/** Serve My filters' reads; `write` answers each PUT. */
function serve(personal: FilterPreferences, write: (path: string, body: Record<string, unknown>) => Response = () => json({ ...personal, revision: personal.revision + 1 })) {
  const writes: Write[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = new URL(String(input), "http://editarr.test").pathname;
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      writes.push({ path, body });
      return write(path, body);
    }
    if (path === "/api/mcf-registry") return json(registry);
    if (path === "/api/settings/filters") return json(server);
    return json(personal);
  }));
  render(<MyFilters />);
  return writes;
}
const chip = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}: `) });
function pickSeverity(label: string, level: string) {
  fireEvent.click(chip(label));
  fireEvent.click(screen.getByRole("radio", { name: level }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
}
const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));

describe("My filters", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("shows a locked mode and required items as read-only and saves only what the person may change", async () => {
    const writes = serve(locked);
    const sync = await screen.findByRole("radio", { name: "Keep me in sync" });
    expect(sync).toBeChecked();
    expect(sync).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Use my own copy" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("An admin requires some of your filters");
    const required = screen.getByRole("listitem", { name: "Rule 1" });
    expect(required).toHaveTextContent("Required by an admin");
    expect(within(required).queryByRole("button")).not.toBeInTheDocument();
    fireEvent.click(chip("Swearing"));
    expect(screen.getByRole("dialog", { name: "Swearing" })).toHaveTextContent("Required by an admin at High");
    expect(screen.queryByRole("radio", { name: "Low" })).not.toBeInTheDocument();
    pickSeverity("Violence \\(all\\)", "Low");
    expect(screen.getByText("1 unsaved change")).toBeVisible();
    save();
    await screen.findByText("Your filters are saved.");
    expect(writes).toEqual([{ path: "/api/settings/filters/personal",
      body: { mode: "live", revision: 3, overrides: [], personal_rules: [], severity_overrides: [{ subcategory: "violence", severity: "low" }] } }]);
    expect(screen.queryByText(/Source:|Policy revision|Effective mode/)).not.toBeInTheDocument();
  });

  it("switches mode as its own action, but not while changes are unsaved", async () => {
    const writes = serve(view, (_path, body) => json({ ...view, mode: body.mode, revision: 4 }));
    const copy = await screen.findByRole("radio", { name: "Use my own copy" });
    pickSeverity("Violence \\(all\\)", "High");
    expect(copy).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("Save or discard your changes below before you switch.");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(chip("Violence \\(all\\)")).toHaveAccessibleName("Violence (all): Medium");
    fireEvent.click(copy);
    expect(await screen.findByText("You now use your own copy.")).toBeVisible();
    expect(writes).toEqual([{ path: "/api/settings/filters/personal/mode", body: { revision: 3, mode: "copy" } }]);
    expect(screen.getByRole("radio", { name: "Use my own copy" })).toBeChecked();
  });

  it("marks copy values that differ from the server and resets them to the server's value", async () => {
    const copy: FilterPreferences = { ...view, mode: "copy",
      rules: [{ rule: serverRule, origin: "copy", edited: false, disabled: false, enforced: false }],
      severities: [{ subcategory: "swearing", severity: "low", origin: "copy", enforced: false }, { subcategory: "violence", severity: "medium", origin: "copy", enforced: false }] };
    const writes = serve(copy);
    await screen.findByRole("radio", { name: "Use my own copy" });
    expect(screen.getByRole("radio", { name: "Use my own copy" })).toBeChecked();
    await waitFor(() => expect(chip("Swearing")).toHaveAccessibleName("Swearing: Low, changed by you"));
    expect(chip("Violence \\(all\\)")).toHaveAccessibleName("Violence (all): Medium");
    expect(screen.getByRole("listitem", { name: "Rule 1" })).toHaveTextContent("Yours");
    fireEvent.click(chip("Swearing"));
    expect(screen.getByRole("dialog", { name: "Swearing" })).toHaveTextContent("Server default High");
    fireEvent.click(screen.getByRole("button", { name: "Reset to server default" }));
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: High");
    save();
    await screen.findByText("Your filters are saved.");
    expect(writes[0].body).toEqual({ mode: "copy", revision: 3, rules: [serverRule],
      severities: [{ subcategory: "swearing", severity: "high" }, { subcategory: "violence", severity: "medium" }] });
  });

  it("in live mode, drops an override and an edited server rule when the person goes back to the server's value", async () => {
    const edited = { ...serverRule, disposition: "allow" as const };
    const live: FilterPreferences = { ...view,
      rules: [{ rule: edited, origin: "instance", edited: true, disabled: false, enforced: false }],
      severities: [{ subcategory: "swearing", severity: "low", origin: "override", enforced: false }, view.severities[1]] };
    const writes = serve(live);
    const card = await screen.findByRole("listitem", { name: "Rule 1" });
    expect(card).toHaveTextContent("KeepServer default · edited by you");
    fireEvent.click(within(card).getByRole("button", { name: "Use server's rule for rule 1" }));
    expect(screen.getByRole("listitem", { name: "Rule 1" })).toHaveTextContent("SelectServer default");
    await waitFor(() => expect(chip("Swearing")).toHaveAccessibleName("Swearing: Low, changed by you"));
    fireEvent.click(chip("Swearing"));
    fireEvent.click(screen.getByRole("button", { name: "Reset to server default" }));
    expect(chip("Swearing")).toHaveAccessibleName("Swearing: High");
    expect(screen.getByText("2 unsaved changes")).toBeVisible();
    save();
    await screen.findByText("Your filters are saved.");
    expect(writes[0].body).toEqual({ mode: "live", revision: 3, overrides: [], personal_rules: [], severity_overrides: [] });
  });

  it("turns off a server rule and adds a personal rule", async () => {
    const writes = serve(view);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off rule 1" }));
    expect(screen.getByRole("listitem", { name: "Rule 1" })).toHaveTextContent("Server default · turned off");
    fireEvent.click(screen.getByRole("button", { name: "Add my rule" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Rule 2 family"), { target: { value: "violence" } });
    expect(screen.getByRole("listitem", { name: "Rule 2" })).toHaveTextContent("Yours");
    save();
    await screen.findByText("Your filters are saved.");
    expect(writes[0].body).toMatchObject({ overrides: [{ rule_id: "server-swearing", disabled: true }],
      personal_rules: [{ disposition: "allow", category: "violence" }] });
  });

  it("keeps edits after a conflict and blocks saving until the person takes the latest version", async () => {
    const writes = serve(view, () => json({ code: "stale_settings_revision", detail: "Changed elsewhere" }, 409));
    await screen.findByRole("radio", { name: "Keep me in sync" });
    pickSeverity("Violence \\(all\\)", "Low");
    save();
    expect(await screen.findByRole("button", { name: "Discard my changes and use the latest version" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Changed elsewhere");
    expect(chip("Violence \\(all\\)")).toHaveAccessibleName("Violence (all): Low, changed by you");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Use my own copy" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Discard my changes and use the latest version" }));
    expect(chip("Violence \\(all\\)")).toHaveAccessibleName("Violence (all): Medium");
    expect(writes).toHaveLength(1);
  });
});

it("writes every unenforced copy value and leaves enforced entries to the policy", () => {
  const copy = { ...locked, mode: "copy" as const };
  expect(personalFilterUpdate(copy, copy)).toEqual({ mode: "copy", revision: 3, rules: [], severities: [{ subcategory: "violence", severity: "medium" }] });
});
