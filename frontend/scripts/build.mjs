import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryDir = resolve(frontendDir, "..");
const sourceDir = join(frontendDir, "src");
const webDir = join(repositoryDir, "src", "colorless", "web");
const checkOnly = process.argv.includes("--check");
const temporaryDir = await mkdtemp(join(tmpdir(), "colorless-frontend-"));

const outputs = [
  ["app/image-worker.js", "assets/js/image-worker.js"],
  ["image-worker-fixture.js", "assets/image-worker-fixture.js"],
  ["static-load-benchmark.js", "assets/static-load-benchmark.js"],
  ["app/signup.js", "assets/js/signup.js"],
];

const fingerprint = (content) => createHash("sha256").update(content).digest("hex").slice(0, 12);
const temporaryOutput = (webPath) => join(temporaryDir, webPath);
const productionOutput = (webPath) => join(webDir, webPath);

async function bundle(entry, webPath, define = {}) {
  const outfile = temporaryOutput(webPath);
  await mkdir(dirname(outfile), { recursive: true });
  await build({
    entryPoints: [join(sourceDir, entry)],
    outfile,
    bundle: true,
    minify: true,
    format: "iife",
    platform: "browser",
    target: ["es2022"],
    charset: "utf8",
    legalComments: "none",
    define,
  });
  return readFile(outfile);
}

function replaceFingerprint(html, expression, replacement) {
  const updated = html.replace(expression, replacement);
  if (updated === html && !html.includes(replacement)) {
    throw new Error(`Could not update HTML entrypoint: ${replacement}`);
  }
  return updated;
}

async function expectedHtml(hashes) {
  const pages = new Map();
  const indexPath = join(webDir, "index.html");
  const signupPath = join(webDir, "signup.html");
  const imageBenchmarkPath = join(webDir, "assets", "image-worker-benchmark.html");
  const staticBenchmarkPath = join(webDir, "assets", "static-load-benchmark.html");

  let index = await readFile(indexPath, "utf8");
  index = replaceFingerprint(
    index,
    /<script (?:type="module" |defer )?src="assets\/js\/(?:entrypoints\/main|main)\.js\?v=[0-9a-f]{12}"><\/script>/,
    `<script defer src="assets/js/main.js?v=${hashes.get("assets/js/main.js")}"></script>`,
  );
  pages.set(indexPath, index);

  let signup = await readFile(signupPath, "utf8");
  signup = replaceFingerprint(
    signup,
    /<script (?:type="module" |defer )?src="\/assets\/js\/signup\.js\?v=[0-9a-f]{12}"><\/script>/,
    `<script defer src="/assets/js/signup.js?v=${hashes.get("assets/js/signup.js")}"></script>`,
  );
  pages.set(signupPath, signup);

  let imageBenchmark = await readFile(imageBenchmarkPath, "utf8");
  imageBenchmark = replaceFingerprint(
    imageBenchmark,
    /<script (?:type="module" |defer )?src="image-worker-benchmark\.js\?v=[0-9a-f]{12}"><\/script>/,
    `<script defer src="image-worker-benchmark.js?v=${hashes.get("assets/image-worker-benchmark.js")}"></script>`,
  );
  pages.set(imageBenchmarkPath, imageBenchmark);

  let staticBenchmark = await readFile(staticBenchmarkPath, "utf8");
  staticBenchmark = replaceFingerprint(
    staticBenchmark,
    /<script defer src="static-load-benchmark\.js\?v=[0-9a-f]{12}"><\/script>/,
    `<script defer src="static-load-benchmark.js?v=${hashes.get("assets/static-load-benchmark.js")}"></script>`,
  );
  pages.set(staticBenchmarkPath, staticBenchmark);
  return pages;
}

try {
  const generated = new Map();
  for (const [entry, webPath] of outputs) {
    generated.set(webPath, await bundle(entry, webPath));
  }

  const workerHash = fingerprint(generated.get("assets/js/image-worker.js"));
  const fixtureHash = fingerprint(generated.get("assets/image-worker-fixture.js"));
  const workerDefines = {
    COLORLESS_IMAGE_WORKER_URL: JSON.stringify(`/assets/js/image-worker.js?v=${workerHash}`),
  };
  generated.set("assets/js/main.js", await bundle("app/entrypoints/main.js", "assets/js/main.js", workerDefines));
  generated.set(
    "assets/image-worker-benchmark.js",
    await bundle("image-worker-benchmark.js", "assets/image-worker-benchmark.js", {
      ...workerDefines,
      COLORLESS_FIXTURE_WORKER_URL: JSON.stringify(`/assets/image-worker-fixture.js?v=${fixtureHash}`),
    }),
  );

  const hashes = new Map([...generated].map(([webPath, content]) => [webPath, fingerprint(content)]));
  const pages = await expectedHtml(hashes);
  const stale = [];

  if (checkOnly) {
    for (const [webPath, expected] of generated) {
      const target = productionOutput(webPath);
      const actual = await readFile(target).catch(() => null);
      if (!actual?.equals(expected)) stale.push(relative(repositoryDir, target));
    }
    for (const [target, expected] of pages) {
      const actual = await readFile(target, "utf8");
      if (actual !== expected) stale.push(relative(repositoryDir, target));
    }
    if (stale.length) {
      throw new Error(`Frontend build is stale. Run npm run build:\n${stale.map((path) => `- ${path}`).join("\n")}`);
    }
  } else {
    for (const [webPath, content] of generated) {
      const target = productionOutput(webPath);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, content);
    }
    for (const [target, content] of pages) await writeFile(target, content, "utf8");
  }

  const totalBytes = [...generated.values()].reduce((total, content) => total + content.length, 0);
  console.log(`${checkOnly ? "Verified" : "Built"} ${generated.size} frontend files (${totalBytes} bytes).`);
} finally {
  await rm(temporaryDir, { recursive: true, force: true });
}
