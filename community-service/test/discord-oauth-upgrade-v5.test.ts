import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('preserves populated version 0005 through the OAuth usage upgrade', () => verifyOAuthUpgrade(5));