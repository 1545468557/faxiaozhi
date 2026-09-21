import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * 测试配置（独立于应用构建）
 *
 * 应用实际运行器是 vinext + Vite + Cloudflare Workers，它的 vite.config.ts 会拉起
 * Cloudflare 插件，不适合跑单测。所以单测走这份最小配置：只解析 `@/` 别名 + jsdom。
 */
export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname) },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    globals: true,
    reporters: "default",
  },
});
