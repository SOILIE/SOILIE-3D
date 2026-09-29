import assert from 'node:assert/strict';
import test from 'node:test';
import { accounted, canReserve, canRetryCreditError, workerCount, usageCost, RESERVATION_USD, validatePlan } from '../benchmark/run_layoutgpt_controlled.mjs';

test('API concurrency can decrease but cannot exceed its fixed maximum', () => {
  assert.equal(workerCount(),3);
  assert.equal(workerCount(1),1);
  for (const value of [0,4,1.5,NaN]) assert.throws(()=>workerCount(value));
});

test('only explicitly authorized credit rejections retry, with previous spending retained', () => {
  const rejected={status:'error',httpStatus:429,errorCode:'credit_balance_exhausted',reservedUsd:RESERVATION_USD};
  assert.equal(canRetryCreditError(rejected,false),false);
  assert.equal(canRetryCreditError(rejected,true),true);
  for (const change of [{status:'complete'},{status:'uncertain'},{httpStatus:500},{errorCode:'rate_limit_exceeded'}])
    assert.equal(canRetryCreditError({...rejected,...change},true),false);
  assert.equal(accounted({entries:{a:{actualUsd:.08,previousAttempts:[rejected]}}}),.08+RESERVATION_USD);
  assert.equal(canReserve({budgetUsd:35,entries:{a:{actualUsd:34.5,previousAttempts:[rejected]}}}),false);
});

test('inventory requests share one fixed cap and require valid unique tasks', () => {
  const plan = {variant:'shared-inventory-gpt4-v1',budgetUsd:35,campaignSha256:'a'.repeat(64),requests:[{
    id:'layoutgpt-bedroom-000',roomType:'bedroom',requestedObjects:3,requestedInventory:{bed:1,chair:2},
    estimatedInputTokens:4000,request:{model:'gpt-4',max_tokens:1024,n:1,messages:[]}}]};
  assert.doesNotThrow(()=>validatePlan(plan,1));
  for (const override of [{budgetUsd:36},{requests:[plan.requests[0],plan.requests[0]]},{previousBatch:{}}])
    assert.throws(()=>validatePlan({...plan,...override},1));
  assert.throws(()=>validatePlan({...plan,requests:[{...plan.requests[0],requestedObjects:4}]},1));
});

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
