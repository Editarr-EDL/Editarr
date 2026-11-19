import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSerialPoll } from "./use-serial-poll";

afterEach(() => { cleanup(); vi.useRealTimers(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

it("waits one second after each response before polling again", async () => {
  vi.useFakeTimers();
  const pending = deferred<string>();
  const request = vi.fn(() => pending.promise);
  const onResult = vi.fn();
  renderHook(() => useSerialPoll({ enabled: true, request, onResult }));
  await act(async () => { await vi.advanceTimersByTimeAsync(999); });
  expect(request).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(request).toHaveBeenCalledTimes(1);
  request.mockResolvedValue("next");
  await act(async () => { pending.resolve("first"); await vi.advanceTimersByTimeAsync(999); });
  expect(onResult).toHaveBeenCalledWith("first");
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(request).toHaveBeenCalledTimes(2);
});

it("ignores responses and errors that settle after polling stops", async () => {
  vi.useFakeTimers();
  const success = deferred<string>();
  const failure = deferred<string>();
  const request = vi.fn().mockReturnValueOnce(success.promise).mockReturnValueOnce(failure.promise);
  const onResult = vi.fn();
  const onError = vi.fn();
  const { rerender, unmount } = renderHook(({ enabled, key }) => useSerialPoll({ enabled, key, request, onResult, onError }),
    { initialProps: { enabled: true, key: "job-1" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  rerender({ enabled: false, key: "job-1" });
  await act(async () => { success.resolve("stale"); await vi.advanceTimersByTimeAsync(5_000); });
  expect(onResult).not.toHaveBeenCalled();
  expect(request).toHaveBeenCalledTimes(1);

  rerender({ enabled: true, key: "job-2" });
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(request).toHaveBeenCalledTimes(2);
  unmount();
  await act(async () => { failure.reject(new Error("gone")); await vi.advanceTimersByTimeAsync(5_000); });
  expect(onError).not.toHaveBeenCalled();
  expect(request).toHaveBeenCalledTimes(2);
});
