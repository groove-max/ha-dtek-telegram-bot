# Contributing

Thanks for contributing to DTEK Telegram Bot.

This project is a Home Assistant add-on focused on practical Telegram notifications for electricity monitoring, built around `ha-dtek-monitor` and local Home Assistant sensors.

## Before opening a PR

1. Make sure the change belongs in this repository.
2. If the issue is caused by missing or incorrect DTEK entities, check whether the problem actually belongs to `ha-dtek-monitor`.
3. If the change affects user-visible behavior, update documentation and templates as needed.

## Development expectations

Keep changes consistent with the current project direction:

- prefer pragmatic fixes over speculative abstractions
- preserve existing user-facing behavior unless the change is intentional
- keep Telegram message changes explicit and reviewable
- keep power-detection logic testable
- avoid regressions in ingress UI and import/export flows

## Local validation

Before opening a PR, run:

```bash
python -m unittest discover -s tests -v
python -m compileall rootfs/app tests
```

If you touched:

- GitHub Actions or YAML files: validate YAML syntax
- Dockerfile or shell scripts: check linting locally if possible
- ingress UI: test it in a running add-on, not only by static inspection

## Documentation

If your change affects:

- installation
- configuration
- power detection semantics
- Telegram message behavior
- import/export
- release or repository setup

then update the relevant documentation:

- `README.md`
- `README.uk.md`
- `DOCS.md`
- `CHANGELOG.md`

## Pull requests

Good PRs for this repository usually include:

- a clear problem statement
- a narrowly scoped implementation
- tests for runtime logic changes
- a short explanation of tradeoffs if behavior changed

## What not to do

- Do not bundle unrelated refactors with behavioral fixes.
- Do not silently change Telegram message semantics.
- Do not rely on DTEK-only assumptions if the add-on is explicitly combining DTEK and local power sensors.
- Do not commit `__pycache__` or generated `.pyc` files.

