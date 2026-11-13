// ESLint: TypeScript's recommended rules, React hooks (rules of hooks, effect
// dependencies) and accessibility (jsx-a11y). `make check` runs it.
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, jsxA11y.flatConfigs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // autoFocus is used only on a field that appears after a click (an inline form),
      // where moving the focus to it is what a keyboard user needs; never on page load.
      "jsx-a11y/no-autofocus": "off",
    },
  },
  {
    // Untitled UI's MIT source (src/ui/untitled, kept as upstream wrote it so updates stay a copy):
    // a badge's image has alt text that says "image".
    files: ["src/ui/untitled/**/*.{ts,tsx}"],
    rules: {
      "jsx-a11y/img-redundant-alt": "off",
    },
  },
);
