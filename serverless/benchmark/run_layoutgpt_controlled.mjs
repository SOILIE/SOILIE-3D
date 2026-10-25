// Explicitly budgeted GPT-4 calls. This runner never automatically retries an
// ambiguous transport failure: its maximum possible charge stays reserved.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync, openSync, closeSync, unlinkSync } from 'node:fs';
import { resolve, join, relative, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

export const RATE = { input: 30 / 1e6, output: 60 / 1e6 };
// GPT-4's full 8,192-token context is reserved at input rates, plus its
// maximum 1,024 output tokens at output rates. This intentionally double-
// counts reserved output context and covers tokenizer-estimate differences.
export const RESERVATION_USD = 8192 * RATE.input + 1024 * RATE.output;
export const EXTENSION_VARIANT = 'inventory-extension-7-8-gpt4-v1';
const AUTHOR_COMMIT = 'fc31954962553e5b65bf267a904a6930d50b1f5e';
const AUTHOR_PROMPT_SHA256 = '8aab0891a1cf60aab64b184f04170a42c7cee2832f299643ecb88a7c38048c24';
export const OBJECT_COUNT_DEFINITION = 'non-window-instances-v1';
export function archiveConfiguration(archiveDate = '2026-10-25') {
  if (typeof archiveDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(archiveDate) || archiveDate.startsWith('0000')) {
    throw new Error('The public output date must be an ISO calendar date');
  }
  const parsed = new Date(`${archiveDate}T00:00:00.000Z`);
  if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== archiveDate) {
    throw new Error('The public output date must be an ISO calendar date');
  }
  return { schemaVersion: 1, bucket: 'soilie3d-data',
    publicPrefix: `files/outputs/${archiveDate}/layoutgpt/`,
    privatePrefix: `private/new-benchmark-${archiveDate.replaceAll('-', '')}/layoutgpt/`,
    parserSha256: '5e8ea10b8d0980da40fe311434a9475f6f605e51b2d5c3cab6e8f87c0f9b5e56' };
}
export function planArchiveConfiguration(plan) {
  const match = /^files\/outputs\/(\d{4}-\d{2}-\d{2})\/$/.exec(plan.publicOutputPrefix || '');
  if (!match) throw new Error('The frozen plan needs an exact dated public output prefix');
  const expected = archiveConfiguration(match[1]);
  if (canonicalJson(plan.outputArchive) !== canonicalJson(expected)) {
    throw new Error('The frozen bucket, public/private date, or parser contract differs');
  }
  return expected;
}
const charge = row => row.actualUsd ?? row.reservedUsd;
export function accounted(ledger) {
  return Object.values(ledger.entries).reduce((sum, row) => sum + charge(row) +
    (row.previousAttempts || []).reduce((total, attempt) => total + charge(attempt), 0), ledger.previousBatchUsd || 0);
}
export function canRetryCreditError(entry, authorized) {
  return Boolean(authorized && entry?.status === 'error' && entry.httpStatus === 429 &&
    entry.errorCode === 'credit_balance_exhausted');
}
export function canReserve(ledger) { return accounted(ledger) + RESERVATION_USD <= ledger.budgetUsd; }
export function usageCost(usage) {
  if (!Number.isSafeInteger(usage?.prompt_tokens) || !Number.isSafeInteger(usage?.completion_tokens) ||
      usage.prompt_tokens < 0 || usage.completion_tokens < 0 || usage.prompt_tokens > 8192 || usage.completion_tokens > 1024) {
    throw new Error('Unexpected provider token accounting; retain reservation');
  }
  return usage.prompt_tokens * RATE.input + usage.completion_tokens * RATE.output;
}
const sha = value => createHash('sha256').update(value).digest('hex');
// The new protocol hashes compact, key-sorted JSON in both Python and Node.
// Legacy plans keep their original hashes and spending limits unchanged.
export const canonicalJson = value => JSON.stringify(value, (_, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
export const extensionDigest = value => sha(canonicalJson(value));
const save = (path, value) => {
  const temp = path + '.new'; writeFileSync(temp, JSON.stringify(value, null, 2)); renameSync(temp, path);
};
const saveBytes = (path, body) => {
  const temp = path + '.new'; writeFileSync(temp, body); renameSync(temp, path);
};

function claimExtensionAuthorization(folder, plan, planRaw) {
  planArchiveConfiguration(plan);
  if (plan.perCell !== 30 || plan.budgetUsd !== 40 ||
      plan.objectCountDefinition !== OBJECT_COUNT_DEFINITION ||
      plan.inventoryPolicy !== 'fresh-soilie-first-scheduled-local-v1' ||
      plan.requests.some(row => row.task.inventoryOrigin !== 'fresh-soilie')) {
    throw new Error('This authorization is 120 fixed shared-inventory attempts under one US$40 ceiling');
  }
  const sourceRaw = readFileSync(join(folder, 'source-slots.json'));
  const sources = JSON.parse(sourceRaw);
  const slots = new Map(sources.tasks.map(row => [row.taskId, row]));
  if (!/^[a-f0-9]{64}$/.test(plan.sourceSlotsSha256 || '') || sha(sourceRaw) !== plan.sourceSlotsSha256 ||
      sources.policy !== plan.inventoryPolicy || sources.sourcePlatform !== 'local' ||
      sources.objectCountDefinition !== OBJECT_COUNT_DEFINITION ||
      slots.size !== plan.requests.length || plan.requests.some(row =>
        slots.get(row.id)?.soilieSceneId !== row.task.soilieSceneId ||
        sources.soilieCampaignSha256 !== row.task.soilieCampaignSha256)) {
    throw new Error('Frozen quality-blind shared source slots changed');
  }
  // This is the one historical US$40 authorization anchor, NOT an output-date
  // namespace. Never create a new allowance merely by changing a folder date.
  const directory = resolve(import.meta.dirname, '../../.codex/benchmark/replacement-baselines-20261023/layoutgpt-authorization');
  mkdirSync(directory, { recursive: true });
  const path = join(directory, 'binding.json');
  const binding = { schemaVersion: 1, folder, planSha256: sha(planRaw), budgetUsd: 40, totalAttempts: 120 };
  try {
    const owner = openSync(path, 'wx');
    try { writeFileSync(owner, JSON.stringify(binding)); } finally { closeSync(owner); }
    return true;
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    if (canonicalJson(JSON.parse(readFileSync(path))) !== canonicalJson(binding)) {
      throw new Error('The approved US$40 campaign is already bound elsewhere; never reset it with another folder');
    }
    return false;
  }
}

export function validatePlan(plan, limit) {
  const bedroom = plan.variant === 'bedroom-original-prompt-timing';
  const extension = plan.variant === EXTENSION_VARIANT;
  const inventory = plan.variant === 'shared-inventory-gpt4-v1' || extension;
  const budget = extension ? plan.budgetUsd : bedroom ? 6.15 : 35;
  if (extension) validateExtension(plan);
  const count = inventory ? plan.requests.length : bedroom ? 20 : plan.previousBatch ? 1 : 120;
  if (inventory && plan.previousBatch && (!Number.isSafeInteger(plan.previousBatch.expectedRequests) ||
      plan.previousBatch.expectedRequests < 1 || plan.previousBatch.expectedRequests > 240 ||
      !/^[a-f0-9]{64}$/.test(plan.previousBatch.ledgerSha256 || '') ||
      !/^[a-f0-9]{64}$/.test(plan.previousBatch.planSha256 || '') ||
      !Number.isFinite(plan.previousBatch.accountedUsd) || plan.previousBatch.accountedUsd < 0)) {
    throw new Error('Invalid shared-budget parent receipt');
  }
  if (inventory && (count < 1 || count > 240 ||
      !/^[a-f0-9]{64}$/.test(plan.campaignSha256) || new Set(plan.requests.map(r => r.id)).size !== count)) {
    throw new Error('Invalid inventory campaign');
  }
  if (plan.budgetUsd !== budget || plan.requests.length !== count || (bedroom && plan.previousBatch) ||
      !Number.isInteger(limit) || limit < 1 || limit > count) throw new Error('Unexpected approved plan or spending cap');
  if (bedroom && plan.roomType !== 'bedroom') throw new Error('Bedroom pilot room type changed');
  for (const row of plan.requests) {
    if (inventory && (!['bedroom','living_room'].includes(row.roomType) ||
        !(extension ? /^layoutgpt-[a-z0-9-]+-(bedroom|living_room)-[78]-\d{3}$/ : /^layoutgpt-(bedroom|living_room)-\d{3}$/).test(row.id) ||
        !Number.isInteger(row.requestedObjects) || row.requestedObjects < (extension ? 7 : 3) || row.requestedObjects > (extension ? 8 : 6) ||
        !row.requestedInventory || Object.values(row.requestedInventory).some(n => !Number.isInteger(n) || n < 1) ||
        Object.values(row.requestedInventory).reduce((a,b) => a+b,0) !== row.requestedObjects)) {
      throw new Error('Invalid requested inventory');
    }
    const request = row.request;
    if (request.model !== 'gpt-4' || request.max_tokens !== (bedroom ? 512 : 1024) || request.n !== 1 ||
        !Array.isArray(request.messages) || !Number.isSafeInteger(row.estimatedInputTokens) ||
        row.estimatedInputTokens < 1 || row.estimatedInputTokens + request.max_tokens > 8192) throw new Error('Unexpected inference configuration');
  }
}

function validateExtension(plan) {
  planArchiveConfiguration(plan);
  if (plan.schemaVersion !== 2 || plan.previousBatch ||
      plan.objectCountDefinition !== OBJECT_COUNT_DEFINITION ||
      !/^[a-z][a-z0-9-]{2,39}$/.test(plan.campaignId || '') ||
      plan.sourceCommit !== AUTHOR_COMMIT || plan.authorPromptSha256 !== AUTHOR_PROMPT_SHA256 ||
      plan.expectedResponseModel !== 'gpt-4-0613' ||
      !/^[a-f0-9]{64}$/.test(plan.taskManifestSha256 || '') ||
      !/^[a-f0-9]{64}$/.test(plan.retainedCampaignSha256 || '') ||
      !Number.isInteger(plan.perCell) || plan.perCell < 1 || plan.perCell > 60 ||
      !Number.isFinite(plan.budgetUsd) || plan.budgetUsd <= 0 ||
      plan.requests.length !== plan.perCell * 4 ||
      plan.budgetUsd + 1e-9 < plan.requests.length * RESERVATION_USD ||
      plan.promptProtocol !== 'authors-css-k8-bedroom-k4-living-room-inventory-v1') {
    throw new Error('Invalid seven/eight inventory extension protocol');
  }
  const cells = new Map();
  const seeds = new Set(), sources = new Set();
  for (const row of plan.requests) {
    const task = row.task;
    const request = row.request;
    const expectedId = `layoutgpt-${plan.campaignId}-${row.roomType}-${row.requestedObjects}-`;
    const native = row.nativeInventory;
    const windowLabel = label => ['window', 'opaque_window'].includes(label.trim().toLowerCase().replace(/[-\s]+/g, '_'));
    const positiveInventory = value => value && !Array.isArray(value) && typeof value === 'object' &&
      Object.keys(value).length > 0 && Object.entries(value).every(([label, n]) =>
        /^[a-z][a-z_ ]*$/.test(label) && !windowLabel(label) && Number.isInteger(n) && n > 0);
    const origin = task?.inventoryOrigin;
    const allowedTaskKeys = ['id', 'roomType', 'requestedObjects', 'seed', 'inventory', 'nativeInventory', 'inventoryOrigin',
      ...(origin === 'fresh-soilie' ? ['soilieSceneId', 'soilieSceneSha256', 'soilieCampaignSha256', 'sourceInventory', 'substitutions'] : [])];
    if (!row.id.startsWith(expectedId) || !task || task.id !== row.id ||
        !['fresh-soilie', 'predeclared-model-native'].includes(origin) ||
        canonicalJson(Object.keys(task).sort()) !== canonicalJson(allowedTaskKeys.sort()) ||
        (origin === 'fresh-soilie' && (!/^[A-Za-z0-9_.-]+$/.test(task.soilieSceneId || '') ||
          !/^[a-f0-9]{64}$/.test(task.soilieSceneSha256 || '') ||
          !/^[a-f0-9]{64}$/.test(task.soilieCampaignSha256 || '') || !positiveInventory(task.sourceInventory) ||
          !Array.isArray(task.substitutions) || task.substitutions.length > 1)) ||
        task.roomType !== row.roomType || task.requestedObjects !== row.requestedObjects ||
        !Number.isSafeInteger(task.seed) || task.seed < 0 || seeds.has(task.seed) ||
        !positiveInventory(native) || !positiveInventory(row.requestedInventory) ||
        Object.values(native).reduce((a,b) => a+b,0) !== row.requestedObjects ||
        canonicalJson(task.inventory) !== canonicalJson(row.requestedInventory) ||
        canonicalJson(task.nativeInventory) !== canonicalJson(native) ||
        row.taskSha256 !== extensionDigest(task) || row.requestSha256 !== extensionDigest(request) ||
        typeof row.sourceRoomId !== 'string' || !row.sourceRoomId ||
        sources.has(`${row.roomType}:${row.sourceRoomId}`) ||
        request?.temperature !== 0.7 || request?.top_p !== 1 || request?.frequency_penalty !== 0 ||
        request?.presence_penalty !== 0 || request?.stop !== 'Condition:') {
      throw new Error('Changed extension task, prompt, or immutable input');
    }
    if (origin === 'fresh-soilie') {
      const mapped = { ...task.sourceInventory };
      for (const change of task.substitutions) {
        if (!change || canonicalJson(Object.keys(change).sort()) !== canonicalJson(['from', 'to']) ||
            typeof change.from !== 'string' || typeof change.to !== 'string' || !(mapped[change.from] > 0)) {
          throw new Error('Invalid disclosed instance substitution');
        }
        mapped[change.from] -= 1;
        mapped[change.to] = (mapped[change.to] || 0) + 1;
      }
      const inventory = Object.fromEntries(Object.entries(mapped).filter(([, count]) => count > 0));
      if (canonicalJson(inventory) !== canonicalJson(row.requestedInventory)) {
        throw new Error('Disclosed substitutions differ from requested inventory');
      }
    }
    const inventoryText = Object.keys(native).sort().map(label => `${label}: ${native[label]}`).join(', ');
    const instruction = `Generate exactly ${row.requestedObjects} furniture instances with this inventory: ${inventoryText}. ` +
      'Include no other objects. Use one CSS line per instance; count repeated instances separately.\nLayout:\n';
    if (!request.messages?.at(-1)?.content?.endsWith(instruction) ||
        request.messages[0]?.role !== 'system' || request.messages.at(-1).role !== 'user') {
      throw new Error('Inventory instruction differs from the frozen prompt protocol');
    }
    seeds.add(task.seed); sources.add(`${row.roomType}:${row.sourceRoomId}`);
    const cell = `${row.roomType}:${row.requestedObjects}`;
    cells.set(cell, (cells.get(cell) || 0) + 1);
  }
  if (['bedroom:7', 'bedroom:8', 'living_room:7', 'living_room:8'].some(cell => cells.get(cell) !== plan.perCell)) {
    throw new Error('Extension requires the frozen equal four-cell attempt allocation');
  }
}

export function workerCount(value = 3) {
  if (!Number.isInteger(value) || value < 1 || value > 3) throw new Error('Expected 1-3 API workers');
  return value;
}

export async function run(folder, credentialFile, limit = null, retryCreditError = false, workers = 3, authorizedBudgetUsd = null,
                          archivePython = null, archiveProfile = 'darkest') {
  workerCount(workers);
  folder = resolve(folder);
  const raw = readFileSync(join(folder, 'requests.json'));
  const plan = JSON.parse(raw);
  limit ??= plan.requests.length;
  validatePlan(plan, limit);
  // A prepared extension is not spending authorization. Require an explicit
  // operator ceiling on every invocation, including resumes and known retries.
  if (plan.variant === EXTENSION_VARIANT && authorizedBudgetUsd !== plan.budgetUsd) {
    throw new Error('Pass --authorized-budget-usd matching the separately approved extension cap');
  }
  const extension = plan.variant === EXTENSION_VARIANT;
  if (extension && (!archivePython || !plan.outputArchive || retryCreditError)) {
    throw new Error('Extension requires verified incremental archival and exactly one attempt per frozen task');
  }
  if (extension) {
    const scoped = relative(resolve(import.meta.dirname, '../../.codex'), folder);
    if (isAbsolute(scoped) || scoped === '..' || scoped.startsWith('../') || scoped.startsWith('..\\')) {
      throw new Error('Extension working evidence must remain inside this project .codex directory');
    }
  }
  const storage = (command, id = null) => new Promise((accept, reject) => {
    const args = ['-m', 'serverless.benchmark.layoutgpt_extension_store', command,
      '--root', folder, '--profile', archiveProfile, '--node-path', process.execPath];
    if (id !== null) args.push('--task-id', id);
    // Do not block the event loop while another worker's paid HTTP response is
    // in flight: unrelated S3 latency must not inflate its inference timer.
    const child = spawn(archivePython, args, { cwd: resolve(import.meta.dirname, '../..'),
      stdio: ['ignore', 'pipe', 'ignore'] });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk.toString(); });
    child.on('error', () => reject(new Error('Private evidence process could not start; stop inference')));
    child.on('close', code => {
      if (code !== 0) reject(new Error('Private evidence archival failed; resume storage before more inference'));
      else { if (output.trim()) console.log(output.trim()); accept(); }
    });
  });
  const archive = id => storage('archive', id);
  let previousBatchUsd = 0;
  if (plan.previousBatch) {
    // Windows invokes this runner; the preparation step may use WSL paths.
    const previousFolder = plan.previousBatch.folder.replace(/^\/mnt\/([a-z])\//i, (_, drive) => drive.toUpperCase() + ':/');
    const previousRaw = readFileSync(join(previousFolder, 'inference-ledger.json'));
    const previous = JSON.parse(previousRaw);
    if (sha(previousRaw) !== plan.previousBatch.ledgerSha256 || previous.planSha256 !== plan.previousBatch.planSha256 ||
        Object.keys(previous.entries).length !== (plan.previousBatch.expectedRequests ?? 120) ||
        previous.budgetUsd !== plan.budgetUsd || existsSync(join(previousFolder, 'inference.lock')) ||
        Object.values(previous.entries).some(row => row.status !== 'complete')) {
      throw new Error('Previous batch changed or contains unsettled spending');
    }
    previousBatchUsd = accounted(previous);
    if (Math.abs(previousBatchUsd - plan.previousBatch.accountedUsd) > 1e-9) throw new Error('Previous spending differs');
  }
  const lockPath = join(folder, 'inference.lock');
  const lock = openSync(lockPath, 'wx');
  writeFileSync(lock, JSON.stringify({ pid: process.pid }));
  try {
    let newAuthorization = false;
    if (extension) {
      await storage('validate-runtime');
      newAuthorization = claimExtensionAuthorization(folder, plan, raw);
    }
    const ledgerPath = join(folder, 'inference-ledger.json');
    if (extension && !newAuthorization && !existsSync(ledgerPath)) {
      throw new Error('Approved campaign spending ledger is missing; never reset previously reserved charges');
    }
    const ledger = existsSync(ledgerPath) ? JSON.parse(readFileSync(ledgerPath)) : {
      schemaVersion: 1, planSha256: sha(raw), budgetUsd: plan.budgetUsd, previousBatchUsd, rate: RATE, entries: {},
    };
    if (ledger.planSha256 !== sha(raw) || ledger.budgetUsd !== plan.budgetUsd || (ledger.previousBatchUsd || 0) !== previousBatchUsd) throw new Error('Resume plan or budget changed');
    if (extension) {
      // Create the durable empty ledger before credentials or dispatch. Once
      // approval is bound, its disappearance is a reconciliation error, never
      // implicit permission to start a fresh spending balance.
      if (!existsSync(ledgerPath)) save(ledgerPath, ledger);
      // A crash after a durable reservation may already have incurred a
      // charge. Never retry it; settle its conservative reservation and store
      // whatever response bytes survived before dispatching anything else.
      for (const [id, entry] of Object.entries(ledger.entries)) {
        if (entry.status === 'reserved') {
          entry.status = 'uncertain'; entry.errorCode = 'PROCESS_INTERRUPTED_AFTER_RESERVATION';
          entry.finishedAt = new Date().toISOString();
          const orphan = join(folder, 'responses', id + '.json');
          if (existsSync(orphan) && !entry.responseSha256) {
            entry.responseSha256 = sha(readFileSync(orphan));
            entry.orphanProviderBodyRecovered = true;
          }
          save(ledgerPath, ledger);
        }
        await archive(id);
      }
    }
    // Credential material is used only in the authorization header. Neither
    // the path nor its contents is copied into publication or invocation logs.
    const secret = credentialFile ? JSON.parse(readFileSync(credentialFile, 'utf8')).OpenAI?.api_key
      : process.env.OPENAI_API_KEY;
    if (typeof secret !== 'string' || !secret.trim()) throw new Error('An OpenAI API key is required');
    const responses = join(folder, 'responses'); mkdirSync(responses, { recursive: true });
    const wireRequests = join(folder, 'wire-requests');
    if (extension) mkdirSync(wireRequests, { recursive: true });
    let cursor = 0, stopped = false;
    const jobs = plan.requests.slice(0, limit);
    async function worker() {
      while (cursor < jobs.length && !stopped) {
        const row = jobs[cursor++];
        const previous = ledger.entries[row.id];
        if (previous && !canRetryCreditError(previous, retryCreditError)) continue;
        if (extension && (existsSync(join(wireRequests, row.id + '.json')) ||
            existsSync(join(responses, row.id + '.json')) || existsSync(join(folder, 'archive', row.id, 'remote.json')))) {
          stopped = true;
          throw new Error('Unledgered request evidence exists; reconcile it without another paid call');
        }
        // Only an explicit operator flag can retry a known billing rejection.
        // Its receipt and conservative spending reservation remain in history;
        // completed or ambiguous requests are NEVER automatically repeated.
        if (!canReserve(ledger)) { stopped = true; break; }
        const wire = JSON.stringify(row.request);
        const entry = { status: 'reserved', reservedUsd: RESERVATION_USD, requestSha256: row.requestSha256 };
        if (extension) {
          saveBytes(join(wireRequests, row.id + '.json'), wire);
          entry.wireRequestSha256 = sha(wire); entry.dispatchedAt = new Date().toISOString();
        }
        if (previous) {
          const { previousAttempts = [], ...attempt } = previous;
          entry.previousAttempts = [...previousAttempts, attempt];
        }
        ledger.entries[row.id] = entry;
        save(ledgerPath, ledger); // Synchronous reservation before any network side effect.
        const started = performance.now();
        try {
          const response = await fetch('https://api.openai.com/v1/chat/completions', {
            method: 'POST', headers: { Authorization: 'Bearer ' + secret, 'Content-Type': 'application/json' },
            body: wire, signal: AbortSignal.timeout(180000),
          });
          entry.httpStatus = response.status;
          entry.providerRequestId = response.headers.get('x-request-id');
          // Preserve the provider body bytes, rather than only a reserialized
          // interpretation. Authorization headers are never evidence inputs.
          let responseBytes;
          let payload;
          if (extension) {
            responseBytes = Buffer.from(await response.arrayBuffer());
            if (responseBytes.includes(Buffer.from(secret))) {
              // A provider should never echo a credential. Do not copy it to
              // evidence if it does; keep a disclosed redaction and raw hash.
              entry.originalProviderBodySha256 = sha(responseBytes);
              responseBytes = Buffer.from(responseBytes.toString('utf8').replaceAll(secret, '[redacted]'));
              entry.providerBodyRedacted = true;
            }
            saveBytes(join(responses, row.id + '.json'), responseBytes);
            entry.responseSha256 = sha(responseBytes);
            payload = JSON.parse(responseBytes.toString('utf8'));
          } else {
            payload = await response.json();
          }
          entry.wallSeconds = (performance.now() - started) / 1000;
          if (!response.ok) {
            // Keep a conservative reservation on every error; do not assume
            // a malformed/missing receipt proves zero billing.
            entry.status = 'error'; entry.errorCode = payload.error?.code || 'API_ERROR';
            entry.errorType = payload.error?.type || null;
            // Keep support diagnostics privately, never whole bodies or keys.
            entry.errorMessage = String(payload.error?.message || '').replaceAll(secret, '[redacted]').slice(0, 700);
            if ([401, 403, 429].includes(response.status)) stopped = true;
          } else {
            if (!extension) save(join(responses, row.id + '.json'), payload);
            entry.responseSha256 = sha(readFileSync(join(responses, row.id + '.json')));
            entry.usage = payload.usage; entry.model = payload.model;
            if (plan.variant === EXTENSION_VARIANT && payload.model !== plan.expectedResponseModel) {
              // Preserve the paid response but never silently mix a new model
              // into the frozen condition or price an unknown model at GPT-4 rates.
              entry.status = 'error'; entry.errorCode = 'MODEL_IDENTITY_CHANGED'; stopped = true;
            } else {
              entry.actualUsd = usageCost(payload.usage);
              entry.status = 'complete';
            }
          }
        } catch {
          entry.status = 'uncertain'; entry.errorCode = 'NO_VERIFIABLE_COMPLETION';
          entry.wallSeconds = (performance.now() - started) / 1000;
        }
        if (extension) entry.finishedAt = new Date().toISOString();
        save(ledgerPath, ledger);
        if (extension) {
          try { await archive(row.id); }
          catch (error) { stopped = true; throw error; }
        }
        console.log(JSON.stringify({ id: row.id, status: entry.status, seconds: entry.wallSeconds,
                                    accountedUsd: accounted(ledger), budgetUsd: ledger.budgetUsd }));
      }
    }
    // Wait for every already-dispatched request before releasing the owner
    // lock, even when one worker's upload fails. Otherwise a resume could race
    // a still-live paid request from the previous controller.
    const results = await Promise.allSettled(Array.from({ length: workers }, worker));
    const failed = results.find(result => result.status === 'rejected');
    if (failed) throw failed.reason;
    console.log(JSON.stringify({ attempted: Object.keys(ledger.entries).length,
      complete: Object.values(ledger.entries).filter(row => row.status === 'complete').length,
      accountedUsd: accounted(ledger), budgetUsd: ledger.budgetUsd }));
  } finally { closeSync(lock); unlinkSync(lockPath); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  const retryCreditError = args.includes('--retry-credit-exhausted');
  const concurrency = args.find(arg => arg.startsWith('--workers='));
  const authorization = args.find(arg => arg.startsWith('--authorized-budget-usd='));
  const archivePythonArg = args.find(arg => arg.startsWith('--archive-python='));
  const archiveProfileArg = args.find(arg => arg.startsWith('--archive-profile='));
  const workers = workerCount(concurrency ? Number(concurrency.slice('--workers='.length)) : 3);
  const [folder, credentialFile, limit] = args.filter(arg => arg !== '--retry-credit-exhausted' &&
    arg !== '--preflight' && arg !== concurrency && arg !== authorization && arg !== archivePythonArg && arg !== archiveProfileArg);
  if (args.includes('--preflight')) {
    const plan = JSON.parse(readFileSync(join(resolve(folder), 'requests.json')));
    validatePlan(plan, limit === undefined ? plan.requests.length : Number(limit));
    console.log(JSON.stringify({ variant: plan.variant, requests: plan.requests.length,
      budgetUsd: plan.budgetUsd, maximumReservedUsd: plan.requests.length * RESERVATION_USD,
      inferenceStarted: false }));
  } else {
    await run(folder, credentialFile, limit === undefined ? null : Number(limit), retryCreditError, workers,
      authorization ? Number(authorization.slice('--authorized-budget-usd='.length)) : null,
      archivePythonArg ? archivePythonArg.slice('--archive-python='.length) : null,
      archiveProfileArg ? archiveProfileArg.slice('--archive-profile='.length) : 'darkest');
  }
}
