import { afterEach, expect, it, vi } from "vitest";
import { readOperatorFilterContext, readOperatorItem, readOperatorItems, readOperatorSearch,
  readOperatorVersionDetail } from "../../api/api-community-operator-catalog";
import { readOperatorActivity, readOperatorAudit, readOperatorHome, readOperatorLeaderboard, readOperatorProfile,
  readOperatorQueue } from "../../api/api-community-operator-community";
import { readOperatorBackups, readOperatorHealth, readOperatorMaintenanceRuns, readOperatorTrend, trendMetrics, writeOperatorBackupConfig,
  writeOperatorBackupRun, writeOperatorBackupStage } from "../../api/api-community-operator-system";
import { operatorFixture } from "./test-fixtures";

afterEach(() => vi.unstubAllGlobals());
function serve(override?: (path: string) => unknown) {
  const fetcher = vi.fn((target: string, init?: RequestInit) => Promise.resolve(Response.json(override?.(target) ??
    (init?.method === "POST" && target.endsWith("/run") ? { version: 1, observedAt: 1, runId: "bk-28", status: "running" } :
      target.endsWith("/stage") ? stage : operatorFixture(target)))));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
const stage = { version: 1, observedAt: 1, runId: "bk-27", stagedPath: "/s/bk-27.sqlite", sha256: "abc", bytes: 1, schemaVersion: 5,
  restoreVolume: "restore-bk-27", commands: ["docker compose stop community"] };
const signal = new AbortController().signal;

it("accepts every insight, system and backup fixture in the exact service shapes", async () => {
  serve();
  await readOperatorHome("7d", signal); await readOperatorItems("updated", "", null, signal); await readOperatorItem("w_arrival", signal);
  await readOperatorVersionDetail("w_arrival", "v_arrival_dc", signal); await readOperatorFilterContext("f_gore", signal);
  await readOperatorActivity("all", null, signal); await readOperatorLeaderboard("30d", "accepted", signal);
  await readOperatorProfile("c-mira", signal); await readOperatorQueue("all", null, signal); await readOperatorQueue("quarantined", null, signal);
  await readOperatorSearch("arrival", signal); await readOperatorAudit(null, signal); await readOperatorHealth(signal);
  for (const metric of trendMetrics) await readOperatorTrend(metric, "30d", signal);
  await expect(readOperatorMaintenanceRuns("retention", null, signal)).resolves.toMatchObject({ items: [{ outcome: "ok" }, {}, {}] });
  await expect(readOperatorBackups(signal)).resolves.toMatchObject({ running: false });
});

it("rejects unexpected fields, invalid queries and non-identifier paths before sending", async () => {
  const home = operatorFixture("/admin/v1/home") as Record<string, unknown>;
  serve(() => ({ ...home, secret: "x" }));
  await expect(readOperatorHome("7d", signal)).rejects.toMatchObject({ status: 503 });
  const fetcher = serve();
  expect(() => readOperatorItem("../etc", signal)).toThrow();
  expect(() => readOperatorHome("1y" as never, signal)).toThrow();
  expect(() => readOperatorSearch(" ", signal)).toThrow();
  expect(() => readOperatorSearch("a".repeat(101), signal)).toThrow();
  expect(fetcher).not.toHaveBeenCalled();
  await readOperatorQueue("reports", "cursor-1", signal);
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/queue?kind=reports&after=cursor-1");
});

it("sends backup mutations with CSRF headers, exact bodies and no body for runs and staging", async () => {
  const fetcher = serve(target => target.endsWith("/config") ? operatorFixture("/admin/v1/backups") : undefined);
  const config = { enabled: true, frequency: "daily" as const, atUtc: "04:00", keepLast: 7, keepWeekly: 4, destination: "nightly", integrityCheck: true };
  await writeOperatorBackupConfig(config, signal);
  expect(fetcher.mock.calls[0][1]).toMatchObject({ method: "PUT", headers: { "X-Community-Operator": "1", "Content-Type": "application/json" } });
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(config);
  await writeOperatorBackupRun(signal);
  expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "POST", body: undefined, headers: { "X-Community-Operator": "1" } });
  expect(fetcher.mock.calls[1][1]?.headers).not.toHaveProperty("Content-Type");
  await expect(writeOperatorBackupStage("bk-27", signal)).resolves.toMatchObject({ restoreVolume: "restore-bk-27" });
  expect(() => writeOperatorBackupConfig({ ...config, keepLast: 0 }, signal)).toThrow();
  expect(() => writeOperatorBackupStage("../x", signal)).toThrow();
});
