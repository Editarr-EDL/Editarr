import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperatorAccess } from "./operator-access";
import { readOperatorSession } from "./runtime";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function sessionResponse(subject = "fixture-operator") {
  return Response.json({ version: 1, subject });
}

describe("isolated operator runtime", () => {
  it("uses only the same-origin Access session endpoint without local bootstrap or tokens", async () => {
    // A fresh Response per call: the verified app also issues data reads, and a body can be read only once.
    const fetcher = vi.fn(() => Promise.resolve(sessionResponse()));
    vi.stubGlobal("fetch", fetcher);
    render(<OperatorAccess />);
    expect(await screen.findByText("Access verified", {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText("fixture-operator")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith("/admin/v1/session", expect.objectContaining({
      credentials: "same-origin", cache: "no-store", redirect: "error", headers: { Accept: "application/json" },
    }));
    fetcher.mockImplementation(() => Promise.resolve(sessionResponse("fresh-operator")));
    fireEvent.click(screen.getByRole("button", { name: "Refresh access" }));
    expect(await screen.findByText("fresh-operator", {}, { timeout: 2000 })).toBeInTheDocument();
  });
  it.each([[401, "Sign-in required"], [403, "Operator access denied"], [503, "Access verification unavailable"]])(
    "shows honest access failure %s", async (status, label) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: Number(status) })));
      render(<OperatorAccess />);
      expect(await screen.findByText(String(label))).toBeInTheDocument();
      expect(screen.queryByText("Access verified")).not.toBeInTheDocument();
    },
  );
  it.each([new Response("<html>Access login</html>", { headers: { "Content-Type": "text/html" } }),
    Response.json({ version: 1, subject: "operator", token: "must-not-trust" }),
    Response.json({ version: 1, subject: "" })])("rejects login HTML and unexpected session data", async (response) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    await expect(readOperatorSession(new AbortController().signal)).rejects.toThrow();
  });
  it("clears a verified identity while rechecking and ignores unmounted responses", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(sessionResponse());
    vi.stubGlobal("fetch", fetcher);
    const view = render(<OperatorAccess />);
    await screen.findByText("Access verified");
    let finish!: (response: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh access" }));
    await waitFor(() => expect(screen.queryByText("fixture-operator")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Refresh access" })).toBeDisabled();
    const signal = fetcher.mock.calls[1][1].signal as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    finish(sessionResponse("late-operator"));
  });
  it("shows network failures without offering local administrator authentication", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<OperatorAccess />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Access verification unavailable");
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });
});