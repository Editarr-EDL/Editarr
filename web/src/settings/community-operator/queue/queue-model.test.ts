import { expect, it } from "vitest";
import { queueItems } from "../test-fixtures-insights";
import { queueActions, queueImpact, queueLink, queueTitle } from "./queue-model";
import type { QueueItem } from "../../../api/api-community-operator-community";

const entry = (kind: string) => queueItems.find(item => item.kind === kind) as unknown as QueueItem;

it("titles each decision from its labelled parts", () => {
  expect(queueTitle(entry("report"))).toBe("Report · Inaccurate — Violence › Gore · skip · 01:02:10–01:02:31.4");
  expect(queueTitle(queueItems[1] as unknown as QueueItem)).toBe("Report · Other — version The Quiet Orchard (2003) · Unrated");
  expect(queueTitle(entry("alias"))).toBe("Alias suggestion · “Blue Comet: Remastered”");
  expect(queueTitle(entry("quarantined"))).toBe("Filter · Sexual content › Nudity · skip · 01:31:02–01:31:20");
  expect(queueImpact(entry("version"))).toBe("2 uploads · 29 filters · trust 0.41");
  expect(queueLink(entry("report"))).toBe("#/items/w_arrival?version=v_arrival_dc&filter=f_gore");
});

it("offers state-appropriate actions with the listed revision and alias defaults", () => {
  expect(queueActions(entry("report")).map(action => [action.command, action.target, action.revision])).toEqual([
    ["resolve_report", { kind: "report", targetId: "r_gore" }, 0], ["quarantine", { kind: "filter", targetId: "f_gore" }, undefined]]);
  expect(queueActions(entry("alias"))[0]).toMatchObject({ command: "approve_alias", extras: { create: true, aliasId: "remastered", label: "Blue Comet: Remastered" } });
  expect(queueActions(entry("version")).map(action => action.command)).toEqual(["confirm_version", "merge_version", "quarantine"]);
  expect(queueActions(entry("quarantined"))).toMatchObject([{ label: "Restore filter", command: "restore", revision: 1 }]);
  expect(queueActions({ ...entry("report"), resource: "unknown:x" })).toEqual([]);
});
