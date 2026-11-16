import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0006 through the membership upgrade', () => verifyOAuthUpgrade(6));