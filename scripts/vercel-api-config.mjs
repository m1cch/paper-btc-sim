/**
 * Пишет dashboard/api-config.js из переменной API_BASE_URL (Vercel → Project Settings → Environment Variables).
 * Пример: https://your-app.onrender.com (без завершающего слэша)
 */
import { writeFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const base = (process.env.API_BASE_URL ?? "").trim().replace(/\/$/, "");
const content = `window.__API_BASE__ = ${JSON.stringify(base)};\n`;
writeFileSync(join(root, "dashboard", "api-config.js"), content, "utf8");
console.log("dashboard/api-config.js:", base ? `API_BASE_URL (${base.length} chars)` : "empty (same-origin)");
