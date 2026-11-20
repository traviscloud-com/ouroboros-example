#!/usr/bin/env node
/**
 * Bundle budget (task 4.14): gzip-size the entry chunk index.html loads and fail the
 * build when it exceeds the hard limit. Run by `npm run build` and CI.
 *
 *   node scripts/check-bundle.mjs [--dist dist] [--max-kb 400] [--target-kb 250]
 *
 * The "initial chunk" is every <script type="module" src> plus every
 * <link rel="modulepreload"> in dist/index.html (what the browser fetches before the
 * first route renders), summed. Lazy route chunks are listed for information only.
 */
import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).map((a, i, all) => (a.startsWith("--") ? [a.slice(2), all[i + 1]] : [])).filter((x) => x.length),
);
const dist = resolve(args.dist ?? "dist");
const maxKb = Number(args["max-kb"] ?? 400);
const targetKb = Number(args["target-kb"] ?? 250);

const html = readFileSync(join(dist, "index.html"), "utf8");
const initial = new Set();
for (const m of html.matchAll(/<script[^>]+type="module"[^>]+src="([^"]+)"/g)) initial.add(m[1]);
for (const m of html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+)"/g)) initial.add(m[1]);
for (const m of html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)) initial.add(m[1]);

const gz = (file) => gzipSync(readFileSync(file), { level: 9 }).length;
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

const rows = [];
let initialJs = 0;
let initialCss = 0;
for (const href of initial) {
  const file = join(dist, href.replace(/^\//, ""));
  const size = gz(file);
  if (href.endsWith(".css")) initialCss += size;
  else initialJs += size;
  rows.push({ chunk: href, raw: kb(statSync(file).size), gzip: kb(size), initial: true });
}

const assets = join(dist, "assets");
let totalJs = 0;
let totalAll = 0;
for (const name of readdirSync(assets).sort()) {
  const file = join(assets, name);
  const size = gz(file);
  totalAll += size;
  if (name.endsWith(".js")) totalJs += size;
  if (!initial.has(`/assets/${name}`)) rows.push({ chunk: `/assets/${name}`, raw: kb(statSync(file).size), gzip: kb(size), initial: false });
}

console.table(rows);
console.log(`initial JS (gzip): ${kb(initialJs)}  initial CSS (gzip): ${kb(initialCss)}  all JS (gzip): ${kb(totalJs)}  all assets (gzip): ${kb(totalAll)}`);
console.log(`budget: hard limit ${maxKb} KB, target ${targetKb} KB for the initial JS`);

if (initialJs > maxKb * 1024) {
  console.error(`FAIL: initial JS ${kb(initialJs)} gzipped exceeds the ${maxKb} KB budget`);
  process.exit(1);
}
if (initialJs > targetKb * 1024) console.warn(`WARN: initial JS ${kb(initialJs)} is above the ${targetKb} KB target`);
else console.log(`OK: initial JS within target`);
