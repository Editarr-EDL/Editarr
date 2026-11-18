import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { signedInStatus } from "../test/app-test-fixtures";
import { AuthenticationGate } from "./auth-gate";
import { AdministratorLogin } from "./auth-login";
import { JellyfinSetup, validateJellyfinSetup } from "./auth-setup";

const authenticatedStatus = { ...signedInStatus(), csrf_token: "csrf-token" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function setupRequiredStatus() {
  return { ...authenticatedStatus, state: "setup_required" as const, setup_required: true, csrf_token: null };
}

function renderSetup() {
  return render(<AuthenticationGate setupRequired={<JellyfinSetup />} unauthenticated={<h1>Sign in required</h1>}><h1>Protected application</h1></AuthenticationGate>);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("JellyfinSetup", () => {
  it("offers restore during setup and returns to connecting Jellyfin", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ token: "disposable-flow" }));
    vi.stubGlobal("fetch", request);
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Restore from backup" }));
    expect(await screen.findByRole("heading", { name: "RESTORE FROM BACKUP" })).toBeVisible();
    expect(screen.getByLabelText("Backup file")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Create backup" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect a Jellyfin server instead" }));
    expect(screen.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("names an installation whose database schema cannot be restored over", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ detail: { code: "restore_target_unsupported_schema", message: "unsupported" } }, 409)));
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Restore from backup" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not a version this Editarr can restore over");
  });

  it("names why a non-empty installation cannot restore and keeps Jellyfin setup available", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ detail: { code: "restore_target_has_work", message: "This installation already has data in scans" } }, 409));
    vi.stubGlobal("fetch", request);
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Restore from backup" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has scans, reviews, or settings");
    expect(screen.getByLabelText("Backup file")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Connect a Jellyfin server instead" }));
    expect(screen.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
  });

  it("restores a full backup, then shows the review notice before sign-in", async () => {
    const restoreJob = (state: string, extra: Record<string, unknown> = {}) => json({
      id: "restore-job", action: "restore", kind: "full", state, error: null, reason: null, plan: null,
      inventory: null, report: null, ...extra,
    });
    const plan = {
      kind: "full", protection: "plain", source_identity: { installation_id: "i", key_id: "sha256:owner" },
      account_identities: 2, administrator_included: true, included_secret_types: ["database-local-auth"],
      omitted_environment: [], backup_deployment: { base_url: "https://source.example/editarr", port: 9443 },
    };
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ token: "flow-token" }))
      .mockResolvedValueOnce(restoreJob("waiting"))
      .mockResolvedValueOnce(restoreJob("uploaded"))
      .mockResolvedValueOnce(restoreJob("ready", { plan }))
      .mockResolvedValueOnce(restoreJob("activating"))
      .mockResolvedValueOnce(json({ code: "setup_already_complete", detail: "Administrator setup is complete" }, 409))
      .mockResolvedValueOnce(json({ ...authenticatedStatus, state: "unauthenticated", csrf_token: null }));
    vi.stubGlobal("fetch", request);
    renderSetup();
    fireEvent.click(await screen.findByRole("button", { name: "Restore from backup" }));
    const file = new File(["manifest.json\n"], "full.backup");
    Object.defineProperty(file, "slice", { value: () => ({ text: async () => "manifest.json\n" }) });
    await waitFor(() => expect(screen.getByLabelText("Backup file")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Backup file"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Inspect backup" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Inspect backup" }));
    expect(await screen.findByRole("heading", { name: "Restore preview" })).toBeVisible();
    expect(screen.getByText(/Other accounts' community identities: 2/)).toBeVisible();
    expect(screen.queryByLabelText(/Replace/)).toBeNull();
    const restore = screen.getByRole("button", { name: "Restore backup" });
    expect(restore).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Restore this backup into this new installation/));
    fireEvent.click(restore);
    expect(await screen.findByText("Restore complete", {}, { timeout: 3000 })).toBeVisible();
    expect(screen.getByText(/Review media paths and source connections/)).toBeVisible();
    expect(screen.getByText(/keeps its own base URL and listening port\. The backup used base URL https:\/\/source\.example\/editarr and port 9443/)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Connect a Jellyfin server instead" })).toBeNull());
    expect(request.mock.calls[5][1]).toMatchObject({ method: "POST", body: JSON.stringify({ confirmed: true }) });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });

  async function fill(request: ReturnType<typeof vi.fn>, { server = "http://jellyfin.local:8096", username = "Avery", password = "jellyfin password", rememberMe = true } = {}) {
    vi.stubGlobal("fetch", request);
    renderSetup();
    fireEvent.change(await screen.findByLabelText("Jellyfin server URL"), { target: { value: server } });
    fireEvent.change(screen.getByLabelText("Jellyfin username"), { target: { value: username } });
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: password } });
    if (!rememberMe) fireEvent.click(screen.getByLabelText("Remember me"));
    fireEvent.submit(screen.getByRole("button", { name: "Connect and sign in" }).closest("form")!);
  }

  it("asks for a Jellyfin server and administrator sign-in, with the URL field focused", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(setupRequiredStatus())));
    renderSetup();

    expect(await screen.findByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
    expect(screen.getByLabelText("Jellyfin server URL")).toHaveFocus();
    expect(screen.getByLabelText("Jellyfin username")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Jellyfin password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Remember me")).toBeChecked();
    expect(screen.getByRole("button", { name: "Restore from backup" })).toBeVisible();
    expect(screen.queryByLabelText(/API key/i)).toBeNull();
  });

  it("checks the address and username locally without contacting Editarr", async () => {
    const request = vi.fn().mockResolvedValue(json(setupRequiredStatus()));
    await fill(request, { server: "jellyfin.local", username: " " });

    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("Enter an http or https address");
    expect(screen.getByText("Enter your Jellyfin username.")).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])("sends the server and credentials with remember_me=%s, clears the password, and enters the application", async (rememberMe) => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json(authenticatedStatus, 201));
    await fill(request, { server: " http://jellyfin.local:8096 ", rememberMe });

    expect(await screen.findByRole("status")).toHaveTextContent("Jellyfin connected");
    expect(screen.queryByDisplayValue("jellyfin password")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(request.mock.calls[1][1]).toMatchObject({ credentials: "same-origin", method: "POST" });
    expect(JSON.parse(String((request.mock.calls[1][1] as RequestInit).body))).toEqual({
      server_url: "http://jellyfin.local:8096", username: "Avery", password: "jellyfin password", remember_me: rememberMe,
    });
  });

  it("disables the form while setup is pending", async () => {
    let resolveSetup: ((response: Response) => void) | undefined;
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveSetup = resolve; }));
    await fill(request);

    expect(await screen.findByRole("button", { name: "Connecting…" })).toBeDisabled();
    expect(screen.getByLabelText("Remember me")).toBeDisabled();
    expect(screen.getByLabelText("CONNECT YOUR JELLYFIN SERVER")).toHaveAttribute("aria-busy", "true");
    resolveSetup?.(json(authenticatedStatus, 201));
  });

  it.each([
    ["jellyfin_not_administrator", 403, "not an administrator"],
    ["invalid_credentials", 401, "did not accept that username and password"],
    ["jellyfin_unreachable", 502, "could not reach a Jellyfin server"],
    ["jellyfin_not_jellyfin", 502, "did not answer as a Jellyfin server"],
    ["jellyfin_key_refused", 502, "did not create an API key"],
    ["jellyfin_key_left_behind", 502, "Dashboard > API Keys"],
    ["login_throttled", 429, "Too many setup attempts"],
  ])("names a %s refusal in its own words and keeps the form", async (code, status, message) => {
    const canary = "do-not-display-this-detail";
    await fill(vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ code, detail: canary }, status)));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(canary)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("jellyfin password")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Jellyfin server URL")).toHaveValue("http://jellyfin.local:8096");
    expect(screen.getByRole("button", { name: "Connect and sign in" })).toBeEnabled();
  });

  it("marks the address field when the server refuses the URL", async () => {
    await fill(vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ code: "jellyfin_bad_url", detail: "bad" }, 400)));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter an http or https address");
    expect(screen.getByLabelText("Jellyfin server URL")).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(screen.getByLabelText("Jellyfin server URL")).toHaveFocus());
  });

  it("moves focus to a server refusal so a narrow screen shows and announces it", async () => {
    await fill(vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ code: "jellyfin_unreachable", detail: "x" }, 502)));

    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("shows how long a throttled setup must wait and blocks submitting until then", async () => {
    await fill(vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "login_throttled", detail: "x" }), {
        status: 429, headers: { "Content-Type": "application/json", "Retry-After": "900" },
      })));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Try again in 15 minutes.");
    expect(alert).not.toHaveTextContent("Wait a moment");
    expect(screen.getByRole("button", { name: "Connect and sign in" })).toBeDisabled();
  });

  it("uses a generic safe error for an unexpected failure", async () => {
    await fill(vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ detail: "do-not-display" }, 500)));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to set up Editarr");
    expect(screen.queryByText("do-not-display")).not.toBeInTheDocument();
  });

  it("refreshes status and moves safely to sign-in if another setup wins", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ code: "setup_already_complete", detail: "Administrator setup is already complete" }, 409))
      .mockResolvedValueOnce(json({ ...authenticatedStatus, state: "unauthenticated", csrf_token: null }));
    await fill(request);

    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("tells the browser that lost a setup race why it is on the sign-in screen", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(json(setupRequiredStatus()))
      .mockResolvedValueOnce(json({ code: "setup_already_complete", detail: "x" }, 409))
      .mockResolvedValueOnce(json({ ...authenticatedStatus, state: "unauthenticated", csrf_token: null, sign_in: { jellyfin: true, registration: false } })));
    render(<AuthenticationGate setupRequired={<JellyfinSetup />} unauthenticated={<AdministratorLogin />}><h1>Protected application</h1></AuthenticationGate>);
    fireEvent.change(await screen.findByLabelText("Jellyfin server URL"), { target: { value: "http://jellyfin.local:8096" } });
    fireEvent.change(screen.getByLabelText("Jellyfin username"), { target: { value: "Avery" } });
    fireEvent.submit(screen.getByRole("button", { name: "Connect and sign in" }).closest("form")!);

    expect(await screen.findByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Editarr was already set up from another browser. Sign in to continue.");
  });

  it("accepts only http and https addresses with a host", () => {
    expect(validateJellyfinSetup("https://media.example/jellyfin", "Avery")).toEqual({});
    expect(validateJellyfinSetup("ftp://media.example", "Avery").serverUrl).toBeDefined();
    expect(validateJellyfinSetup("http://", "Avery").serverUrl).toBeDefined();
  });
});
