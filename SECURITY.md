# Security Policy

## Supported versions

Only the latest public version of this repository is considered supported for security-related fixes.

## Reporting a vulnerability

If you believe you found a security issue, please do not open a public issue with exploit details.

Instead:

1. Contact the maintainer through GitHub security reporting if available for the repository.
2. If that is not available yet, open a minimal public issue only if the problem cannot expose secrets, tokens, or private infrastructure details.

## Scope

Relevant security topics for this project include:

- leaking Telegram bot tokens
- unsafe file/template handling
- ingress UI exposure beyond the intended Home Assistant environment
- path traversal or arbitrary file write/read through template or import/export features
- unsafe handling of Home Assistant auth tokens

## Out of scope

The following are usually not security issues in this repository by themselves:

- incorrect DTEK outage data from the upstream provider
- false-positive or false-negative electricity detection caused by bad sensor choices
- Telegram delivery delays caused by Telegram API rate limiting

