import { it } from 'vitest';
import { verifyCatalogUpgrade } from './helpers/catalog-upgrade';
it('preserves populated 0007 through catalog upgrade', () => verifyCatalogUpgrade(7));