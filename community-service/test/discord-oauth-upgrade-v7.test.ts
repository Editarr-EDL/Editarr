import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0007 through confirmed enrollment/profile upgrade', () => verifyOAuthUpgrade(7));