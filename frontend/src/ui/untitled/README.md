# Untitled UI React (vendored)

MIT-licensed components from https://github.com/untitleduico/react (see `LICENSE`),
copied as source. The commit they come from is in `UPSTREAM`. Plan:
`docs/plan-untitled-ui.md`.

- **Only the open-source (MIT) components** go here, never PRO ones: this
  repository may become public.
- **Copied unchanged** except one rewrite, so the folder can sit next to ours:
  `"@/components/…"`, `"@/utils/…"`, `"@/hooks/…"` became
  `"@/ui/untitled/components/…"` and so on. To add or update a component,
  copy it from upstream and apply the same rewrite.
- `styles/theme.css` is upstream's token file; `src/styles/index.css` imports it.
- ESLint turns off one jsx-a11y rule (`img-redundant-alt`, for the badge)
  for this folder only (see `eslint.config.js`).
- Only what our code imports is kept. To use another component, copy it
  from the commit in `UPSTREAM` with the same rewrite.
- `styles/theme.css` (about 830 lines) and some components are longer than
  the project's 500-line rule; they are upstream files, kept whole so they
  can be updated by copying.
