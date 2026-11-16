import type { Env } from '../config';

export function tunnelActor(request: Request): string | null;
export function readConfiguration(path?: string, secretsPath?: string): {
  enabled: boolean; trustedTunnel: boolean; requestBytes: number; env: Env;
};