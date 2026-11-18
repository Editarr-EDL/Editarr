import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { fetchMcfRegistry } from "../api/api-service";
import { useMcfRegistry } from "./mcf-registry";

vi.mock("../api/api-service", () => ({ fetchMcfRegistry: vi.fn() }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const registry = { families: [{ token: "language", label: "Language", categories: [] }] } as never;

it("shares one registry request between consumers", async () => {
  vi.mocked(fetchMcfRegistry).mockResolvedValue(registry);
  const first = renderHook(() => useMcfRegistry());
  const second = renderHook(() => useMcfRegistry());
  await waitFor(() => expect(second.result.current.registry).toBe(registry));
  expect(first.result.current).toEqual({ registry, error: null });
  expect(fetchMcfRegistry).toHaveBeenCalledTimes(1);
});

it("reports a failed load and retries on the next mount", async () => {
  vi.mocked(fetchMcfRegistry).mockRejectedValueOnce(new Error("Registry offline")).mockResolvedValueOnce(registry);
  const failed = renderHook(() => useMcfRegistry());
  await waitFor(() => expect(failed.result.current).toEqual({ registry: null, error: "Registry offline" }));
  failed.unmount();
  const retried = renderHook(() => useMcfRegistry());
  await waitFor(() => expect(retried.result.current).toEqual({ registry, error: null }));
  expect(fetchMcfRegistry).toHaveBeenCalledTimes(2);
});
