// Read-only browser audit of frozen numbered stimuli. No reviewer calls.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve, join } from 'node:path';

const root = resolve(process.argv[2]);
const dependencyRoot = resolve(process.argv[3]);
const { chromium } = createRequire(join(dependencyRoot, 'package.json'))('playwright');
const protocol = JSON.parse(await readFile(join(root, 'protocol.json'), 'utf8'));
assert.equal(protocol.rubricVersion, 'functional-use-v2');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1200 } });
  for (const [url, source] of Object.entries(protocol.sourceImages)) {
    const svg = await readFile(join(root, source.file), 'utf8');
    await page.setContent(`<style>body{margin:0}svg{width:720px;height:1080px}</style>${svg}`);
    const observed = await page.locator('svg').evaluate(node => {
      const keys = [...node.querySelectorAll('[data-key]')].map(key => ({
        id: key.dataset.key, text: key.textContent, width: key.getBBox().width,
        fontSize: parseFloat(getComputedStyle(key).fontSize),
      }));
      const markers = [...node.querySelectorAll('[data-object-ref]')].map(group => {
        const circle = group.querySelector('circle:last-of-type');
        return { id: group.dataset.objectRef, x: circle.cx.baseVal.value, y: circle.cy.baseVal.value };
      });
      return { keys, markers, objects: [...node.querySelectorAll('[data-object]')].map(group => ({
        id: group.dataset.object, fronts: group.querySelectorAll('.front').length,
      })) };
    });
    const inventory = protocol.inventories[url].objects;
    assert.equal(observed.keys.length, inventory.length);
    for (const item of inventory) {
      const key = observed.keys.find(value => value.id === item.id);
      assert.equal(key.text, `${item.id}. ${item.category}`);
      assert.ok(key.width < 224 && key.fontSize >= 14, 'Category key must fit its column');
      assert.equal(observed.markers.filter(value => value.id === item.id).length, 3);
      const objects = observed.objects.filter(value => value.id === item.id);
      assert.equal(objects.length, 3);
      assert.ok(objects.every(value => value.fronts === (item.front ? 1 : 0)));
    }
    for (const [index, marker] of observed.markers.entries()) {
      assert.ok(marker.x >= 11 && marker.x <= 709 && marker.y >= 11 && marker.y <= 1069);
      for (const other of observed.markers.slice(index + 1)) {
        assert.ok(Math.hypot(marker.x - other.x, marker.y - other.y) >= 22,
          'Number circles must not overlap');
      }
    }
  }
  // The site's responsive previews link to full-resolution diagrams. On small
  // screens the preview fits; its link retains the resolution-independent SVG
  // that can be panned/zoomed rather than squeezing the key to tiny text.
  const source = Object.values(protocol.sourceImages)[0];
  const svg = await readFile(join(root, source.file));
  const data = `data:image/svg+xml;base64,${svg.toString('base64')}`;
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 1200 });
    await page.setContent(`<style>body{margin:16px}a{display:block;max-width:720px}img{display:block;width:100%;height:auto}</style><a href="${data}" aria-label="Open full-size numbered diagram"><img alt="Numbered room with three views and category key" src="${data}"></a>`);
    await page.locator('img').evaluate(image => image.decode());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.keyboard.press('Tab');
    assert.equal(await page.locator(':focus').getAttribute('aria-label'), 'Open full-size numbered diagram');
    assert.equal(await page.locator('img').evaluate(image => image.naturalWidth / image.naturalHeight), 2 / 3);
    assert.equal(await page.locator('a').getAttribute('href'), data);
  }
  console.log(JSON.stringify({ panels: Object.keys(protocol.sourceImages).length,
    mappings: 'passed', nonoverlappingNumberKeys: 'passed', desktopMobilePreviews: 'passed' }));
} finally { await browser.close(); }
