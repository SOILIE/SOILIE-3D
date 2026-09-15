import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { commitLevel, nextVersion as calculateNextVersion } from './versioning.mjs';

if (process.env.SOILIE3D_VERSION_BUMPING === '1' || process.env.SKIP_VERSION_BUMP === '1') process.exit(0);

const root = resolve(import.meta.dirname, '..');
const git = (args, options = {}) => {
  const output = execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    stdio: options.stdio || ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, SOILIE3D_VERSION_BUMPING: '1' },
  });
  return typeof output === 'string' ? output.trim() : '';
};
const message = git(['log', '-1', '--pretty=%B']);
const level = commitLevel(message);
if (!level) process.exit(0);

const packagePath = resolve(root, 'package.json');
const lockPath = resolve(root, 'package-lock.json');
const readmePath = resolve(root, 'README.md');
const infrastructurePath = resolve(root, 'serverless', 'infra', 'template.yaml');
const packageDocument = JSON.parse(readFileSync(packagePath, 'utf8'));
const nextVersion = calculateNextVersion(packageDocument.version, level);

packageDocument.version = nextVersion;
writeFileSync(packagePath, `${JSON.stringify(packageDocument, null, 2)}\n`);
const lockDocument = JSON.parse(readFileSync(lockPath, 'utf8'));
lockDocument.version = nextVersion;
if (lockDocument.packages?.['']) lockDocument.packages[''].version = nextVersion;
writeFileSync(lockPath, `${JSON.stringify(lockDocument, null, 2)}\n`);
const readme = readFileSync(readmePath, 'utf8').replace(
  /\*\*Current model version: [^*]+\*\*/,
  `**Current model version: ${nextVersion}**`,
);
writeFileSync(readmePath, readme);
const infrastructure = readFileSync(infrastructurePath, 'utf8').replace(
  /(ModelVersion:\r?\n\s+Type: String\r?\n\s+Default:)\s+[^\r\n]+/,
  `$1 ${nextVersion}`,
);
writeFileSync(infrastructurePath, infrastructure);

git(['add', 'package.json', 'package-lock.json', 'README.md', 'serverless/infra/template.yaml']);
git(['commit', '--amend', '--no-edit', '--no-verify'], { stdio: 'inherit' });
process.stdout.write(`SOILIE-3D version bumped to ${nextVersion}.\n`);
