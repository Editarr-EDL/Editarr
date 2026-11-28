import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch, pinFixtureClock } from "../test-render";
import { QueuePage } from "./queue-page";

beforeEach(() => pinFixtureClock());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([["all", "15 d"], ["aliases", "2 d"]])("shows the whole-queue oldest wait for %s, not the oldest on the page", async (kind, oldest) => {
  stubOperatorFetch();
  renderOperator(<QueuePage kind={kind} params={{}} />);
  const health = await screen.findByRole("complementary", { name: "Queue summary" });
  expect(await within(health).findByText(oldest)).toBeInTheDocument();
  expect(within(health).getByText("oldest waiting")).toBeInTheDocument();
});
