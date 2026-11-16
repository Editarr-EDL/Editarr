import { it } from 'vitest';
import { verifyCatalogUpgrade } from './helpers/catalog-upgrade';
it('preserves populated 0008 including name-change limits through catalog upgrade', () => verifyCatalogUpgrade(8));