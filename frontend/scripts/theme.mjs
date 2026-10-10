#!/usr/bin/env node
/**
 * 法小智前端主题色切换（可逆）
 *
 * 用法：
 *   node scripts/theme.mjs            列出所有主题
 *   node scripts/theme.mjs blue       切到藏青蓝
 *   node scripts/theme.mjs green      还原成原始绿色
 *
 * 原理
 * ----
 * 站点的绿色是**硬编码**在各 CSS 里的（同一族色值散在 11 个文件 + favicon，
 * 变量名还不统一：--fh-teal / --v2-teal / --teal）。所以这里不改变量名，
 * 而是把「绿色系」的十六进制整体做**色相旋转**：亮度、饱和度都保留，
 * 只换色相 —— 明暗层次和对比关系不会垮。
 *
 * 首次运行会把**原始文件**逐字节快照到 `.theme/original/`。
 * 之后每次切换都「先还原快照 → 再按目标主题重算」，因此：
 *   - 任何主题都能一键来回切，反复切换结果一致（幂等）
 *   - `green` 直接输出原始文件，与改动前逐字节相同
 *
 * 不处理的：注释里写死的颜色名（如「墨与玉」「teal」）属于原始设计的说明，
 * 以及红色/琥珀等语义色（色相不在绿色区间，不会被动到）。
 */

import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const SNAP = path.join(ROOT, ".theme", "original");

/** 原始主色 #0b766b 的色相，作为旋转基准 */
const BASE_HUE = 173.8;
/** 只旋转这个色相区间、且饱和度够高的颜色，避免动到灰阶和红色/琥珀 */
const HUE_MIN = 110;
const HUE_MAX = 210;
const SAT_MIN = 0.05;

const PALETTES = {
  green: { hue: 173.8, label: "原色 · 墨绿（默认）" },
  blue: { hue: 214, label: "藏青蓝" },
  indigo: { hue: 243, label: "靛紫" },
  violet: { hue: 277, label: "紫罗兰" },
  wine: { hue: 348, label: "酒红" },
  amber: { hue: 38, label: "琥珀金" },
};

// ---------------------------------------------------------------- 颜色换算
const hexToRgb = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const rgbToHex = (r, g, b) =>
  "#" +
  [r, g, b]
    .map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0"))
    .join("");

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  switch (max) {
    case r: h = (g - b) / d + (g < b ? 6 : 0); break;
    case g: h = (b - r) / d + 2; break;
    default: h = (r - g) / d + 4;
  }
  return [h * 60, s, l];
}

function hslToRgb(h, s, l) {
  h = (((h % 360) + 360) % 360) / 360;
  if (s === 0) { const v = l * 255; return [v, v, v]; }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const f = (t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
}

/** 6 位（#rrggbb）或 8 位（#rrggbbaa，带透明度）—— 后者常见于软阴影，必须一起转 */
const HEX = /#([0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})\b/g;

// ---------------------------------------------------------------- 重建快照
/**
 * 用「当前处于原始色」的状态重建快照。
 *
 * 什么时候需要：你**有意**改了基础设计（比如给导航加了新动效），
 * 希望这个改动成为所有主题的共同底子 —— 那就先 `theme.mjs green` 回到原始色，
 * 再跑 `theme.mjs resnap`。
 *
 * 为什么要有守卫：快照是一切主题的来源。若在蓝色状态下去重建，
 * 快照就会被染成蓝色，`green` 将再也回不到原来的绿色。
 */
function resnap() {
  const activePath = path.join(ROOT, ".theme", "active");
  const active = fs.existsSync(activePath)
    ? fs.readFileSync(activePath, "utf8").trim()
    : "";
  if (active !== "green") {
    console.error(`拒绝重建快照：当前主题是「${active || "未设置"}」，不是原始色。`);
    console.error("请先执行：node scripts/theme.mjs green");
    process.exit(1);
  }
  const files = collectFiles();
  for (const f of files) {
    const dst = path.join(SNAP, rel(f));
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(f, dst);
  }
  console.log(`已用当前（原始色）状态重建快照：${files.length} 个文件`);
}

/** 把单个色值按 delta 旋转；不属于绿色系则原样返回。8 位时保留 alpha 不变 */
function rotateHex(hex, delta) {
  const raw = hex.slice(1);
  const [rgb, alpha] = raw.length === 8 ? [raw.slice(0, 6), raw.slice(6)] : [raw, ""];
  const [r, g, b] = hexToRgb("#" + rgb);
  const [h, s, l] = rgbToHsl(r, g, b);
  if (!(h >= HUE_MIN && h <= HUE_MAX && s > SAT_MIN)) return hex;
  return rgbToHex(...hslToRgb(h + delta, s, l)) + alpha;
}

// ---------------------------------------------------------------- 文件集合
/** 整个前端：所有样式、组件里的内联色值、图标、静态页 —— 不只是 app/ 下那几个 CSS */
const SKIP_DIRS = new Set([
  "node_modules", ".next", ".vinext", "dist", "build", ".wrangler",
  ".git", ".theme", "tests", "__pycache__",
]);
const EXTS = [".css", ".tsx", ".ts", ".svg", ".html"];

function collectFiles() {
  const out = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) {
        if (SKIP_DIRS.has(e.name)) continue;
        walk(path.join(dir, e.name));
      } else if (EXTS.some((x) => e.name.endsWith(x))) {
        out.push(path.join(dir, e.name));
      }
    }
  };
  walk(ROOT);
  return out.sort();
}

const rel = (p) => path.relative(ROOT, p);

// ---------------------------------------------------------------- 快照
function ensureSnapshot() {
  const files = collectFiles();
  if (fs.existsSync(SNAP)) return files;
  for (const f of files) {
    const dst = path.join(SNAP, rel(f));
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(f, dst);
  }
  console.log(`已快照 ${files.length} 个原始文件到 ${rel(SNAP)}/`);
  return files;
}

// ---------------------------------------------------------------- 主流程
function list() {
  const active = fs.existsSync(path.join(ROOT, ".theme", "active"))
    ? fs.readFileSync(path.join(ROOT, ".theme", "active"), "utf8").trim()
    : "(未设置，当前应为原始色)";
  console.log("当前主题：" + active + "\n\n可用主题：");
  for (const [k, v] of Object.entries(PALETTES)) {
    console.log(`  ${k.padEnd(8)} ${v.label}${k === "green" ? "   ← 还原用这个" : ""}`);
  }
}

function apply(name) {
  const pal = PALETTES[name];
  if (!pal) { console.error(`没有这个主题：${name}`); list(); process.exit(1); }

  const files = ensureSnapshot();
  const delta = pal.hue - BASE_HUE;
  let total = 0;
  const changed = [];

  for (const f of files) {
    const src = path.join(SNAP, rel(f));
    const original = fs.readFileSync(src, "utf8");
    let out = original;

    if (delta !== 0) {
      // 跳过 /* ... */ 注释块，注释里的色值保持原样（那是原始设计的说明）
      const parts = original.split(/(\/\*[\s\S]*?\*\/)/);
      out = parts
        .map((part, i) => {
          if (i % 2 === 1) return part; // 注释
          return part.replace(HEX, (m) => {
            const nv = rotateHex(m.toLowerCase(), delta);
            if (nv !== m.toLowerCase()) total++;
            return nv;
          });
        })
        .join("");
    }

    if (out !== fs.readFileSync(f, "utf8")) {
      fs.writeFileSync(f, out);
      changed.push(rel(f));
    }
  }

  fs.mkdirSync(path.join(ROOT, ".theme"), { recursive: true });
  fs.writeFileSync(path.join(ROOT, ".theme", "active"), name + "\n");

  console.log(`已切到「${pal.label}」 — 改写 ${total} 处色值，涉及 ${changed.length} 个文件`);
  if (changed.length) changed.forEach((c) => console.log("   " + c));
  console.log(`\n还原：node scripts/theme.mjs green`);
}

const arg = process.argv[2];
if (!arg) list();
else if (arg === "resnap") resnap();
else apply(arg);
