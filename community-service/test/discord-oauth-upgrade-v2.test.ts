import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0002 through the OAuth upgrade', () => verifyOAuthUpgrade(2));