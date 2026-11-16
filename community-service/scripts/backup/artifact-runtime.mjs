import { registerHooks } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const sourceRoot = new URL('../../src/', import.meta.url).href;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (context.parentURL?.startsWith(sourceRoot) && specifier.startsWith('.')) {
      const url = new URL(specifier, context.parentURL);
      if (!existsSync(fileURLToPath(url)) && existsSync(fileURLToPath(`${url.href}.ts`))) url.pathname += '.ts';
      return { url: url.href, format: 'module', shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url.startsWith(sourceRoot) && url.endsWith('.ts')) return { format: 'module', shortCircuit: true,
      source: ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
      }).outputText };
    if (url.startsWith(sourceRoot) && url.endsWith('.json')) return { format: 'module', shortCircuit: true,
      source: `export default ${JSON.stringify(JSON.parse(readFileSync(fileURLToPath(url), 'utf8')))};` };
    return next(url, context);
  },
});
export const { verifyArtifactSignature } = await import('../../src/artifact/signatures.ts');
export const { withoutArrProvenance } = await import('../../src/catalog/compatibility-inputs.ts');
export const { loadReceiptSigner } = await import('../../src/contributions/receipts.ts');
export const { reconcileImportCounts } = await import('../../src/contributions/imports.ts');
export const { reconcileVoteCounts } = await import('../../src/contributions/votes.ts');
export const { rankRevision } = await import('../../src/reputation/ranking.ts');
export const { readPublication } = await import('../../src/contributions/storage.ts');
export const { lookupIdentifiers } = await import('../../src/catalog/lookup.ts');
hooks.deregister();