import assert from 'node:assert/strict';
import test from 'node:test';
import { accounted, canReserve, usageCost, RESERVATION_USD } from '../benchmark/run_layoutgpt_controlled.mjs';

test('budget includes complete, pending and ambiguous requests', () => {
  const ledger = { budgetUsd: 35, entries: { a: { actualUsd: 34.7 }, b: { reservedUsd: RESERVATION_USD, status: 'uncertain' } } };
  assert.ok(accounted(ledger) > 35);
  assert.equal(canReserve(ledger), false);
  assert.equal(canReserve({ budgetUsd: 35, entries: { a: { actualUsd: 34.8 } } }), false);
  assert.equal(canReserve({ budgetUsd: 35, entries: {} }), true);
  assert.equal(canReserve({ budgetUsd: 35, previousBatchUsd: 34.8, entries: {} }), false);
  assert.equal(accounted({ previousBatchUsd: 10.25, entries: { a: { actualUsd: .08 } } }), 10.33);
});
test('provider usage is bounded by the conservative reservation', () => {
  assert.ok(usageCost({ prompt_tokens: 8192, completion_tokens: 1024 }) <= RESERVATION_USD);
  assert.equal(usageCost({ prompt_tokens: 3000, completion_tokens: 1024 }), .15144);
  for (const usage of [null, {}, { prompt_tokens: -1, completion_tokens: 1 }, { prompt_tokens: 1, completion_tokens: 1025 }]) {
    assert.throws(() => usageCost(usage));
  }
});
