const LOOPBACK_HOSTS = ["localhost", "127.0.0.1"] as const;

export function developmentLoopbackRedirect(
  requestHost: string | undefined,
  requestUrl: string | undefined,
  canonicalHost: string,
  canonicalPort: number,
): string | null {
  if (!LOOPBACK_HOSTS.includes(canonicalHost as (typeof LOOPBACK_HOSTS)[number])) return null;
  const canonicalAuthority = `${canonicalHost}:${canonicalPort}`;
  const normalizedRequestHost = requestHost?.toLowerCase();
  if (!normalizedRequestHost || normalizedRequestHost === canonicalAuthority) return null;
  const alternateHost = canonicalHost === "localhost" ? "127.0.0.1" : "localhost";
  if (normalizedRequestHost !== `${alternateHost}:${canonicalPort}`) return null;
  const target = requestUrl?.startsWith("/") ? requestUrl : "/";
  return `http://${canonicalAuthority}${target}`;
}