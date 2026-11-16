import { describe, expect, it } from "vitest";

import { retryAfterMessage } from "./retry-after";

describe("retryAfterMessage", () => {
  it.each([
    [1, "Try again in 1 second."],
    [59, "Try again in 59 seconds."],
    [60, "Try again in 1 minute."],
    [61, "Try again in 2 minutes."],
    [900, "Try again in 15 minutes."],
  ])("names a %i second wait in the largest whole unit", (seconds, message) => {
    expect(retryAfterMessage(seconds)).toBe(message);
  });
});
