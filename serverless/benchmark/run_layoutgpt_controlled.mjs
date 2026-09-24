// Explicitly budgeted GPT-4 calls. This runner never automatically retries an
// ambiguous transport failure: its maximum possible charge stays reserved.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync, openSync, closeSync, unlinkSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

export const RATE = { input: 30 / 1e6, output: 60 / 1e6 };
// GPT-4's full 8,192-token context is reserved at input rates, plus its
// maximum 1,024 output tokens at output rates. This intentionally double-
// counts reserved output context and covers tokenizer-estimate differences.
export const RESERVATION_USD = 8192 * RATE.input + 1024 * RATE.output;
export function accounted(ledger) {
  return Object.values(ledger.entries).reduce((sum, row) => sum + (row.actualUsd ?? row.reservedUsd), ledger.previousBatchUsd || 0);
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
const save = (path, value) => {
  const temp = path + '.new'; writeFileSync(temp, JSON.stringify(value, null, 2)); renameSync(temp, path);
};

export async function run(folder, credentialFile, limit = null) {
  folder = resolve(folder);
  const raw = readFileSync(join(folder, 'requests.json'));
  const plan = JSON.parse(raw);
  limit ??= plan.requests.length;
  if (plan.budgetUsd !== 35 || plan.requests.length !== (plan.previousBatch ? 1 : 120) ||
      !Number.isInteger(limit) || limit < 1 || limit > plan.requests.length) {
    throw new Error('Expected the approved US$35 plan or its one-call supplement');
  }
  let previousBatchUsd = 0;
  if (plan.previousBatch) {
    // Windows invokes this runner; the preparation step may use WSL paths.
    const previousFolder = plan.previousBatch.folder.replace(/^\/mnt\/([a-z])\//i, (_, drive) => drive.toUpperCase() + ':/');
    const previousRaw = readFileSync(join(previousFolder, 'inference-ledger.json'));
    const previous = JSON.parse(previousRaw);
    if (sha(previousRaw) !== plan.previousBatch.ledgerSha256 || previous.planSha256 !== plan.previousBatch.planSha256 ||
        Object.keys(previous.entries).length !== 120 || Object.values(previous.entries).some(row => row.status !== 'complete')) {
      throw new Error('Previous batch changed or contains unsettled spending');
    }
    previousBatchUsd = accounted(previous);
    if (Math.abs(previousBatchUsd - plan.previousBatch.accountedUsd) > 1e-9) throw new Error('Previous spending differs');
  }
  for (const row of plan.requests) {
    const request = row.request;
    if (request.model !== 'gpt-4' || request.max_tokens !== 1024 || request.n !== 1 ||
        !Array.isArray(request.messages) || row.estimatedInputTokens + 1024 > 8192) throw new Error('Unexpected inference configuration');
  }
  const lockPath = join(folder, 'inference.lock');
  const lock = openSync(lockPath, 'wx');
  writeFileSync(lock, JSON.stringify({ pid: process.pid }));
  try {
    const ledgerPath = join(folder, 'inference-ledger.json');
    const ledger = existsSync(ledgerPath) ? JSON.parse(readFileSync(ledgerPath)) : {
      schemaVersion: 1, planSha256: sha(raw), budgetUsd: 35, previousBatchUsd, rate: RATE, entries: {},
    };
    if (ledger.planSha256 !== sha(raw) || ledger.budgetUsd !== 35 || (ledger.previousBatchUsd || 0) !== previousBatchUsd) throw new Error('Resume plan or budget changed');
    // Credential material is used only in the authorization header. Neither
    // the path nor its contents is copied into publication or invocation logs.
    const secret = process.env.OPENAI_API_KEY || (credentialFile
      ? JSON.parse(readFileSync(credentialFile, 'utf8')).OpenAI?.api_key : null);
    if (!secret) throw new Error('An OpenAI API key is required');
    const responses = join(folder, 'responses'); mkdirSync(responses, { recursive: true });
    let cursor = 0, stopped = false;
    const jobs = plan.requests.slice(0, limit);
    async function worker() {
      while (cursor < jobs.length && !stopped) {
        const row = jobs[cursor++];
        if (ledger.entries[row.id]) continue; // Completed AND uncertain requests never duplicate.
        if (!canReserve(ledger)) { stopped = true; break; }
        const entry = { status: 'reserved', reservedUsd: RESERVATION_USD, requestSha256: row.requestSha256 };
        ledger.entries[row.id] = entry;
        save(ledgerPath, ledger); // Synchronous reservation before any network side effect.
        const started = performance.now();
        try {
          const response = await fetch('https://api.openai.com/v1/chat/completions', {
            method: 'POST', headers: { Authorization: 'Bearer ' + secret, 'Content-Type': 'application/json' },
            body: JSON.stringify(row.request), signal: AbortSignal.timeout(180000),
          });
          const payload = await response.json();
          entry.wallSeconds = (performance.now() - started) / 1000;
          entry.httpStatus = response.status;
          if (!response.ok) {
            // Keep a conservative reservation on every error; do not assume
            // a malformed/missing receipt proves zero billing.
            entry.status = 'error'; entry.errorCode = payload.error?.code || 'API_ERROR';
            if ([401, 403, 429].includes(response.status)) stopped = true;
          } else {
            save(join(responses, row.id + '.json'), payload);
            entry.actualUsd = usageCost(payload.usage);
            entry.usage = payload.usage; entry.model = payload.model;
            entry.responseSha256 = sha(readFileSync(join(responses, row.id + '.json')));
            entry.status = 'complete';
          }
        } catch {
          entry.status = 'uncertain'; entry.errorCode = 'NO_VERIFIABLE_COMPLETION';
          entry.wallSeconds = (performance.now() - started) / 1000;
        }
        save(ledgerPath, ledger);
        console.log(JSON.stringify({ id: row.id, status: entry.status, seconds: entry.wallSeconds,
                                    accountedUsd: accounted(ledger), budgetUsd: ledger.budgetUsd }));
      }
    }
    await Promise.all(Array.from({ length: 3 }, worker));
    console.log(JSON.stringify({ attempted: Object.keys(ledger.entries).length,
      complete: Object.values(ledger.entries).filter(row => row.status === 'complete').length,
      accountedUsd: accounted(ledger), budgetUsd: 35 }));
  } finally { closeSync(lock); unlinkSync(lockPath); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [folder, credentialFile, limit] = process.argv.slice(2);
  await run(folder, credentialFile, limit === undefined ? null : Number(limit));
}
