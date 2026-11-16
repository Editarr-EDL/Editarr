import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { setCsrfToken } from "../api/api-transport";
import { ordinaryAccount } from "../settings/users/users.test-support";
import { installProductionApi, json, signedInStatus } from "./app-test-fixtures";

afterEach(() => {
  cleanup(); setCsrfToken(null); vi.restoreAllMocks(); vi.unstubAllGlobals();
  window.localStorage.clear(); window.location.hash = "";
});

it.each(["users", "profile"])("guards the %s deep link before mounting the directory", async (section) => {
  const fallback = installProductionApi();
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const pathname = new URL(String(input), "http://editarr.test").pathname;
    paths.push(pathname);
    if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 3, username: "pat", role: "user", capabilities: ["profile.view.own", "profile.edit.own"] }));
    if (pathname === "/api/users/3") return json(ordinaryAccount);
    return fallback(input, init);
  }));
  window.location.hash = `#/settings/${section}`;
  render(<AuthenticationGate><App /></AuthenticationGate>);
  expect(await screen.findByRole("heading", { name: section === "users" ? "Page unavailable" : "My profile" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Users" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "My Profile" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Account menu for / })).toBeVisible();
  expect(paths).not.toContain("/api/users");
  expect(paths).not.toContain("/api/jellyfin/sync");
  expect(paths).not.toContain("/api/auth/api-key");
});