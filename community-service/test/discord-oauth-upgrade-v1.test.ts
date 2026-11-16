import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0001 through the OAuth upgrade', () => verifyOAuthUpgrade(1));