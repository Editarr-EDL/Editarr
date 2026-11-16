import { it } from 'vitest';
import { verifyCatalogUpgrade } from './helpers/catalog-upgrade';
it('creates the full fresh catalog schema', () => verifyCatalogUpgrade(0));