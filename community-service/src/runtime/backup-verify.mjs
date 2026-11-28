/** Worker entry: verify a snapshot file off the event loop (see verifySnapshot in schema.mjs). */
import { parentPort, workerData } from 'node:worker_threads';
import { verifySnapshot } from './schema.mjs';

try { parentPort.postMessage({ ok: true, ...verifySnapshot(workerData.path, { integrity: workerData.integrity }) }); }
catch (error) { parentPort.postMessage({ ok: false, code: String(error?.message ?? 'verification_failed') }); }
