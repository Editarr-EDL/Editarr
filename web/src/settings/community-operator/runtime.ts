export interface OperatorSession { version: 1; subject: string }
export class OperatorAccessError extends Error {
  constructor(public readonly status: number) { super("Operator access unavailable"); }
}

export async function readOperatorSession(signal: AbortSignal): Promise<OperatorSession> {
  const response = await fetch("/admin/v1/session", {
    method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "application/json" }, signal,
  });
  if (!response.ok) throw new OperatorAccessError(response.status);
  if (!response.headers.get("Content-Type")?.startsWith("application/json")) throw new OperatorAccessError(503);
  const value: unknown = await response.json();
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new OperatorAccessError(503);
  const record = value as Record<string, unknown>;
  if (Object.keys(record).sort().join(",") !== "subject,version" || record.version !== 1 ||
      typeof record.subject !== "string" || !record.subject || record.subject.length > 256)
    throw new OperatorAccessError(503);
  return { version: 1, subject: record.subject };
}