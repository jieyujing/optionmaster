import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
await mkdir("dist/assets",{recursive:true});
await build({entryPoints:["src/main.tsx"],bundle:true,minify:true,format:"esm",target:"es2022",outfile:"dist/assets/app.js",legalComments:"none",define:{"process.env.NODE_ENV":'"production"'}});
await copyFile("index.html","dist/index.html");
console.log("Built static React research desk");
