import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../api/api-users";
import { ProfileEditor } from "./profile-editor";

vi.mock("../../api/api-users", async (original) => ({ ...await original<typeof api>(), editAccount: vi.fn(), uploadAccountAvatar: vi.fn(), removeAccountAvatar: vi.fn() }));
const account: api.AccountDetail = {
  id: 4, username: null, display_name: "Pat", source: "local", role: "user", status: "active", disable_cause: null,
  last_login_at: null, created_at: "2026-10-24T12:00:00Z", avatar: null,
  overrides: { permissions: false, media_access: false, filter_policy: false },
  email: null, timezone: null, language: "en", read_only_fields: [], available_languages: ["en"], link_cleanup: null, successor_refusal: null,
};
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

it("saves independent local preferences and uploads the selected image", async () => {
  const onSaved = vi.fn();
  vi.mocked(api.editAccount).mockResolvedValue({ ...account, display_name: "Pat Smith", email: "pat@example.test" });
  vi.mocked(api.uploadAccountAvatar).mockResolvedValue({ ...account, avatar: "digest" });
  render(<ProfileEditor account={account} editable onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Pat Smith" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "pat@example.test" } });
  fireEvent.click(screen.getByText("Save profile"));
  await screen.findByText("Profile saved.");
  expect(api.editAccount).toHaveBeenCalledWith(4, { display_name: "Pat Smith", email: "pat@example.test", timezone: null, language: "en" });
  const image = new File(["png"], "portrait.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Avatar image"), { target: { files: [image] } });
  fireEvent.click(screen.getByText("Upload avatar"));
  await screen.findByText("Avatar saved.");
  expect(api.uploadAccountAvatar).toHaveBeenCalledWith(4, image);
  expect(onSaved).toHaveBeenLastCalledWith(expect.objectContaining({ avatar: "digest" }));
});

it("shows the Jellyfin username and avatar read-only while saving email, timezone, and supported language", async () => {
  const linked: api.AccountDetail = { ...account, username: null, source: "jellyfin", avatar: "digest", read_only_fields: ["username", "display_name", "avatar"] };
  vi.mocked(api.editAccount).mockResolvedValue(linked);
  render(<ProfileEditor account={linked} editable onSaved={vi.fn()} />);
  expect(screen.queryByLabelText("Display name")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Avatar image")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /avatar/i })).not.toBeInTheDocument();
  expect(screen.getByText("Jellyfin username").nextElementSibling).toHaveTextContent(/^None \(Jellyfin name: Pat\)$/);
  expect(screen.getByText("Avatar from Jellyfin")).toBeVisible();
  expect(screen.getByRole("combobox")).toHaveTextContent("English");
  expect(screen.getAllByRole("option")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "Europe/London" } });
  fireEvent.click(screen.getByText("Save profile"));
  await waitFor(() => expect(api.editAccount).toHaveBeenCalledWith(4, { email: null, timezone: "Europe/London", language: "en" }));
});

it("never offers an admin a rename for a Jellyfin account and shows the username Jellyfin gave it", () => {
  const linked: api.AccountDetail = { ...account, username: "Pat Jellyfin", source: "jellyfin", read_only_fields: ["username", "display_name", "avatar"] };
  render(<ProfileEditor account={linked} editable onSaved={vi.fn()} />);
  expect(screen.queryByRole("textbox", { name: "Username" })).not.toBeInTheDocument();
  expect(screen.getByText("Jellyfin username").nextElementSibling).toHaveTextContent("Pat Jellyfin");
});

it("offers no username for a local account and saves its display name", async () => {
  vi.mocked(api.editAccount).mockResolvedValue({ ...account, display_name: "Patricia" });
  render(<ProfileEditor account={account} editable onSaved={vi.fn()} />);
  expect(screen.queryByText(/username/i)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Patricia" } });
  fireEvent.click(screen.getByText("Save profile"));
  await waitFor(() => expect(api.editAccount).toHaveBeenCalledWith(4, { display_name: "Patricia", email: null, timezone: null, language: "en" }));
});

it("shows a rejected save without discarding the draft", async () => {
  vi.mocked(api.editAccount).mockRejectedValue(new Error("Timezone must be an IANA name"));
  render(<ProfileEditor account={account} editable onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "bad timezone" } });
  fireEvent.click(screen.getByText("Save profile"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Timezone must be an IANA name");
  expect(screen.getByLabelText("Timezone")).toHaveValue("bad timezone");
});