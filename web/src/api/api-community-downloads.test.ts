import { expect, it, vi } from "vitest";

import { communityDownloadStatus, type DownloadChoice } from "./api-community-downloads";
import { apiRequest } from "./api-transport";

vi.mock("./api-transport", () => ({ apiRequest: vi.fn() }));

const choice: DownloadChoice = {
  contributionId: "contribution", revisionId: "revision", artifactDigest: "b".repeat(64), channels: ["video"],
};
const status = (pendingChannels: string[]) => {
  vi.mocked(apiRequest).mockResolvedValueOnce(Response.json({ version: 1, outcomes: [{
    revisionId: choice.revisionId, status: "imported", scanId: "scan", receiptState: "pending",
    reviewed: false, reviewRequired: false, approvedChannels: [], verificationPending: true, pendingChannels,
  }] }));
  return communityDownloadStatus({ source: "jellyfin", item_id: "movie" },
    "a".repeat(64), [choice], new AbortController().signal);
};

it("accepts staged channels matching the request", async () => {
  await expect(status(["video"])).resolves.toMatchObject([{ pendingChannels: ["video"], approvedChannels: [] }]);
});

it.each([[], ["audio"], ["video", "video"], ["subtitles"]])("rejects invalid staged channels %j", async (...channels) => {
  await expect(status(channels)).rejects.toMatchObject({ code: "invalid_response" });
});
