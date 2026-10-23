import { describe, expect, it } from "vitest";

// Every screen string exists in English, Hindi and Marathi, keeps its placeholders, and has
// not been damaged by an encoding conversion (which turns Devanagari into "???"). Found in
// Phase 7: 21 staff strings per language had shipped as question marks.
const files = import.meta.glob<Record<string, unknown>>("./locales/*/*.json", {
  eager: true,
  import: "default",
});

type Flat = Record<string, string>;

function flatten(value: unknown, prefix = "", out: Flat = {}): Flat {
  if (typeof value === "string") {
    out[prefix] = value;
  } else if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, out);
    }
  }
  return out;
}

function load(language: string, namespace: string): Flat {
  const file = files[`./locales/${language}/${namespace}.json`];
  expect(file, `${language}/${namespace}.json exists`).toBeDefined();
  return flatten(file);
}

const namespaces = [
  ...new Set(
    Object.keys(files)
      .filter((path) => path.startsWith("./locales/en/"))
      .map((path) => path.replace("./locales/en/", "").replace(".json", "")),
  ),
];
const placeholders = (text: string) => [...text.matchAll(/\{\{\s*([\w.]+)[^}]*\}\}/g)].map((m) => m[1]).sort();

describe("translations", () => {
  it("finds the locale files", () => {
    expect(namespaces.length).toBeGreaterThanOrEqual(4);
  });

  for (const namespace of namespaces) {
    for (const language of ["hi", "mr"]) {
      it(`${language}/${namespace} has every English key, placeholder and readable text`, () => {
        const english = load("en", namespace);
        const translated = load(language, namespace);
        expect(Object.keys(translated).sort()).toEqual(Object.keys(english).sort());
        for (const [key, text] of Object.entries(translated)) {
          expect(text, `${language}.${namespace}.${key}`).not.toMatch(/\?{3,}/);
          expect(placeholders(text), `${language}.${namespace}.${key} placeholders`).toEqual(
            placeholders(english[key] ?? ""),
          );
        }
      });
    }
  }
});
