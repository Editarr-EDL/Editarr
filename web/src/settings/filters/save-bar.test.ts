import { describe, expect, it } from "vitest";

import { changeCount } from "./save-bar";

const item = (id: string, value = 0) => ({ id, value });
const count = (saved: { id: string; value: number }[], draft: { id: string; value: number }[]) => changeCount(saved, draft, entry => entry.id);

describe("changeCount", () => {
  it("counts each added, removed, or edited entry once", () => {
    expect(count([item("a"), item("b")], [item("a"), item("b")])).toBe(0);
    expect(count([item("a"), item("b")], [item("a", 1), item("c")])).toBe(3);
  });

  it("counts a new order once, and removing an entry does not count as reordering the rest", () => {
    expect(count([item("a"), item("b"), item("c")], [item("c"), item("a"), item("b")])).toBe(1);
    expect(count([item("a"), item("b"), item("c")], [item("b"), item("c")])).toBe(1);
  });
});
