// Neutral image packets for independent reviewers. Never expose protocol
// condition labels, private session credentials, or other reviewers' answers.
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';

const root = resolve(process.argv[2]);
const dependencyRoot = resolve(process.argv[3]);
const { chromium } = createRequire(join(dependencyRoot, 'package.json'))('playwright');
const browser = await chromium.launch({ headless: true });
const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const output = join(root, 'review-images');
await mkdir(output, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1480, height: 1140 }, deviceScaleFactor: 1 });
  for (let index = 1; index <= 10; index++) {
    const reviewer = `reviewer-${String(index).padStart(2, '0')}`;
    const folder = join(root, 'packets', reviewer);
    const cases = [];
    let prompt;
    for (const set of ['set-a', 'set-b']) {
      let raw;
      try { raw = await readFile(join(folder, `${set}.json`), 'utf8'); }
      catch (error) { if (error.code === 'ENOENT') continue; throw error; }
      const packet = JSON.parse(raw);
      if (prompt && prompt !== packet.prompt) throw new Error('Reviewer instructions differ across sets');
      prompt = packet.prompt;
      for (const item of packet.cases) {
        const key = createHash('sha256').update(JSON.stringify([item.title, item.leftImage, item.rightImage])).digest('hex');
        const destination = join(output, `${key}.png`);
        try { await access(destination); }
        catch {
          const urls = await Promise.all([item.leftImage, item.rightImage].map(async path => {
            if (!/^\/benchmarks\/stimuli\/[a-f0-9]+\.svg$/.test(path)) throw new Error('Unexpected stimulus path');
            return `data:image/svg+xml;base64,${(await readFile(join(root, 'site', path.slice(1)))).toString('base64')}`;
          }));
          await page.setContent(`<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#f2f4f6;color:#25384a;font:16px Arial}h1{font-size:20px;margin:10px;text-align:center}.pair{display:grid;grid-template-columns:720px 720px;gap:20px;padding:0 10px}figure{margin:0}figcaption{text-align:center;font-size:20px;font-weight:bold}img{display:block;width:720px;height:1080px}</style><h1>${escape(item.title)}</h1><div class="pair"><figure><figcaption>LEFT</figcaption><img src="${urls[0]}"></figure><figure><figcaption>RIGHT</figcaption><img src="${urls[1]}"></figure></div>`, { waitUntil: 'load' });
          await page.screenshot({ path: destination, fullPage: true });
        }
        cases.push({ set, caseId: item.caseId, title: item.title, image: destination });
      }
    }
    if (!cases.length || !prompt) throw new Error('No frozen reviewer cases');
    // Sessions already independently shuffle/balance each baseline. Interleave
    // their cases deterministically to avoid a visible block of one generator.
    cases.sort((a, b) => createHash('sha256').update(reviewer + a.caseId).digest('hex').localeCompare(createHash('sha256').update(reviewer + b.caseId).digest('hex')));
    await writeFile(join(folder, 'cases.json'), JSON.stringify(cases, null, 2));
    await writeFile(join(folder, 'prompt.txt'), `${prompt}\n`);
    console.log(JSON.stringify({ reviewer, cases: cases.length }));
  }
} finally { await browser.close(); }
