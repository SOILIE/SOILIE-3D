import assert from 'node:assert/strict';
import test from 'node:test';

import { commitLevel, nextVersion } from '../../scripts/versioning.mjs';

test('commit levels produce semantic model versions', () => {
  assert.equal(nextVersion('4.0.0', 'patch'), '4.0.1');
  assert.equal(nextVersion('4.0.1', 'minor'), '4.1.0');
  assert.equal(nextVersion('4.1.7', 'major'), '5.0.0');
});

test('invalid versions and prefixes are rejected', () => {
  assert.throws(() => nextVersion('4.0', 'patch'));
  assert.throws(() => nextVersion('4.0.0', 'release'));
});

test('only a leading release prefix opts a commit into a bump', () => {
  assert.equal(commitLevel('patch: repair a bug'), 'patch');
  assert.equal(commitLevel('  MINOR: paper release'), 'minor');
  assert.equal(commitLevel('docs: mention major: elsewhere'), null);
});
