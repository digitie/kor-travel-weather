import { defineConfig, globalIgnores } from "eslint/config";
import nextPlugin from "@next/eslint-plugin-next";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactX from "eslint-plugin-react-x";
import reactDom from "eslint-plugin-react-dom";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import importX from "eslint-plugin-import-x";
import { createTypeScriptImportResolver } from "eslint-import-resolver-typescript";

export default defineConfig([
  ...tseslint.configs.recommended,
  importX.flatConfigs.recommended,
  reactX.configs.recommended,
  reactDom.configs.recommended,
  reactHooks.configs.flat.recommended,
  nextPlugin.configs.recommended,
  nextPlugin.configs["core-web-vitals"],
  {
    plugins: { "jsx-a11y-x": jsxA11y },
    settings: { "import-x/resolver-next": [createTypeScriptImportResolver({ project: "./tsconfig.json" })] },
    rules: {
      "jsx-a11y-x/alt-text": "error",
      "jsx-a11y-x/aria-props": "error",
      "jsx-a11y-x/aria-proptypes": "error",
      "jsx-a11y-x/role-has-required-aria-props": "error",
      "react-hooks/immutability": "off",
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
      // Compiler는 별도 도입한다. 기존 Hooks 두 규칙은 활성화한다.
      "react-x/error-boundaries": "off",
      "react-x/exhaustive-deps": "off",
      "react-x/purity": "off",
      "react-x/refs": "off",
      "react-x/immutability": "off",
      "react-x/rules-of-hooks": "off",
      "react-x/set-state-in-effect": "off",
      "react-x/set-state-in-render": "off",
      "react-x/static-components": "off",
      "react-x/unsupported-syntax": "off",
      "react-x/use-memo": "off",
    },
  },
  globalIgnores([".next/**", "node_modules/**", "public/maplibre/**", "next-env.d.ts", "test-results/**", "playwright-report/**"]),
]);
