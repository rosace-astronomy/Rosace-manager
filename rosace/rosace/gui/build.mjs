/**
 * build.mjs — Custom esbuild script for the Rosace GUI.
 *
 * Problem: esbuild skips tsconfig.json for files inside node_modules,
 * so TypeScript decorators (@class_ns, etc.) used by x4js are left
 * as raw "@" characters, causing a SyntaxError in browsers.
 *
 * Solution: an esbuild plugin intercepts every .ts file (including
 * node_modules) and compiles it with TypeScript's own transpileModule()
 * API, which correctly applies experimentalDecorators.
 * esbuild then bundles the resulting JavaScript.
 */

import * as esbuild from "esbuild";
import * as ts    from "typescript";
import { readFileSync } from "fs";

const isWatch = process.argv.includes("--watch");

const tsPlugin = {
  name: "ts-with-decorators",
  setup(build) {
    build.onLoad({ filter: /\.ts$/ }, (args) => {
      const source = readFileSync(args.path, "utf8");

      const result = ts.transpileModule(source, {
        fileName: args.path,
        compilerOptions: {
          experimentalDecorators: true,
          target:                 ts.ScriptTarget.ES2020,
          module:                 ts.ModuleKind.ESNext,
          useDefineForClassFields: false,
          emitDecoratorMetadata:  false,
          sourceMap:              false,
        },
      });

      return { contents: result.outputText, loader: "js" };
    });
  },
};

const config = {
  entryPoints: ["src/main.ts"],
  bundle:      true,
  outfile:     "bundle.js",
  minify:      !isWatch,
  keepNames:   true,
  sourcemap:   true,
  loader: {
    ".scss": "empty",
    ".svg":  "dataurl",
  },
  plugins: [tsPlugin],
};

if (isWatch) {
  const ctx = await esbuild.context(config);
  await ctx.watch();
  console.log("Watching for changes…");
} else {
  await esbuild.build(config);
  console.log("Build complete.");
}
