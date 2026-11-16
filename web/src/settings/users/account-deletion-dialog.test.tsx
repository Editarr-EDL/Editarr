import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePrincipal } from "../../auth/auth-gate";
import { ModalDialog } from "../../ui/ModalDialog";
import { AccountDeletionDialog } from "./account-deletion-dialog";
import { deletionPreview, deletionResult, deletionServer, publication, summary } from "./account-deletion.test-support";
import { ownerPrincipal } from "./users.test-support";

vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn() }));

const PASSWORD = "owner synthetic password";

function open(onDeleted = vi.fn()) {
  render(<AccountDeletionDialog target={{ id: 3, name: "Pat" }} onClose={vi.fn()} onDeleted={onDeleted} />);
  return screen.getByRole("dialog", { name: "Delete Pat" });
}

function choose(dialog: HTMLElement, mode: RegExp) {
  fireEvent.click(within(dialog).getByRole("radio", { name: mode }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
}

async function confirm(password = PASSWORD) {
  const dialog = await screen.findByRole("dialog", { name: "Delete Pat" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete account" }));
  const fresh = screen.getByRole("dialog", { name: "Confirm deleting Pat" });
  fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: password } });
  fireEvent.click(within(fresh).getByRole("button", { name: "Delete account" }));
}

describe("Account deletion dialog", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal); });

  it("asks whether a linked account's Jellyfin user may come back and sends Block them", async () => {
    const server = deletionServer();
    const dialog = open();
    server.answer("preview", 200, deletionPreview({ credentials: { sessions: 1, api_key: false, linked: true } }));
    choose(dialog, /Keep data/);
    const choice = await within(dialog).findByRole("group", { name: "After deletion, their Jellyfin user" });
    expect(within(choice).getByRole("radio", { name: /Allow them back/ })).toBeChecked();
    expect(choice).toHaveTextContent("Their next Jellyfin sign-in creates a fresh Editarr account.");
    expect(choice).toHaveTextContent("They can't sign in or be imported until the owner unblocks them in Users.");
    fireEvent.click(within(choice).getByRole("radio", { name: /Block them/ }));
    server.answer("delete", 200, deletionResult({ identity_released: true, jellyfin_blocked: true }));
    await confirm();
    const result = await within(dialog).findByRole("region", { name: "Deletion result" });
    expect(server.requests.filter(request => request.route === "delete").map(request => (request.body as { jellyfin_user: string }).jellyfin_user)).toEqual(["block"]);
    expect(result).toHaveTextContent("Jellyfin user blocked until the owner unblocks them in Users");
  });

  it("offers no Jellyfin choice for a local account", async () => {
    const server = deletionServer();
    const dialog = open();
    server.answer("preview", 200, deletionPreview());
    choose(dialog, /Keep data/);
    await within(dialog).findByRole("region", { name: "Deletion impact" });
    expect(within(dialog).queryByRole("group", { name: /Jellyfin user/ })).not.toBeInTheDocument();
  });

  it("requires a choice, shows retention's consequences, and on a stale preview applies only the re-reviewed confirmation", async () => {
    const server = deletionServer();
    const onDeleted = vi.fn();
    const dialog = open(onDeleted);
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Choose what happens to the account's data.");
    expect(server.requests).toEqual([]);

    server.answer("preview", 200, deletionPreview());
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    await new Promise(resolve => requestAnimationFrame(resolve));
    within(dialog).getByRole("button", { name: "Review impact" }).focus();
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const impact = await within(dialog).findByRole("region", { name: "Deletion impact" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(impact).toHaveTextContent("2 saved reviews stay unchanged");
    expect(impact).toHaveTextContent("2 personal export files stay published exactly as they are");
    expect(impact).toHaveTextContent("Anything already shared with the community stays published; nothing is retracted");
    expect(impact).not.toHaveTextContent(/retracted automatically|will be retracted/i);
    expect(within(impact).getByRole("region", { name: "Published files of Pat" })).toHaveTextContent("EDL · movie-1");

    server.refuse("delete", 409, "stale_account_deletion");
    server.answer("preview", 200, deletionPreview({ confirmation: "b".repeat(64), reviews: { count: 3, hidden: 0, ids: ["review-1", "review-2", "review-3"] } }));
    await confirm();
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion impact" })).toHaveTextContent("3 saved reviews stay unchanged"));
    const review = within(dialog).getByRole("region", { name: "Deletion impact" });
    expect(within(review).getByRole("alert")).toHaveTextContent("The account changed after this preview. Nothing changed. Review the current impact and confirm again.");
    expect(onDeleted).not.toHaveBeenCalled();

    server.answer("delete", 200, deletionResult());
    await confirm();
    await within(dialog).findByRole("region", { name: "Deletion result" });
    const posts = server.requests.filter(request => request.route === "delete").map(request => request.body);
    expect(posts).toEqual([
      { mode: "retain", jellyfin_user: "allow", confirmation: "a".repeat(64), files: [], reauthentication: { password: PASSWORD } },
      { mode: "retain", jellyfin_user: "allow", confirmation: "b".repeat(64), files: [], reauthentication: { password: PASSWORD } },
    ]);
    expect(server.requests.filter(request => request.route === "preview").map(request => new URL(request.url, "http://x").search)).toEqual(["?mode=retain", "?mode=retain"]);
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("region", { name: "Deletion result" })).toHaveTextContent("2 reviews kept unchanged.");
    expect([...document.querySelectorAll<HTMLInputElement>("input[type=password]")].every(input => input.value === "")).toBe(true);
  });

  it("transfers to a chosen destination and shows the new owner and policy effect", async () => {
    const server = deletionServer();
    server.accounts([summary(3, "Pat"), summary(5, "Robin"), summary(6, "Gone", { status: "disabled" })]);
    const dialog = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /Transfer data/ }));
    const select = await within(dialog).findByLabelText("Destination account");
    expect(within(select).getAllByRole("option").map(option => option.textContent)).toEqual(["Choose an account", "Robin"]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Choose the account that receives the data.");
    fireEvent.change(select, { target: { value: "5" } });
    server.answer("preview", 200, deletionPreview({ mode: "transfer", destination_id: 5, transfer: { files_moved: 2, policy_files: 1 }, publications: { hidden: 0, items: [
      publication(11),
      publication(12, { review_id: "review-9", policy_owner_id: 7, actor_id: 7, original_author_id: 7, target: "review_owner" }),
      publication(13, { review_id: "review-2", target_user_id: 8, target_export_id: "local-8" }),
    ] } }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const impact = await within(dialog).findByRole("region", { name: "Deletion impact" });
    expect(impact).toHaveTextContent("2 saved reviews move to Robin. Robin becomes the owner; Pat stays recorded as the original author.");
    expect(impact).toHaveTextContent("2 personal export files move to Robin's export ID. 2 listed files follow Robin's filter policy as the new owner of their reviews. 1 listed file comes from another account's review and keeps that review owner's filter policy. 1 kept file from deleted reviews now follows Robin's filter policy. Every affected file is regenerated.");
    expect(impact).not.toHaveTextContent("regenerated with Robin's filter policy");
    const files = within(within(impact).getByRole("region", { name: "Published files of Pat" })).getAllByRole("listitem").map(item => item.textContent);
    expect(files).toEqual([
      "EDL · movie-1: moves to Robin's export ID; follows Robin's filter policy",
      "MCF · movie-1: moves to Robin's export ID; keeps its review owner's filter policy",
      "EDL · movie-1: stays with its recipient; follows Robin's filter policy",
    ]);
    server.answer("delete", 200, deletionResult({ mode: "transfer", destination_id: 5, reviews_retained: [], reviews_transferred: ["review-1", "review-2"], files_moved: 2 }));
    await confirm();
    await within(dialog).findByRole("region", { name: "Deletion result" });
    expect(server.requests.find(request => request.route === "preview")!.url).toContain("mode=transfer&destination=5");
    expect(server.requests.find(request => request.route === "delete")!.body).toMatchObject({ mode: "transfer", destination_id: 5 });
    expect(within(dialog).getByRole("region", { name: "Deletion result" })).toHaveTextContent("2 reviews transferred to Robin. 2 personal export files moved.");
  });

  it("names transfer blockers by count and offers no confirmation while blocked", async () => {
    const server = deletionServer();
    server.accounts([summary(5, "Robin")]);
    const dialog = open();
    fireEvent.click(within(dialog).getByRole("radio", { name: /Transfer data/ }));
    fireEvent.change(await within(dialog).findByLabelText("Destination account"), { target: { value: "5" } });
    server.answer("preview", 200, deletionPreview({ mode: "transfer", destination_id: 5, reviews: { count: 3, hidden: 1, ids: ["review-1", "review-2"] }, transfer: { files_moved: 2, policy_files: 0 }, blockers: [
      { code: "export_filename_collision", count: 1 }, { code: "reviews_outside_access", count: 1 }, { code: "transfer_media_inaccessible", count: 2 },
    ] }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const blockers = await within(dialog).findByRole("region", { name: "Deletion blockers for Pat" });
    expect(blockers).toHaveTextContent("1 moved file would take a file name the destination already publishes.");
    expect(blockers).toHaveTextContent("1 review by this account is hidden from you, so you can't delete or transfer it.");
    expect(blockers).toHaveTextContent("2 reviews are on media the destination can't access. Nothing is transferred partially.");
    expect(within(dialog).getByRole("region", { name: "Deletion impact" })).toHaveTextContent("3 saved reviews, 1 of them hidden from you");
    expect(within(dialog).queryByRole("button", { name: "Delete account" })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Change choice" }));
    expect(within(dialog).getByRole("radio", { name: /Transfer data/ })).toBeChecked();
  });

  it("permanent deletion keeps files by default, sends each explicit choice, and shows scans that surviving reviews need", async () => {
    const server = deletionServer();
    const dialog = open();
    server.answer("preview", 200, deletionPreview({ mode: "delete", reviews: { count: 1, hidden: 0, ids: ["review-1"] },
      files: [{ review_id: "review-1", publication: publication(11), choices: ["remove", "retain"] }, { review_id: "review-1", publication: publication(12, { scope: "default" }), choices: ["retain"] }],
      scans: { hidden: 0, items: [{ scan_id: "scan-shared-1", status: "completed", required: true }, { scan_id: "scan-alone-2", status: "completed", required: false }] } }));
    choose(dialog, /Delete permanently/);
    const impact = await within(dialog).findByRole("region", { name: "Deletion impact" });
    expect(impact).toHaveTextContent("1 scan is kept because another account's review, a file outside this account's reviews, or pending community work needs it. 1 scan is deleted unless a published file that stays still uses it.");
    expect(impact).not.toHaveTextContent("1 scan is deleted.");
    const scans = within(within(impact).getByRole("region", { name: "Scans of Pat" })).getAllByRole("listitem").map(item => item.textContent);
    expect(scans).toEqual(["Scan scan-sha (completed): kept, still needed", "Scan scan-alo (completed): deleted unless a published file that stays still uses it"]);
    const edl = within(impact).getByRole("group", { name: "EDL · movie-1" });
    expect(within(edl).getByRole("radio", { name: "Keep file" })).toBeChecked();
    expect(within(impact).getByRole("group", { name: "MCF · movie-1 · item default" })).toHaveTextContent("You can't remove this file, so it is kept.");
    fireEvent.click(within(edl).getByRole("radio", { name: "Remove file" }));
    server.answer("delete", 200, deletionResult({ mode: "delete", reviews_retained: [], reviews_deleted: ["review-1"], files_retained: [12], files_removed: [11], scans_deleted: ["scan-alone-2"], scans_kept: ["scan-shared-1"] }));
    await confirm();
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion result" })).toHaveTextContent("1 review deleted. 1 published file kept, 1 removed."));
    expect(server.requests.find(request => request.route === "delete")!.body).toMatchObject({ mode: "delete", files: [{ export_id: 11, choice: "remove" }, { export_id: 12, choice: "retain" }] });
  });

  it("reports an unknown outcome and asks for a refresh when the connection drops after confirming", async () => {
    const server = deletionServer();
    const onDeleted = vi.fn();
    const dialog = open(onDeleted);
    server.answer("preview", 200, deletionPreview());
    choose(dialog, /Keep data/);
    server.disconnect("delete");
    await confirm();
    const stopped = await within(dialog).findByRole("region", { name: "Deletion stopped" });
    expect(stopped).toHaveTextContent("Editarr did not confirm whether Pat was deleted. Check the Users list before trying again.");
    expect(stopped).not.toHaveTextContent(/Nothing changed|Failed to fetch/);
    expect(onDeleted).toHaveBeenCalledWith({ userId: 3, name: "Pat", state: "unknown", lines: [expect.stringContaining("did not confirm whether Pat was deleted")] });
  });

  it("reports an unknown outcome when Editarr answers busy, since a transfer may already have committed", async () => {
    const server = deletionServer();
    const onDeleted = vi.fn();
    const dialog = open(onDeleted);
    server.answer("preview", 200, deletionPreview());
    choose(dialog, /Keep data/);
    server.refuse("delete", 503, "database_busy");
    await confirm();
    const stopped = await within(dialog).findByRole("region", { name: "Deletion stopped" });
    expect(stopped).toHaveTextContent("Editarr did not confirm whether Pat was deleted.");
    expect(stopped).not.toHaveTextContent(/Nothing changed|busy/);
    expect(onDeleted).toHaveBeenCalledWith(expect.objectContaining({ userId: 3, state: "unknown" }));
  });

  it("keeps a wrong password in the confirmation dialog and stops on a protected account", async () => {
    const server = deletionServer();
    const onDeleted = vi.fn();
    const dialog = open(onDeleted);
    server.answer("preview", 200, deletionPreview());
    choose(dialog, /Keep data/);
    server.refuse("delete", 403, "reauthentication_failed");
    await confirm("wrong");
    const fresh = screen.getByRole("dialog", { name: "Confirm deleting Pat" });
    expect(await within(fresh).findByRole("alert")).toHaveTextContent("That password is incorrect.");
    server.refuse("delete", 403, "protected_account");
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: PASSWORD } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Delete account" }));
    expect(await within(dialog).findByRole("region", { name: "Deletion stopped" })).toHaveTextContent("This account is protected.");
    expect(onDeleted).not.toHaveBeenCalled();
  });
});

describe("Account deletion dialog opening focus", () => {
  let frames: FrameRequestCallback[] = [];
  const nextFrame = () => act(() => { const due = frames; frames = []; due.forEach(callback => callback(0)); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    frames = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
    vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames[id - 1] = () => undefined; });
  });

  /** The duplicate-identity path: the deletion dialog opens from a button inside the link dialog. */
  function Opener() {
    const [open, setOpen] = useState(false);
    return <ModalDialog labelledBy="link"><h2 id="link">Link to Jellyfin</h2>
      <button type="button" onClick={() => setOpen(true)}>Review deleting Pat</button>
      {open && <AccountDeletionDialog target={{ id: 3, name: "Pat" }} onClose={vi.fn()} onDeleted={vi.fn()} />}
    </ModalDialog>;
  }

  function openFromLink() {
    render(<Opener />);
    nextFrame();
    const trigger = screen.getByRole("button", { name: "Review deleting Pat" });
    trigger.focus();
    fireEvent.click(trigger);
    return screen.getByRole("dialog", { name: "Delete Pat" });
  }

  it("opens on its Close button", () => {
    const dialog = openFromLink();
    nextFrame();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close" }));
  });

  it("keeps the control Tab reached before a late opening frame", () => {
    const dialog = openFromLink();
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    const reached = within(dialog).getByRole("radio", { name: /Keep data/ });
    expect(document.activeElement).toBe(reached);
    nextFrame();
    expect(document.activeElement).toBe(reached);
  });
});
