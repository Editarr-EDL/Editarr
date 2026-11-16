import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "../App";
import { setCsrfToken } from "../api/api-transport";
import { ordinaryAccount } from "../settings/users/users.test-support";
import { installProductionApi, json, signedInStatus } from "../test/app-test-fixtures";
import { AuthenticationGate } from "./auth-gate";
import { openAccountMenu } from "./account-menu.test-support";

const account = { ...ordinaryAccount, id: 1, username: "jellyfin-owner", display_name: "Jellyfin Owner", avatar: "digest-1" };

afterEach(() => {
  cleanup(); setCsrfToken(null); vi.restoreAllMocks(); vi.unstubAllGlobals();
  window.localStorage.clear(); window.location.hash = "";
});

function renderShell({ accountResponse = () => json(account) }: { accountResponse?: () => Response } = {}) {
  const fallback = installProductionApi();
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const pathname = new URL(String(input), "http://editarr.test").pathname;
    paths.push(pathname);
    if (pathname === "/api/auth/status") return json(signedInStatus({ username: "Jellyfin Owner" }));
    if (pathname === "/api/users/1") return accountResponse();
    if (pathname === "/api/auth/logout") return json({ state: "unauthenticated", sign_in: { jellyfin: true } });
    return fallback(input, init);
  }));
  window.location.hash = "#/library/movies";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  return paths;
}

it("puts Jellyfin Libraries at the top level and drops the session footer and My Profile link", async () => {
  renderShell();
  const navigation = await screen.findByRole("navigation", { name: "Primary navigation" });
  await within(navigation).findByRole("button", { name: "Movies" });
  expect(within(navigation).getByRole("button", { name: /Jellyfin Libraries/ })).toBeVisible();
  expect(within(navigation).queryByRole("button", { name: /Sources/ })).not.toBeInTheDocument();
  expect(within(navigation).queryByRole("button", { name: "My Profile" })).not.toBeInTheDocument();
  expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Sign out$/i })).not.toBeInTheDocument();
});

it("shows the account's avatar beside the brand and names the account in the menu", async () => {
  renderShell();
  const trigger = await screen.findByRole("button", { name: "Account menu for jellyfin-owner" });
  expect(trigger).toHaveAttribute("aria-haspopup", "menu");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(trigger.querySelector("img")?.getAttribute("src")).toContain("/users/1/avatar?v=digest-1");
  expect(trigger.closest(".brand")).toHaveTextContent("Editarr");

  const menu = await openAccountMenu("jellyfin-owner");
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(within(menu).getByText("jellyfin-owner")).toBeVisible();
  expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["My Profile", "Sign Out"]);
  expect(within(menu).getByRole("menuitem", { name: "My Profile" })).toHaveFocus();
});

it("opens My Profile from the menu and closes it", async () => {
  renderShell();
  const menu = await openAccountMenu("jellyfin-owner");
  fireEvent.click(within(menu).getByRole("menuitem", { name: "My Profile" }));
  expect(await screen.findByRole("heading", { name: "My profile" })).toBeVisible();
  expect(window.location.hash).toMatch(/^#\/?settings\/profile$/);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("signs out from the menu", async () => {
  const paths = renderShell();
  const menu = await openAccountMenu("jellyfin-owner");
  fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign Out" }));
  await waitFor(() => expect(paths).toContain("/api/auth/logout"));
  await waitFor(() => expect(screen.queryByRole("button", { name: /^Account menu for / })).not.toBeInTheDocument());
});

it("supports the keyboard and closes on Escape or an outside click", async () => {
  renderShell();
  const trigger = await screen.findByRole("button", { name: "Account menu for jellyfin-owner" });
  let menu = await openAccountMenu("jellyfin-owner");
  const [profile, signOut] = within(menu).getAllByRole("menuitem");
  fireEvent.keyDown(menu, { key: "ArrowDown" });
  expect(signOut).toHaveFocus();
  fireEvent.keyDown(menu, { key: "ArrowDown" });
  expect(profile).toHaveFocus();
  fireEvent.keyDown(menu, { key: "End" });
  expect(signOut).toHaveFocus();
  fireEvent.keyDown(menu, { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();

  menu = await openAccountMenu("jellyfin-owner");
  fireEvent.pointerDown(document.body);
  await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(await screen.findByRole("menu")).toBeVisible();
});

it("falls back to the session name and an initial when the account cannot be read", async () => {
  renderShell({ accountResponse: () => json({ code: "unavailable" }, 503) });
  const trigger = await screen.findByRole("button", { name: "Account menu for Jellyfin Owner" });
  expect(trigger).toHaveTextContent("J");
  expect(trigger.querySelector("img")).toBeNull();
});
