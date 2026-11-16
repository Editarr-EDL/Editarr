import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, setCsrfToken } from "../api";
import type { AuthenticationStatus } from "../api/api-system-types";
import { signedInStatus } from "../test/app-test-fixtures";
import { AuthenticationGate } from "./auth-gate";
import { AdministratorLogin } from "./auth-login";

const authenticatedStatus = { ...signedInStatus(), csrf_token: "csrf-token" };

function authenticationStatus(state: "unauthenticated" | "authenticated", signIn = { jellyfin: true }): AuthenticationStatus {
  if (state === "authenticated") return authenticatedStatus;
  return { state, setup_required: false, sign_in: signIn, restart_required: false };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderLogin() {
  return render(
    <AuthenticationGate unauthenticated={<AdministratorLogin />}>
      <h1>Protected application</h1>
    </AuthenticationGate>,
  );
}

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("AdministratorLogin", () => {
  it("uses labelled, password-manager-compatible controls and defaults to remembering the session", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(authenticationStatus("unauthenticated"))));
    renderLogin();

    const username = await screen.findByLabelText("Jellyfin username");
    const password = screen.getByLabelText("Jellyfin password");
    expect(username).toHaveFocus();
    expect(username).toHaveAttribute("type", "text");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(username).toHaveAttribute("autocapitalize", "none");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Remember me")).toBeChecked();
  });

  it("submits the selected remember-me value without a reload, clears the password, and restores the safe attempted route", async () => {
    window.location.hash = "#/settings/general";
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("unauthenticated")))
      .mockResolvedValueOnce(json({ enabled: false }))
      .mockResolvedValueOnce(json(authenticatedStatus));
    vi.stubGlobal("fetch", request);
    renderLogin();

    fireEvent.change(await screen.findByLabelText("Jellyfin username"), { target: { value: "Admin" } });
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByLabelText("Remember me"));
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);

    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/settings/general");
    expect(screen.queryByDisplayValue("correct horse battery")).not.toBeInTheDocument();
    expect(String(request.mock.calls[2][0])).toBe("/api/auth/jellyfin/login");
    expect(request.mock.calls[2][1]).toMatchObject({ credentials: "same-origin", method: "POST" });
    expect(new Headers((request.mock.calls[2][1] as RequestInit).headers).has("X-CSRF-Token")).toBe(false);
    expect(JSON.parse((request.mock.calls[2][1] as RequestInit).body as string)).toEqual({
      username: "Admin",
      password: "correct horse battery",
      remember_me: false,
    });
  });

  it.each([
    ["unknown username", "unknown-user"],
    ["wrong password", "wrong-password"],
  ])("does not distinguish %s from a bad password", async (_caseName, detail) => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("unauthenticated")))
      .mockResolvedValueOnce(json({ enabled: false }))
      .mockResolvedValueOnce(json({ code: "invalid_credentials", detail }, 401));
    vi.stubGlobal("fetch", request);
    renderLogin();

    fireEvent.change(await screen.findByLabelText("Jellyfin username"), { target: { value: "Admin" } });
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "do-not-retain" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to sign in with those credentials. Please try again.");
    expect(screen.queryByText(detail)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("do-not-retain")).not.toBeInTheDocument();
  });

  it("uses generic throttling feedback without exposing the server response", async () => {
    const detail = "internal limiter details must never be rendered";
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("unauthenticated")))
      .mockResolvedValueOnce(json({ enabled: false }))
      .mockResolvedValueOnce(json({ code: "login_throttled", detail }, 429));
    vi.stubGlobal("fetch", request);
    renderLogin();

    fireEvent.change(await screen.findByLabelText("Jellyfin password"), { target: { value: "do-not-retain" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many sign-in attempts. Please wait and try again.");
    expect(screen.queryByText(detail)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("do-not-retain")).not.toBeInTheDocument();
  });

  it("disables every control and prevents duplicate submissions while login is pending", async () => {
    let resolveLogin: ((response: Response) => void) | undefined;
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("unauthenticated")))
      .mockResolvedValueOnce(json({ enabled: false }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveLogin = resolve; }));
    vi.stubGlobal("fetch", request);
    renderLogin();

    fireEvent.change(await screen.findByLabelText("Jellyfin username"), { target: { value: "Admin" } });
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "correct horse battery" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);

    expect(await screen.findByRole("button", { name: "Signing in…" })).toBeDisabled();
    expect(screen.getByLabelText("Jellyfin username")).toBeDisabled();
    expect(screen.getByLabelText("Jellyfin password")).toBeDisabled();
    expect(screen.getByLabelText("Remember me")).toBeDisabled();
    expect(screen.getByLabelText("SIGN IN TO CONTINUE")).toHaveAttribute("aria-busy", "true");
    fireEvent.submit(screen.getByRole("button", { name: "Signing in…" }).closest("form")!);
    expect(request).toHaveBeenCalledTimes(3);
    await act(async () => { resolveLogin?.(json(authenticatedStatus)); });
  });

  it("returns to the login page when an authenticated session expires", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("authenticated")))
      .mockResolvedValueOnce(json({ code: "authentication_required", detail: "Sign in again" }, 401));
    vi.stubGlobal("fetch", request);
    renderLogin();

    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    await act(async () => { await apiRequest("/api/scans"); });
    expect(await screen.findByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Jellyfin username")).toHaveFocus());
  });

  it("keeps the sign-in page open after a Jellyfin outage and signs in on retry", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(authenticationStatus("unauthenticated")))
      .mockResolvedValueOnce(json({ enabled: false }))
      .mockResolvedValueOnce(json({ code: "jellyfin_unavailable", detail: "Jellyfin could not verify the sign-in" }, 503))
      .mockResolvedValueOnce(json(authenticatedStatus));
    vi.stubGlobal("fetch", request);
    renderLogin();

    fireEvent.change(await screen.findByLabelText("Jellyfin username"), { target: { value: "casey" } });
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "jellyfin password" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Jellyfin could not confirm the sign-in. Try again shortly.");
    expect(screen.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use Quick Connect" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "jellyfin password" } });
    fireEvent.submit(screen.getByRole("button", { name: "Sign in" }).closest("form")!);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(request.mock.calls.slice(1).map(([input]) => String(input))).toEqual(["/api/auth/jellyfin/quick-connect", "/api/auth/jellyfin/login", "/api/auth/jellyfin/login"]);
  });

  it("offers only Jellyfin sign-in, with no account type choice or registration", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(authenticationStatus("unauthenticated"))));
    renderLogin();

    expect(await screen.findByLabelText("Jellyfin username")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("explains that nobody can sign in while Jellyfin is not connected", async () => {
    const request = vi.fn().mockResolvedValue(json(authenticationStatus("unauthenticated", { jellyfin: false })));
    vi.stubGlobal("fetch", request);
    renderLogin();

    expect(await screen.findByText("Sign-in is unavailable because Editarr is not connected to a Jellyfin server.")).toBeVisible();
    expect(screen.queryByLabelText("Jellyfin password")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(1);
  });
});
