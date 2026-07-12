import tseslint from "typescript-eslint";
import pluginVue from "eslint-plugin-vue";

// Lean lint: correctness rules only (unused code, undefined template refs,
// suspicious TS). Formatting is left to the editor — no stylistic tiers.
export default tseslint.config(
  { ignores: ["**/dist/**", "**/node_modules/**", "tmp/**", "vendor/**"] },
  ...tseslint.configs.recommended,
  ...pluginVue.configs["flat/essential"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // Non-null assertions are used deliberately at WebGL setup boundaries.
      "@typescript-eslint/no-non-null-assertion": "off",
      "vue/multi-word-component-names": "off",
    },
  },
);
