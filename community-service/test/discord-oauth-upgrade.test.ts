import { it } from 'vitest';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';

it('creates the OAuth schema from a fresh isolated SQLite', () => verifyOAuthUpgrade(0));