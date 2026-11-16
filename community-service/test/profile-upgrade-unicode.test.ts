import { it } from 'vitest';
import { verifyProfileUpgradeRefusal } from './helpers/profile-upgrade-refusal';

it('refuses legacy names requiring application-side Unicode normalization without changing their spelling', () =>
  verifyProfileUpgradeRefusal(['Cafe\u0301 Current Name']));