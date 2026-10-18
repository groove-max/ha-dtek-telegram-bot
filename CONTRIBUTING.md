# Contributing to DTEK Telegram Bot

**English** · [Українська](CONTRIBUTING.uk.md) · [User guide](README.md)

Bug reports, fixes and documentation improvements are welcome in English or Ukrainian.

## Choose the right repository

DTEK website parsing, regional address lookup and HA entities belong to [DTEK Monitor](https://github.com/groove-max/ha-dtek-monitor). Telegram delivery, message templates and local-sensor power detection belong to [DTEK Telegram Bot](https://github.com/groove-max/ha-dtek-telegram-bot).

Before opening an issue, search existing issues and include versions, reproduction steps, expected/actual behavior and a short sanitized log. Do not publish bot tokens, HA tokens, exact private addresses or raw production journals. For vulnerabilities, use [Security](SECURITY.md).

## Run the checks

From the repository root, with the indicated Python version installed:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -c requirements.lock
python -c "import aiogram, jinja2"
python -m unittest discover -s tests -v
python -m compileall rootfs/app tests
```

CI tests Python 3.12 and 3.13. Also build the shipped Dockerfile for runtime changes. Unit tests must use the real runtime dependencies, including Jinja rendering and aiogram error types.

`requirements.txt` sets supported ranges; `requirements.lock` pins the versions used by both CI and the shipped image. Review dependency PRs against both files. Raising a lower bound to an already pinned version does not change installed packages. When changing the pinned versions, check dependency resolution, `python -m pip check`, unit/browser tests and both architecture builds.

For sensor changes, cover fresh, stale, unavailable and restored readings; both orders of independent/voltage events; repeated observations; reconnect/restart; and failed delivery. For schedule changes, cover multiple/hidden groups, changing snapshots, missing publication coverage and retry relevance. UI changes need an ingress smoke check.


For UI changes, run the isolated Chromium tests as well:

```bash
python -m pip install -r requirements-ui.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s tests/browser -v
```

These tests serve the actual UI assets with a simulated API; they do not contact HA or Telegram. They cover delayed responses, concurrent edits, template drafts, keyboard controls and narrow layouts. CI runs them separately from the unit suite.


## Test against a live installation

Use a separate HA/test destination and keep production notification delivery disabled during experiments. Never toggle electrical equipment as an incidental software test. Document which scenarios used fixtures and which were actually observed.

## Documentation and pull requests

- Keep `README.md` and `README.uk.md` aligned; detailed guides also have `.uk.md` counterparts.
- Keep the README focused on installing and using the project. Put contracts and implementation detail in the reference/contributor guides.
- Validate relative links and YAML examples. Use fictional addresses and placeholder entity IDs.
- Update the changelog for user-visible behavior changes; historical release entries stay in their original language.
- Explain the problem, resulting behavior and meaningful checks. Preserve existing user settings and identifiers unless a documented migration is necessary.
- Do not commit credentials, private diagnostics, caches, virtual environments or `graphify-out/` artifacts.

Contributions must be your own work or compatible with this project’s [license](LICENSE). Submit contributions under PolyForm Noncommercial 1.0.0 and preserve third-party notices.
