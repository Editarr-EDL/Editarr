import { it } from 'vitest';
import { verifyCatalogUpgrade } from './helpers/catalog-upgrade';
it('preserves populated 0002 through catalog upgrade', () => verifyCatalogUpgrade(2));