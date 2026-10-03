// Self-host MediaPipe's WASM runtime so proctoring never depends on a CDN version match.
import { cpSync, existsSync, mkdirSync } from "node:fs";

const src = "node_modules/@mediapipe/tasks-vision/wasm";
const dest = "public/mediapipe";
if (!existsSync(src)) {
  console.error("MediaPipe not installed - run npm install");
  process.exit(1);
}
mkdirSync(dest, { recursive: true });
cpSync(src, dest, { recursive: true });
console.log("Copied MediaPipe wasm -> public/mediapipe");
