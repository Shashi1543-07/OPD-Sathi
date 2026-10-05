import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const localeRoot = fileURLToPath(new URL('../src/locales/', import.meta.url));
const languages = ['en', 'hi', 'mr'];
const readLocale = (language) => {
  const filename = path.join(localeRoot, language, 'common.json');
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
};

const baseline = Object.keys(readLocale('en')).sort();
let failed = false;
for (const language of languages.slice(1)) {
  const keys = Object.keys(readLocale(language)).sort();
  const missing = baseline.filter((key) => !keys.includes(key));
  const extra = keys.filter((key) => !baseline.includes(key));
  if (missing.length || extra.length) {
    console.error(`${language}: missing [${missing.join(', ')}], extra [${extra.join(', ')}]`);
    failed = true;
  }
}
if (failed) process.exit(1);
console.log(`Locale key parity: ${languages.join(', ')} — ${baseline.length} keys each.`);
