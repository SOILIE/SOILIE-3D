// Each room is rasterized once. Paired packets only copy those pixels, so
// reversing LEFT/RIGHT cannot change SVG rasterization or text placement.
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';

const root = resolve(process.argv[2]);
const dependencyRoot = resolve(process.argv[3]);
const { chromium } = createRequire(join(dependencyRoot, 'package.json'))('playwright');
const hash = body => createHash('sha256').update(body).digest('hex');
const protocol = JSON.parse(await readFile(join(root, 'protocol.json'), 'utf8'));
await mkdir(join(root, 'panels'), { recursive: true });
await mkdir(join(root, 'packets'), { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 720, height: 1080 }, deviceScaleFactor: 1 });
  const panels = {};
  for (const [url, source] of Object.entries(protocol.sourceImages)) {
    const svg = await readFile(join(root, source.file));
    if (hash(svg) !== source.sha256) throw new Error('Changed source SVG');
    await page.setContent(`<style>html,body{margin:0;width:720px;height:1080px;background:#f2f4f6}img{display:block;width:720px;height:1080px}</style><img src="data:image/svg+xml;base64,${svg.toString('base64')}">`);
    await page.locator('img').evaluate(image => image.decode());
    const png = await page.screenshot();
    const file = `panels/${hash(png)}.png`;
    try { await writeFile(join(root, file), png, { flag: 'wx' }); }
    catch (error) { if (error.code !== 'EEXIST' || !png.equals(await readFile(join(root, file)))) throw error; }
    panels[url] = { file, sha256: hash(png), data: png.toString('base64') };
  }
  const index = {};
  for (const row of protocol.assignments) {
    const left = panels[row.leftSource], right = panels[row.rightSource];
    // Render labels separately; no case IDs, reviewer IDs or generator names
    // occur in the image. drawImage copies the 720x1080 pixels without scaling.
    const data = await page.evaluate(async ({ left, right, title }) => {
      const canvas = document.createElement('canvas');
      canvas.width = 1480; canvas.height = 1146;
      const context = canvas.getContext('2d');
      context.fillStyle = '#f2f4f6'; context.fillRect(0, 0, 1480, 1146);
      context.fillStyle = '#25384a'; context.font = 'bold 20px Arial'; context.textAlign = 'center';
      context.fillText(title, 740, 25); context.fillText('LEFT', 370, 53); context.fillText('RIGHT', 1110, 53);
      for (const [body, x] of [[left, 10], [right, 750]]) {
        const image = new Image(); image.src = `data:image/png;base64,${body}`;
        await image.decode(); context.drawImage(image, x, 66);
      }
      return canvas.toDataURL('image/png').split(',')[1];
    }, { left: left.data, right: right.data, title: row.title });
    const bytes = Buffer.from(data, 'base64');
    const file = `packets/${row.assignmentId}.png`;
    await writeFile(join(root, file), bytes, { flag: 'wx' });
    index[row.assignmentId] = { file, imageSha256: hash(bytes),
      leftPanel: left.file, rightPanel: right.file, leftPanelSha256: left.sha256, rightPanelSha256: right.sha256 };
  }
  await writeFile(join(root, 'packets/index.json'), JSON.stringify(index, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ panels: Object.keys(panels).length, packets: Object.keys(index).length }));
} finally { await browser.close(); }
