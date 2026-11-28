export function fileSha256(path: string): Promise<string>;
export function restoreDatabase(source: string, path: string, sha256: string, root?: URL): Promise<{ version: number; current: number }>;
