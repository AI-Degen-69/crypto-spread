# Quality Guardrails & Constraints — Issue #392

## Scope & Functional Boundaries
- Make Conventional Commits the single canonical PR-title rule across all repo-side files: `.coderabbit.yaml`, `docs/git-workflow.md`, and `docs/issue-workflow.md`.
- `.coderabbit.yaml`:
  - Rewrite `reviews.auto_title_instructions` to generate Conventional Commits PR titles: `<type>(<scope>): <imperative summary> (#<issue>)`.
  - Rewrite `reviews.pre_merge_checks.title.requirements` to enforce Conventional Commits referencing the issue number (`#<issue>`).
  - Allow types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `perf`, `ci`, `style`, `revert`.
  - Keep `mode: error` and `request_changes_workflow: true`.
  - Keep `custom_checks` (with `No Hardcoded Secrets`) intact in the same `pre_merge_checks` mapping without duplicate keys.
  - Do NOT touch `path_filters`, `path_instructions`, `tools`, or other sections.
- `docs/git-workflow.md`:
  - Update §3 `PR title:` bullet to clearly describe the canonical format, state that `.coderabbit.yaml` enforces it at `mode: error`, and provide one real worked example from history.
- `docs/issue-workflow.md`:
  - Ensure references to PR title point to `docs/git-workflow.md` §3 as canonical.

## Anti-Regression & Verification
- Verify valid YAML syntax in `.coderabbit.yaml` using `python -c "import yaml; yaml.safe_load(open('.coderabbit.yaml'))"`.
- Verify no remaining `[TAG]` requirements in `.coderabbit.yaml` or `docs/`.
- No new runtime dependencies in `requirements.txt`.
- No full test suite execution locally per project AGENTS.md policy.
