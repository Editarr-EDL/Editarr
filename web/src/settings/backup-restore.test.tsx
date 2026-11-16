import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupRestore } from "./backup-restore";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const plan = {
  kind: "full", protection: "plain", source_identity: null, account_identities: 0, administrator_included: true,
  included_secret_types: [], omitted_environment: [], backup_deployment: { base_url: null, port: null },
};

function restoreJob(state: string, extra: Record<string, unknown> = {}) {
  return json({ id: "restore-job", action: "restore", kind: "full", state, error: null, reason: null, plan: null, inventory: null, report: null, ...extra });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The browser has one setup-restore cookie: each flow response replaces it, and the
// server accepts a request only when its token belongs to the flow in that cookie.
function oneCookieServer() {
  let cookie: string | null = null;
  const flows: Array<() => void> = [];
  const releaseJob: Array<() => void> = [];
  const sent: Array<{ method: string; url: string; token: string | null }> = [];
  let started = 0;
  vi.stubGlobal("fetch", vi.fn((input: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    if (input.endsWith("/setup-restore/flow")) {
      const token = `flow-${++started}`;
      return new Promise<Response>((resolve) => flows.push(() => { cookie = token; resolve(json({ token })); }));
    }
    const token = new Headers(init.headers).get("X-Setup-Restore-Token");
    sent.push({ method, url: input, token });
    if (token !== cookie) return Promise.resolve(json({ detail: "Setup restore request verification failed" }, 403));
    if (method === "POST" && input.endsWith("/setup-restore/jobs")) {
      return new Promise<Response>((resolve) => releaseJob.push(() => resolve(restoreJob("waiting"))));
    }
    if (method === "PUT") return Promise.resolve(restoreJob("uploaded"));
    if (method === "POST" && input.endsWith("/preflight")) return Promise.resolve(restoreJob("ready", { plan }));
    return Promise.resolve(restoreJob("cancelled"));
  }));
  return { flows, releaseJob, sent };
}

async function inspectBackup() {
  const file = new File(["manifest.json\n"], "full.backup");
  Object.defineProperty(file, "slice", { value: () => ({ text: async () => "manifest.json\n" }) });
  fireEvent.change(screen.getByLabelText("Backup file"), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Inspect backup" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Inspect backup" }));
}

describe("SetupRestore", () => {
  it("keeps one restore flow when StrictMode mounts the panel twice and a flow answers late", async () => {
    const { flows, releaseJob, sent } = oneCookieServer();
    render(<StrictMode><SetupRestore onComplete={() => {}} /></StrictMode>);
    await waitFor(() => expect(flows.length).toBeGreaterThan(0));
    flows.shift()?.();
    await waitFor(() => expect(screen.getByLabelText("Backup file")).toBeEnabled());
    await inspectBackup();
    await waitFor(() => expect(releaseJob).toHaveLength(1));
    // Any other flow answers now, after the job was created.
    flows.splice(0).forEach((release) => release());
    releaseJob[0]();

    expect(await screen.findByRole("heading", { name: "Restore preview" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sent.map(({ method, token }) => [method, token])).toEqual([["POST", "flow-1"], ["PUT", "flow-1"], ["POST", "flow-1"]]);
  });

  it("keeps one restore flow when the panel is remounted while its flow is pending", async () => {
    const { flows, releaseJob, sent } = oneCookieServer();
    const first = render(<SetupRestore onComplete={() => {}} />);
    await waitFor(() => expect(flows).toHaveLength(1));
    first.unmount();
    render(<SetupRestore onComplete={() => {}} />);
    // The newest flow answers first; the unmounted panel's flow answers after the job exists.
    flows.pop()?.();
    await waitFor(() => expect(screen.getByLabelText("Backup file")).toBeEnabled());
    await inspectBackup();
    await waitFor(() => expect(releaseJob).toHaveLength(1));
    flows.splice(0).forEach((release) => release());
    releaseJob[0]();

    expect(await screen.findByRole("heading", { name: "Restore preview" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sent.map(({ method, token }) => [method, token])).toEqual([["POST", "flow-1"], ["PUT", "flow-1"], ["POST", "flow-1"]]);
  });

  it("starts a new flow on the next mount after a flow request fails", async () => {
    const answers = [json({ detail: "Unavailable" }, 503), json({ token: "flow-2" })];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(answers.shift())));
    const first = render(<SetupRestore onComplete={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to start restore. Try again.");
    first.unmount();
    render(<SetupRestore onComplete={() => {}} />);

    await waitFor(() => expect(screen.getByLabelText("Backup file")).toBeEnabled());
    expect(answers).toHaveLength(0);
  });
});
