import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0004 through the OAuth upgrade', () => verifyOAuthUpgrade(4));