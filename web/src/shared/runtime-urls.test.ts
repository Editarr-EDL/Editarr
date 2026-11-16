import { afterEach, describe, expect, it } from "vitest";

import {
  applicationAssetUrl,
  applicationUrl,
  initializeRuntimeUrls,
  resetRuntimeUrlsForTests,
  runtimeApiBasePath,
} from "./runtime-urls";

function setBootstrap(json: string, attributes = ""): void {
  document.getElementById("editarr-bootstrap")?.remove();
  document.head.insertAdjacentHTML(
    "beforeend",
    `<script id="editarr-bootstrap" type="application/json" ${attributes}>${json}</script>`,
  );
  resetRuntimeUrlsForTests();
}

describe("runtime URL bootstrap", () => {
  afterEach(() => {
    setBootstrap('{"version":1,"basePath":"/","apiBasePath":"/api"}', 'data-editarr-bootstrap="development"');
  });

  it("uses an explicit nested bootstrap for API and application assets", () => {
    setBootstrap('{"version":1,"basePath":"/media/editarr","apiBasePath":"/media/editarr/api"}');

    expect(initializeRuntimeUrls()).toEqual({
      version: 1,
      basePath: "/media/editarr",
      apiBasePath: "/media/editarr/api",
    });
    expect(runtimeApiBasePath()).toBe("/media/editarr/api");
    expect(applicationAssetUrl("editarr-app-icon.png?v=1")).toBe("/media/editarr/editarr-app-icon.png?v=1");
    expect(applicationUrl("/media/editarr/api/library-items/one/image")).toBe("/media/editarr/api/library-items/one/image");
    expect(applicationUrl("/api/library-items/one/image")).toBe("/media/editarr/api/library-items/one/image");
    expect(applicationUrl("https://jellyfin.example/Items/one/Images/Primary")).toBe("https://jellyfin.example/Items/one/Images/Primary");
  });

  it("uses root paths without a doubled separator", () => {
    setBootstrap('{"version":1,"basePath":"/","apiBasePath":"/api"}');

    expect(applicationAssetUrl("favicon.ico?v=4")).toBe("/favicon.ico?v=4");
    expect(applicationUrl("/api/library-items/one/image")).toBe("/api/library-items/one/image");
  });

  it("fails clearly when the public bootstrap is missing or malformed", () => {
    document.getElementById("editarr-bootstrap")?.remove();
    resetRuntimeUrlsForTests();
    expect(() => initializeRuntimeUrls()).toThrow("runtime bootstrap is invalid");

    setBootstrap('{"version":1,"basePath":"/editarr/","apiBasePath":"/editarr/api"}');
    expect(() => initializeRuntimeUrls()).toThrow("basePath is not a canonical application path");
  });

  it("rejects mismatched API roots and non-relative asset paths", () => {
    setBootstrap('{"version":1,"basePath":"/editarr","apiBasePath":"/api"}');
    expect(() => initializeRuntimeUrls()).toThrow("apiBasePath does not match basePath");

    setBootstrap('{"version":1,"basePath":"/editarr","apiBasePath":"/editarr/api"}');
    expect(() => applicationAssetUrl("/favicon.ico")).toThrow("must be relative");
    expect(() => applicationAssetUrl("https://example.com/favicon.ico")).toThrow("must be relative");
  });

  it.each(["/caf%C3%A9", "/%65ditarr", "/café", "/a&b", "/a/../b"])(
    "rejects unsupported path syntax in bootstrap: %s",
    (basePath) => {
      setBootstrap(JSON.stringify({ version: 1, basePath, apiBasePath: `${basePath}/api` }));
      expect(() => initializeRuntimeUrls()).toThrow("basePath is not a canonical application path");
    },
  );

  it("accepts only the public, non-secret bootstrap schema", () => {
    setBootstrap('{"version":1,"basePath":"/editarr","apiBasePath":"/editarr/api","cookie":"secret"}');
    expect(() => initializeRuntimeUrls()).toThrow("contains unsupported fields");
  });
});
