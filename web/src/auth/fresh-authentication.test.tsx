import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { responseError } from "../api/api-transport";
import { FreshAuthenticationDialog } from "./fresh-authentication";

afterEach(() => { cleanup(); vi.useRealTimers(); });

/** The alert's text as a screen reader reads it: everything not hidden with aria-hidden. */
function spoken(element: HTMLElement): string {
  const copy = element.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("[aria-hidden='true']").forEach(node => node.remove());
  return copy.textContent ?? "";
}

describe("fresh authentication retry window", () => {
  it.each(["12", null, "-1", "not-a-duration", "Infinity"])("parses safe Retry-After seconds from %s", async (value) => {
    const headers = new Headers({ "Content-Type": "application/json" });
    if (value !== null) headers.set("Retry-After", value);
    const error = await responseError(new Response(JSON.stringify({ code: "reauthentication_throttled", detail: "Try later" }), { status: 429, headers }), "Failed");
    expect(error).toMatchObject({ code: "reauthentication_throttled", retryAfterSeconds: value === "12" ? 12 : null });
  });

  it("clears the password and prevents retries until the server's wait expires", async () => {
    vi.useFakeTimers();
    const denial = await responseError(new Response(JSON.stringify({ code: "reauthentication_throttled", detail: "Try later" }), {
      status: 429, headers: { "Retry-After": "2" },
    }), "Failed");
    const confirm = vi.fn().mockRejectedValue(denial);
    render(<FreshAuthenticationDialog title="Confirm identity" body="Generate a key" actionLabel="Generate key" onConfirm={confirm} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "private password" } });
    const form = screen.getByRole("button", { name: "Generate key" }).closest("form")!;
    await act(async () => { fireEvent.submit(form); });
    expect(screen.getByLabelText("Jellyfin password")).toHaveValue("");
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 2 seconds.");
    expect(screen.getByRole("button", { name: "Generate key" })).toBeDisabled();
    await act(async () => { fireEvent.submit(form); });
    expect(confirm).toHaveBeenCalledTimes(1);
    const announced = spoken(screen.getByRole("alert"));
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 1 second.");
    expect(announced).toContain("Try again in 2 seconds.");
    expect(spoken(screen.getByRole("alert"))).toBe(announced);
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(screen.getByRole("button", { name: "Generate key" })).toBeEnabled();
    expect(screen.getByRole("alert")).not.toHaveTextContent("Try again in");
  });

  it("says Jellyfin is unreachable instead of calling the password wrong, and lets the person retry", async () => {
    const outage = await responseError(new Response(JSON.stringify({ code: "jellyfin_unavailable", detail: "Jellyfin did not answer" }), { status: 503 }), "Failed");
    const confirm = vi.fn().mockRejectedValue(outage);
    render(<FreshAuthenticationDialog title="Confirm identity" body="Show key" actionLabel="Show key" onConfirm={confirm} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "private password" } });
    await act(async () => { fireEvent.submit(screen.getByRole("button", { name: "Show key" }).closest("form")!); });
    expect(screen.getByRole("alert")).toHaveTextContent("Editarr could not reach Jellyfin to check your password. Nothing changed. Try again when Jellyfin is back.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("incorrect");
    expect(screen.getByRole("button", { name: "Show key" })).toBeEnabled();
  });
});
