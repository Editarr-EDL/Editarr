import { it } from 'vitest';
import { verifyCatalogUpgrade } from './helpers/catalog-upgrade';
it('preserves populated 0003 through catalog upgrade', () => verifyCatalogUpgrade(3));