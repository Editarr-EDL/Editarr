import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../api/api-users";
import { usePrincipal } from "../../auth/auth-gate";
import type { Capability } from "../../auth/capabilities";
import { ordinaryAccount, ownerPrincipal } from "../users/users.test-support";
import { ProfileView } from "./profile-view";

vi.mock("../../api/api-users", async (original) => ({ ...await original<typeof api>(), readAccount: vi.fn() }));
vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(),
  usePrincipal: vi.fn(), useCan: (capability: Capability) => vi.mocked(usePrincipal)()?.capabilities.has(capability) ?? false }));
vi.mock("../../api/api-community", async original => ({ ...await original<typeof import("../../api/api-community")>(),
  fetchLocalCommunityConnection: vi.fn().mockResolvedValue({ configured: false, connected: false, profile: null, pending: null }),
  fetchCommunityRecovery: vi.fn().mockResolvedValue({ configured: false, identity: null, pending: null, completed: null }) }));
vi.mock("../api-key-settings", () => ({ ApiKeySettings: () => null }));
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, userId: ordinaryAccount.id as typeof ownerPrincipal.userId, role: "user" });
});

it.each(["local", "jellyfin"] as const)("shows read-only creation and last-login facts for a %s profile", async (source) => {
  vi.mocked(api.readAccount).mockResolvedValue({ ...ordinaryAccount, source, last_login_at: "2026-10-24T13:00:00Z" });
  render(<ProfileView />);
  expect(await screen.findByText("Created")).toBeVisible();
  expect(screen.getByText(new Date("2026-10-24T12:00:00Z").toLocaleString())).toBeVisible();
  expect(screen.getByText("Last login")).toBeVisible();
  expect(screen.getByText(new Date("2026-10-24T13:00:00Z").toLocaleString())).toBeVisible();
  expect(screen.queryByLabelText("Created")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Last login")).not.toBeInTheDocument();
  expect(api.readAccount).toHaveBeenCalledWith(ordinaryAccount.id, expect.any(AbortSignal));
});

it("shows Never for a profile with no previous login", async () => {
  vi.mocked(api.readAccount).mockResolvedValue(ordinaryAccount);
  render(<ProfileView />);
  expect(await screen.findByText("Last login")).toBeVisible();
  expect(screen.getByText("Never")).toBeVisible();
});

it("exposes personal community settings without provider connection or directory permissions", async () => {
  vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, role: "user", capabilities: new Set(["profile.view.own", "community.identity.view.own"]) });
  vi.mocked(api.readAccount).mockResolvedValue(ordinaryAccount);
  render(<ProfileView />);
  expect(await screen.findByRole("heading", { name: "Community scans" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
});