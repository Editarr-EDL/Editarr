import { beforeAll, describe, expect, it } from 'vitest';
import { contributorSubmission } from '../src/http/submissions';
import { DEFAULT_SUBMISSION_LIMITS } from '../src/submissions/input';
import { download, eligible, submitWork } from './helpers/master';
import { artifact, contributor, encode, submitRequest, support, type Contributor } from './helpers/submissions';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

let author: Contributor;
let reader: Contributor;

beforeAll(async () => {
  await setupVersionCatalog([]);
  [author, reader] = await Promise.all([contributor(), contributor()]);
});

describe('MCF 1.1.1 classifications in community artifacts', () => {
  it('preserves every new classification and legacy Violence/Rape in a downloaded master', async () => {
    const categories = [
      { category: 'violence', subcategory: 'gore' },
      { category: 'violence', subcategory: 'abuse' },
      { category: 'language', subcategory: 'innuendo', action: 'mute', expression: 'Example phrase' },
      { category: 'sex', subcategory: 'sexualRape' },
      { category: 'discrimination', subcategory: 'sizeism' },
      { category: 'discrimination', subcategory: 'ableism' },
      { category: 'discrimination', subcategory: 'ageism' },
      { category: 'dispensable_scenes', subcategory: 'other' },
      { category: 'violence', subcategory: 'rape' },
    ];
    const entries = categories.map((classification, index) =>
      support(`cue-${index}`, 1_000 + 10_000 * index, 3_000 + 10_000 * index, classification));

    const result = await submitWork(author, '920', { entries });
    const { snapshot } = await download(reader, result.versionId);
    const filters = [...snapshot.filters].sort((left, right) => left.startMs - right.startMs);

    expect(result.outcome).toMatchObject({ newFilters: 9, supports: 9 });
    expect(filters).toHaveLength(categories.length);
    expect(filters).toMatchObject(categories);
  });

  it.each([
    ['sex', 'rape'],
    ['violence', 'sexualRape'],
  ])('rejects the mismatched pair %s/%s', async (category, subcategory) => {
    const value = artifact(author, { entries: [support('cue', 1_000, 3_000, { category, subcategory })] });
    const request = await submitRequest(author, await encode(value, author));
    const response = await contributorSubmission(request, db, 'community.example', now, eligible,
      { policy, limits: DEFAULT_SUBMISSION_LIMITS });

    expect([response.status, await response.json()]).toEqual([422, { error: { code: 'invalid_taxonomy' } }]);
  });
});
