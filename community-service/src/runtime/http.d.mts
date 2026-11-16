import type { Server } from 'node:http';
import type { Env } from '../config';
import type { AssetFetcher } from './contracts';

export function operatorAssets(root: URL): AssetFetcher;
export function createHttpServer(handler: (request: Request) => Response | Promise<Response>, configuration: {
  env: Env; enabled: boolean; requestBytes: number; ready?: () => boolean;
}): Server;