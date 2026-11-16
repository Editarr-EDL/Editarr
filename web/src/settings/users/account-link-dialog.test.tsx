import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import { AccountLinkSection } from "./account-link-section";
import { linkServer, preview } from "./account-link.test-support";
import { ownerPrincipal, unlinkedAccount } from "./users.test-support";

vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn(), useOptionalAuthenticationGate: vi.fn() }));

const OWNER_PASSWORD = "owner synthetic password";
const JELLYFIN_PASSWORD = "jellyfin synthetic password";

function passwordValues() {
  return [...document.querySelectorAll<HTMLInputElement>("input[type=password]")].map(input => input.value);
}

function openLink() {
  render(<AccountLinkSection account={unlinkedAccount} principal={ownerPrincipal} own={false} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Link to Jellyfin" }));
  return screen.getByRole("dialog", { name: "Link to Jellyfin" });
}

function prove(dialog: HTMLElement, jellyfinPassword = JELLYFIN_PASSWORD) {
  fireEvent.change(within(dialog).getByLabelText("Jellyfin username"), { target: { value: "Casey" } });
  fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: jellyfinPassword } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
  const fresh = screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
  fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: OWNER_PASSWORD } });
  fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
}

async function confirm(dialog: HTMLElement, action = "Link account") {
  fireEvent.click(within(dialog).getByRole("button", { name: /^Confirm (re)?link$/ }));
  const fresh = screen.getByRole("dialog", { name: /^Confirm (re)?link$/ });
  fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: OWNER_PASSWORD } });
  fireEvent.click(within(fresh).getByRole("button", { name: action }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: /^Confirm (re)?link$/ })).not.toBeInTheDocument());
}

describe("Account link dialog", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    vi.mocked(useOptionalAuthenticationGate).mockReturnValue(null);
  });

  it("proves both identities, shows the impact before anything changes, and links with the reviewed confirmation", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 200, { state: "completed", preview: preview() });
    const dialog = openLink();
    prove(dialog);
    const impact = await within(dialog).findByRole("region", { name: "Link impact" });
    expect(server.bodies).toEqual([{ route: "proof", body: { reauthentication: { password: OWNER_PASSWORD }, jellyfin: { username: "Casey", password: JELLYFIN_PASSWORD } } }]);
    expect(passwordValues().every(value => value === "")).toBe(true);
    expect(impact).toHaveTextContent("Admin attested. You confirm that this Jellyfin user is the same person");
    expect(impact).toHaveTextContent("Statusdisabled (no Jellyfin link) becomes active. Its existing API key works again.");
    expect(impact).toHaveTextContent("local-3 becomes jf-casey");
    expect(impact).toHaveTextContent("Old files are removed only after every new file is in place");
    expect(impact).toHaveTextContent("Name becomes Casey; avatar comes from Jellyfin. Both become read-only. Email and timezone stay editable.");
    expect(within(impact).getByText("EDL for media movie-1").parentElement).toHaveTextContent("File moves to the new export ID");
    await confirm(dialog);
    expect(server.bodies[1]).toEqual({ route: "link", body: { reauthentication: { password: OWNER_PASSWORD }, proof: "proof-token", confirmation: "a".repeat(64) } });
    expect(within(dialog).getByRole("status")).toHaveTextContent("Linked to Jellyfin user Casey. 1 personal export file now uses export ID jf-casey. Old export files that still held what Editarr wrote were removed. Any other file at an old location was left in place.");
  });

  it("keeps a failed proof in the form with every password cleared and no preview", async () => {
    const server = linkServer();
    server.answer("proof", 422, { code: "jellyfin_proof_failed", detail: "Jellyfin refused the sign-in" });
    const dialog = openLink();
    prove(dialog);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Jellyfin did not accept that username and password.");
    expect(within(dialog).getByLabelText("Jellyfin username")).toHaveValue("Casey");
    expect(passwordValues()).toEqual([""]);
    expect(within(dialog).queryByRole("region", { name: "Link impact" })).not.toBeInTheDocument();
  });

  it("reports an interrupted export write as nothing changed and offers to start again without claiming a link", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 409, { code: "write_failed", detail: "A new export could not be written; nothing changed" });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    await confirm(dialog);
    const stopped = within(dialog).getByRole("region", { name: "Link stopped" });
    expect(stopped).toHaveTextContent("Nothing changed. The account keeps its identity and its existing exports.");
    expect(dialog).not.toHaveTextContent(/Linked to Jellyfin user/);
    fireEvent.click(within(stopped).getByRole("button", { name: "Start again" }));
    expect(within(dialog).getByLabelText("Jellyfin password")).toHaveValue("");
  });

  it("says old files are still awaiting removal when the link committed in the removing state", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 200, { state: "removing", preview: preview() });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    await confirm(dialog);
    const result = within(dialog).getByRole("region", { name: "Link result" });
    expect(result).toHaveTextContent("Some old export files are not removed yet. Editarr retries their removal at startup and every hour.");
    expect(result).not.toHaveTextContent("were removed");
  });

  it("explains a duplicate identity as needing explicit deletion or transfer and offers no confirm or merge", async () => {
    const server = linkServer();
    server.proven({ preview: preview({ blockers: ["jellyfin_identity_in_use"] }) });
    const dialog = openLink();
    prove(dialog);
    const blockers = await within(dialog).findByRole("region", { name: "Link blockers" });
    expect(blockers).toHaveTextContent("Editarr never merges accounts. Delete one of the accounts or transfer its data explicitly, then link again.");
    expect(within(dialog).queryByRole("button", { name: "Confirm link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /merge|unlink/i })).not.toBeInTheDocument();
  });

  it("shows the server's newer preview after a stale confirmation and confirms against it", async () => {
    const server = linkServer();
    server.proven();
    const changed = preview({ exports: [] });
    server.answer("link", 409, { code: "link_preview_stale", detail: "Review", preview: changed, confirmation: "b".repeat(64) });
    server.answer("link", 200, { state: "completed", preview: changed });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    await confirm(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("The impact changed. Review the current preview and confirm again.");
    expect(within(dialog).getByRole("region", { name: "Link impact" })).toHaveTextContent("No personal exports to move.");
    await confirm(dialog);
    expect(server.bodies[2].body).toMatchObject({ proof: "proof-token", confirmation: "b".repeat(64) });
  });

  it("returns to the proof form when the server rejects the proof", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 409, { code: "link_proof_invalid", detail: "Prove both identities again" });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    await confirm(dialog);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("That proof is no longer valid. Prove both identities again.");
    expect(within(dialog).getByRole("button", { name: "Review impact" })).toBeVisible();
  });

  it("asks only for the Jellyfin sign-in and never for the account's own password", () => {
    linkServer();
    const dialog = openLink();
    expect(within(dialog).getAllByLabelText(/password/i)).toHaveLength(1);
    expect(within(dialog).queryByLabelText(/Editarr password/)).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("recorded as an admin attestation");
  });

  it("keeps the review open when only the confirming password was wrong", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 401, { code: "reauthentication_failed", detail: "Wrong password" });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));
    const fresh = screen.getByRole("dialog", { name: "Confirm link" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "wrong" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Link account" }));
    expect(await within(fresh).findByRole("alert")).toHaveTextContent("That password is incorrect.");
    expect(within(dialog).getByRole("region", { name: "Link impact" })).toBeVisible();
  });

  it("keeps the review and the password dialog open when Jellyfin cannot be reached to confirm", async () => {
    const server = linkServer();
    server.proven();
    server.answer("link", 503, { code: "jellyfin_unavailable", detail: "Jellyfin did not answer" });
    const dialog = openLink();
    prove(dialog);
    await within(dialog).findByRole("region", { name: "Link impact" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm link" }));
    const fresh = screen.getByRole("dialog", { name: "Confirm link" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: OWNER_PASSWORD } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Link account" }));
    expect(await within(fresh).findByRole("alert")).toHaveTextContent("Editarr could not reach Jellyfin to check your password. Nothing changed.");
    expect(within(dialog).getByRole("region", { name: "Link impact" })).toBeVisible();
  });
});
