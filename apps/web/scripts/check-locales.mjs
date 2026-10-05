import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const localeRoot = fileURLToPath(new URL("../src/locales/", import.meta.url));
const languages = ["en", "hi", "mr"];
const namespaces = ["common", "kiosk", "staff", "status"];
const flatten = (object, prefix = "") =>
  Object.entries(object).flatMap(([key, value]) => {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    return value && typeof value === "object" && !Array.isArray(value)
      ? flatten(value, fullKey)
      : [fullKey];
  });
const readLocale = (language, namespace) => {
  const filename = path.join(localeRoot, language, `${namespace}.json`);
  return JSON.parse(fs.readFileSync(filename, "utf8"));
};

let failed = false;
let total = 0;
for (const namespace of namespaces) {
  const baseline = flatten(readLocale("en", namespace)).sort();
  total += baseline.length;
  for (const language of languages.slice(1)) {
    const keys = flatten(readLocale(language, namespace)).sort();
    const missing = baseline.filter((key) => !keys.includes(key));
    const extra = keys.filter((key) => !baseline.includes(key));
    if (missing.length || extra.length) {
      console.error(
        `${language}/${namespace}: missing [${missing.join(", ")}], extra [${extra.join(", ")}]`,
      );
      failed = true;
    }
  }
}
if (failed) process.exit(1);
console.log(
  `Locale key parity: ${languages.join(", ")} across ${namespaces.length} namespaces (${total} keys per language).`,
);
