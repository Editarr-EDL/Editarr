export class CliError extends Error {
  constructor(code: string, status?: number);
  code: string;
  status: number;
}
export function operatorOrigin(value: string): string;
export function operatorClient(origin: string, token: string,
  fetcher?: (url: URL, options: RequestInit) => Promise<Response>): (path: string, body?: unknown) => Promise<unknown>;