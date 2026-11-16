import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityReceipts } from "./community-receipts";
import * as api from "../../api/api-community-receipts";
import { CommunityApiError } from "../../api/api-community";

vi.mock("../../api/api-community-receipts", () => ({ recoverCommunityReceipts: vi.fn(), retryCommunityReceipt: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("recovers only the selected contributor's receipt IDs and retries explicitly", async () => {
  vi.mocked(api.recoverCommunityReceipts).mockResolvedValue(["import-owned"]);
  vi.mocked(api.retryCommunityReceipt).mockResolvedValue();
  render(<CommunityReceipts userId={17} />);
  expect(api.recoverCommunityReceipts).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Recover import receipts" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry receipt import-owned" }));
  await screen.findByText("Acknowledged");
  expect(api.recoverCommunityReceipts).toHaveBeenCalledWith(expect.objectContaining({ userId: 17, signal: expect.any(AbortSignal) }));
  expect(api.retryCommunityReceipt).toHaveBeenCalledWith("import-owned", expect.objectContaining({ userId: 17 }));
});

it("clears recovered IDs when current target authority is denied", async () => {
  vi.mocked(api.recoverCommunityReceipts).mockResolvedValue(["private-import"]);
  vi.mocked(api.retryCommunityReceipt).mockRejectedValue(new CommunityApiError("not_found", false, 404));
  render(<CommunityReceipts />);
  fireEvent.click(screen.getByRole("button", { name: "Recover import receipts" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry receipt private-import" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("private-import")).not.toBeInTheDocument();
});