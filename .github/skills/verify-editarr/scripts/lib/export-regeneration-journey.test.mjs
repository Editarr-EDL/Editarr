import assert from "node:assert/strict";
import { test } from "node:test";
import { regenerationStatus } from "./export-regeneration-journey.mjs";

test("regeneration polling retries only the expected publication replacement race", async () => {
  const responses = [
    { status: 409, body: { detail: { code: "stale_export_revision" } } },
    { status: 200, body: { regeneration_state: "succeeded", serving: true } },
  ];
  const actor = {
    async send(method, url) {
      assert.equal(method, "GET");
      assert.equal(url, "/api/exports/42");
      return responses.shift();
    },
  };
  assert.equal(await regenerationStatus(actor, 42), null);
  assert.deepEqual(await regenerationStatus(actor, 42), {
    regeneration_state: "succeeded", serving: true,
  });
});

for (const status of [401, 403, 404, 409, 500]) {
  test(`regeneration polling does not hide an unrelated HTTP ${status}`, async () => {
    const actor = { send: async () => ({ status, body: { detail: { code: "unrelated" } } }) };
    await assert.rejects(regenerationStatus(actor, 42), { code: "ERR_ASSERTION" });
  });
}