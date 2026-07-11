// DuckDock 前端 lint 门(specs/002 FR-005 Phase 2)。
// 取向:卡**真实问题**(未用变量/未定义/React Hooks 误用),不卡风格噪声——
// 与后端 ruff「先只卡 pyflakes」一致的渐进式策略。后续可逐步收紧。
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "coverage", "*.config.js", "*.config.ts", "e2e/**"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // 未用变量是真实 bug 类(对齐后端 ruff F);下划线前缀豁免
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // any 暂不卡(client.ts 等存量 any 多),留作后续逐步收紧,避免一次性大改
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    // 测试/setup 跑在 jsdom+node,放开 node 全局
    files: ["src/test/**", "**/*.test.{ts,tsx}"],
    languageOptions: { globals: { ...globals.node } },
  },
);
