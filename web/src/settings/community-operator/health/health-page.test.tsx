import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch, pinFixtureClock } from "../test-render";
import { HealthPage } from "./health-page";

beforeEach(() => pinFixtureClock());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("shows readiness checks, accessible trend charts for the routed period and retention sweeps", async () => {
  const fetcher = stubOperatorFetch();
  renderOperator(<HealthPage params={{ period: "90d" }} />);
  expect(await screen.findByRole("heading", { name: "Ready · all checks passing" })).toBeInTheDocument();
  expect(screen.getByText(/Database schema/)).toHaveTextContent("passing");
  const chart = await screen.findByRole("img", { name: /^Database size over 90 d\. From .+: lowest .+, highest .+, latest .+\.$/ });
  expect(chart.tagName.toLowerCase()).toBe("svg");
  expect(chart.querySelector("[style]")).toBeNull();
  expect(await screen.findByRole("img", { name: /Stored upload payloads over 90 d against a 350 MB quota/ })).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path]) => path === "/admin/v1/trends?metric=downloads&period=90d")).toBe(true);
  expect(await screen.findByText("Batch limit")).toBeInTheDocument();
  expect(screen.getByText(/Last full recompute/)).toHaveTextContent("13 versions");
  expect(screen.getByRole("region", { name: "Recompute all versions" })).toBeInTheDocument();
});
