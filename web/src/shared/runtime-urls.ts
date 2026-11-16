/**
 * Public URL information supplied with the HTML document.  The production server
 * replaces the explicit development fixture in index.html with this inert JSON
 * before sending the document; it is deliberately not fetched after React starts.
 */
export interface RuntimeBootstrap {
  version: 1;
  basePath: string;
  apiBasePath: string;
}

const BOOTSTRAP_ID = "editarr-bootstrap";
const DEVELOPMENT_FIXTURE = "development";

let bootstrap: RuntimeBootstrap | undefined;

function bootstrapError(reason: string): Error {
  return new Error(`Editarr runtime bootstrap is invalid: ${reason}. Reload from the configured Editarr URL.`);
}

function isCanonicalBasePath(value: unknown): value is string {
  return typeof value === "string"
    && (value === "/" || (
      /^\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*$/.test(value)
      && !value.split("/").some((segment) => segment === "." || segment === "..")
    ));
}

/** Validate the server-provided path bootstrap before constructing API URLs. */
function readBootstrap(): RuntimeBootstrap {
  const element = document.getElementById(BOOTSTRAP_ID);
  if (!(element instanceof HTMLScriptElement) || element.type !== "application/json") {
    throw bootstrapError("the required application/json script is missing");
  }
  if (element.dataset.editarrBootstrap === DEVELOPMENT_FIXTURE && !import.meta.env.DEV && !import.meta.env.MODE.startsWith("test")) {
    throw bootstrapError("a development fixture was served in production");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(element.textContent ?? "");
  } catch {
    throw bootstrapError("it is not valid JSON");
  }
  if (typeof parsed !== "object" || parsed === null) throw bootstrapError("it must be an object");
  const keys = Object.keys(parsed).sort();
  if (keys.join(",") !== "apiBasePath,basePath,version") throw bootstrapError("it contains unsupported fields");
  const value = parsed as Partial<RuntimeBootstrap>;
  if (value.version !== 1) throw bootstrapError("unsupported version");
  if (!isCanonicalBasePath(value.basePath)) throw bootstrapError("basePath is not a canonical application path");
  const expectedApiBasePath = value.basePath === "/" ? "/api" : `${value.basePath}/api`;
  if (value.apiBasePath !== expectedApiBasePath) throw bootstrapError("apiBasePath does not match basePath");
  return { version: 1, basePath: value.basePath, apiBasePath: value.apiBasePath };
}

/** Read and validate the bootstrap before the application starts making requests. */
export function initializeRuntimeUrls(): RuntimeBootstrap {
  bootstrap ??= readBootstrap();
  return bootstrap;
}

export function runtimeApiBasePath(): string {
  return initializeRuntimeUrls().apiBasePath;
}

/**
 * Build an origin-relative URL for an application-owned static asset.  Callers pass
 * a relative asset name; the configured base path is applied exactly once.
 */
export function applicationAssetUrl(relativePath: string): string {
  if (relativePath.startsWith("/") || relativePath.startsWith("//") || /^[a-z][a-z\d+.-]*:/i.test(relativePath)) {
    throw new Error("Application asset paths must be relative");
  }
  const { basePath } = initializeRuntimeUrls();
  return basePath === "/" ? `/${relativePath}` : `${basePath}/${relativePath}`;
}

/**
 * Keep externally hosted URLs intact and add the current application prefix only
 * when a same-origin, application-owned root-relative URL lacks that prefix.  The
 * backend already returns prefixed application URLs, so this is intentionally
 * idempotent.
 */
export function applicationUrl(url: string): string {
  const parsed = new URL(url, window.location.origin);
  if (parsed.origin !== window.location.origin) return url;

  const { basePath } = initializeRuntimeUrls();
  const prefixed = basePath === "/" || parsed.pathname === basePath || parsed.pathname.startsWith(`${basePath}/`);
  if (prefixed) return url;
  const pathname = parsed.pathname.startsWith("/") ? parsed.pathname : `/${parsed.pathname}`;
  return `${basePath}${pathname}${parsed.search}${parsed.hash}`;
}

/** Test-only reset for explicit root/nested bootstrap fixtures. */
export function resetRuntimeUrlsForTests(): void {
  bootstrap = undefined;
}
