import { afterEach, expect, it, vi } from "vitest";
import { readCommunityFeedback, sendCommunityVote } from "./api-community-votes";
import { setCsrfToken } from "./api-transport";
import contract from "../../../tests/fixtures/community/vote-v1.json";

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });
const feedback = { version: 1, revisionId: "one", scope: { serviceUrl: "https://community.example", contributorId: "reader" },
  current: { vote: "neutral", eligible: true, reason: null }, totals: { likes: 0, dislikes: 0 }, pending: null, error: null };
const signal = () => new AbortController().signal;

it("uses private no-store reads and protected same-origin CSRF exact-intent mutations", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(feedback)))
    .mockImplementation(async () => new Response(JSON.stringify({ version: 1, state: "completed", result: {
      version: 1, revisionId: "one", operationId: "vote-one", vote: "neutral" } })));
  vi.stubGlobal("fetch", fetch); setCsrfToken("csrf-vote");
  expect(await readCommunityFeedback("one", signal())).toEqual(feedback);
  await sendCommunityVote("one", { operationId: "vote-one", vote: "neutral" }, signal());
  await sendCommunityVote("one", { operationId: "vote-one", vote: "neutral" }, signal(), true);
  expect(fetch.mock.calls[0][1]).toMatchObject({ cache: "no-store", credentials: "same-origin" });
  expect(new Headers(fetch.mock.calls[1][1].headers).get("x-csrf-token")).toBe("csrf-vote");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ confirmed: true, operation_id: "vote-one", vote: "neutral" });
  expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ confirmed: true, operation_id: "vote-one" });
});

it.each([{ ...feedback, version: true }, { ...feedback, revisionId: "other" },
  { ...feedback, current: { vote: "neutral", eligible: 1, reason: null } },
  { ...feedback, totals: { likes: -1, dislikes: 0 } },
  { ...feedback, totals: { likes: Number.MAX_SAFE_INTEGER, dislikes: 0 } },
  { ...feedback, current: null }, { ...feedback, privateHistory: [] },
  { ...feedback, pending: { operationId: "vote-one", vote: "up", state: "pending" } }])(
  "rejects malformed private feedback", async (value) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
    await expect(readCommunityFeedback("one", signal())).rejects.toMatchObject({ code: "invalid_response" });
  });

it("validates the shared completed-vote wire fixture without treating it as current state", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(contract.completed))));
  await sendCommunityVote(contract.revisionId, { operationId: contract.operationId, vote: "like" }, signal());
});