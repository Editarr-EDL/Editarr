import { describe, expect, it } from "vitest";
import { developmentLoopbackRedirect } from "./development-origin";

describe("development loopback origin", () => {
  it("redirects either alternate loopback spelling to the configured origin", () => {
    expect(developmentLoopbackRedirect(
      "127.0.0.1:5173",
      "/editarr/?tab=active",
      "localhost",
      5173,
    )).toBe("http://localhost:5173/editarr/?tab=active");
    expect(developmentLoopbackRedirect(
      "LOCALHOST:5173",
      "/#/settings/general",
      "127.0.0.1",
      5173,
    )).toBe("http://127.0.0.1:5173/#/settings/general");
  });

  it("does not redirect the canonical or an unrecognized host", () => {
    expect(developmentLoopbackRedirect("localhost:5173", "/", "localhost", 5173)).toBeNull();
    expect(developmentLoopbackRedirect("example.test:5173", "/", "localhost", 5173)).toBeNull();
  });

  it("preserves the requested address when listening on all interfaces", () => {
    for (const host of ["localhost", "127.0.0.1", "10.0.0.64", "10.0.0.99"]) {
      expect(developmentLoopbackRedirect(`${host}:5173`, "/", "0.0.0.0", 5173)).toBeNull();
    }
  });
});