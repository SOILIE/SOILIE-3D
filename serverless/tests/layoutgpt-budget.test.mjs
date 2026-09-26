import assert from 'node:assert/strict';
import test from 'node:test';
import { accounted, canReserve, usageCost, RESERVATION_USD, validatePlan } from '../benchmark/run_layoutgpt_controlled.mjs';

test('twenty-bedroom pilot has a separate strict cap and original output limit', () => {
  const plan = { variant: 'bedroom-original-prompt-timing', roomType: 'bedroom', budgetUsd: 6.15,
    requests: Array.from({length: 20}, () => ({ estimatedInputTokens: 4000,
      request: { model: 'gpt-4', max_tokens: 512, n: 1, messages: [] } })) };
  assert.doesNotThrow(() => validatePlan(plan, 20));
  for (const overrides of [{budgetUsd: 35}, {requests: [...plan.requests, plan.requests[0]]},
    {previousBatch: {}}, {roomType: 'living_room'}]) assert.throws(() => validatePlan({...plan, ...overrides}, 20));
  assert.throws(() => validatePlan(plan, 21));
  const ledger = {budgetUsd: 6.15, entries: {}};
  for (let i = 0; i < 20; i++) {
    assert.ok(canReserve(ledger));
    ledger.entries[i] = {reservedUsd: RESERVATION_USD};
  }
  assert.equal(canReserve(ledger), false);
  assert.ok(accounted(ledger) <= 6.15);
});

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
