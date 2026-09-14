export function nextVersion(current, level) {
  const parts = current.split('.').map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part) || part < 0)) {
    throw new Error(`Unsupported version ${current}`);
  }
  if (level === 'major') return `${parts[0] + 1}.0.0`;
  if (level === 'minor') return `${parts[0]}.${parts[1] + 1}.0`;
  if (level === 'patch') return `${parts[0]}.${parts[1]}.${parts[2] + 1}`;
  throw new Error(`Unsupported bump level ${level}`);
}

export function commitLevel(message) {
  return message.match(/^\s*(patch|minor|major):/i)?.[1].toLowerCase() || null;
}
