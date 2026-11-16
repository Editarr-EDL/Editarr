import { it } from 'vitest';
import { verifyProfileUpgradeRefusal } from './helpers/profile-upgrade-refusal';

it('refuses conflicting legacy names without renaming or deleting contributors', () =>
  verifyProfileUpgradeRefusal(['Legacy Current Name', 'legacy current name']));