import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const lock = JSON.parse(await readFile("apps/web/package-lock.json", "utf8"));
const entries = Object.entries(lock.packages).filter(
  ([path, dependency]) => path.startsWith("node_modules/") && dependency.version,
);
const nameByPath = new Map();
const components = entries.map(([path, dependency]) => {
  const name = dependency.name ?? path.slice("node_modules/".length);
  const purlName = name.startsWith("@") ? `%40${name.slice(1)}` : name;
  const purl = `pkg:npm/${purlName}@${dependency.version}`;
  nameByPath.set(path, purl);
  return {
    type: "library",
    name,
    version: dependency.version,
    purl,
    "bom-ref": purl,
  };
});

const dependencies = entries.map(([path, dependency]) => {
  const dependsOn = Object.keys(dependency.dependencies ?? {})
    .map((name) => {
      const directPath = `${path}/node_modules/${name}`;
      if (nameByPath.has(directPath)) return nameByPath.get(directPath);
      const hoistedPath = `node_modules/${name}`;
      return nameByPath.get(hoistedPath);
    })
    .filter(Boolean);
  return { ref: nameByPath.get(path), dependsOn };
});

const bom = {
  bomFormat: "CycloneDX",
  specVersion: "1.5",
  serialNumber: `urn:uuid:${randomUUID()}`,
  version: 1,
  metadata: {
    timestamp: new Date().toISOString(),
    component: {
      type: "application",
      name: lock.name ?? "@opd-saathi/web",
      version: lock.version ?? "0.0.0",
    },
  },
  components,
  dependencies,
};

await writeFile("docs/security/web.cdx.json", `${JSON.stringify(bom, null, 2)}\n`);
console.log(`Generated web SBOM with ${components.length} locked packages.`);
