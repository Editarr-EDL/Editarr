import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchVersionInformation } from "../api/api-service";
import { About } from "./about";

vi.mock("../api/api-service", () => ({ fetchVersionInformation: vi.fn() }));
afterEach(cleanup);
beforeEach(() => vi.resetAllMocks());

it("shows independent versions and unknown build state", async () => {
  vi.mocked(fetchVersionInformation).mockResolvedValue({
    release: "0.1.0", build_revision: null, build_dirty: null,
    scanner_behavior: "scanner-1", database_schema: "0001_current_baseline",
    local_api: "1", community_api: null, artifact_schema: null,
  });
  render(<About />);
  expect(screen.getByText("Loading version information…")).toBeTruthy();
  expect(await screen.findByText("Unknown")).toBeTruthy();
  expect(screen.getByText("scanner-1")).toBeTruthy();
  expect(screen.getByText("0001_current_baseline")).toBeTruthy();
});

it("shows a dirty build and a failed request without inventing a release", async () => {
  vi.mocked(fetchVersionInformation).mockResolvedValueOnce({
    release: "0.1.0", build_revision: "a".repeat(40), build_dirty: true,
    scanner_behavior: "scanner-1", database_schema: "0001_current_baseline",
    local_api: "1", community_api: null, artifact_schema: null,
  });
  const view = render(<About />);
  expect(await screen.findByText("aaaaaaaaaaaa (dirty)")).toBeTruthy();
  view.unmount();
  vi.mocked(fetchVersionInformation).mockRejectedValueOnce(new Error("offline"));
  render(<About />);
  expect(await screen.findByText("Version information is unavailable.")).toBeTruthy();
});

it("flags a frontend and backend release mismatch", async () => {
  vi.mocked(fetchVersionInformation).mockResolvedValue({
    release: "0.2.0", build_revision: null, build_dirty: null,
    scanner_behavior: "scanner-1", database_schema: "0001_current_baseline",
    local_api: "1", community_api: null, artifact_schema: null,
  });
  render(<About />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Frontend 0.1.0 does not match server 0.2.0",
  );
});