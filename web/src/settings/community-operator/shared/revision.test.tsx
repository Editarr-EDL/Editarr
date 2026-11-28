import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { RevisionChanges } from "./revision";

afterEach(cleanup);
const text = (delta: { filters: number | null; added: number | null; removed: number | null; retimed: number | null; recategorised: number | null }) =>
  render(<RevisionChanges delta={delta} />).container.textContent;

it("claims no change only when every delta is known to be zero", () => {
  expect(text({ filters: 3, added: 0, removed: 0, retimed: 0, recategorised: 0 })).toBe("no served change"); cleanup();
  expect(text({ filters: 3, added: 0, removed: 0, retimed: null, recategorised: null })).toBe("no known changes; other changes unavailable"); cleanup();
  expect(text({ filters: 3, added: 2, removed: 0, retimed: null, recategorised: 0 })).toBe("+2 filters; other changes unavailable"); cleanup();
  expect(text({ filters: 3, added: 1, removed: 1, retimed: 2, recategorised: 0 })).toBe("+1 filter, −1, 2 retimed"); cleanup();
  expect(text({ filters: null, added: null, removed: null, retimed: null, recategorised: null })).toBe("changes unavailable");
});
