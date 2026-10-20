"""Validate an app release and extract its reviewed changelog notes."""

import argparse
import json
import os
from pathlib import Path
import re

TAG = re.compile(r"v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?P<pre>-(?:a|b|rc)[1-9]\d*)?)")


def metadata(root, tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Use vMAJOR.MINOR.PATCH, optionally followed by -rcN")
    version = match['version']
    configured = re.search(r'^version: "([^"\n]+)"$', (root/'config.yaml').read_text(), re.MULTILINE)
    if not configured or configured[1] != version:
        raise ValueError("Tag does not match config.yaml version")
    section = re.search(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\n(.*?)(?=^## \[|\Z)",
        (root/'CHANGELOG.md').read_text(), re.MULTILINE | re.DOTALL,
    )
    if not section or not section[1].strip():
        raise ValueError("Release requires a nonempty dated changelog entry")
    notes = re.sub(
        r"\]\((?![a-z]+:|#)([^)]+)\)",
        lambda link: f"](https://github.com/groove-max/ha-dtek-telegram-bot/blob/{tag}/{link[1]})",
        section[1].strip()+'\n',
    )
    prerelease = bool(match['pre'])
    policy = root / '.github/release-channel.json'
    if policy.exists():
        channel = json.loads(policy.read_text())
        if (
            not isinstance(channel, dict)
            or set(channel) != {'version', 'prerelease'}
            or channel['version'] != version
            or type(channel['prerelease']) is not bool
        ):
            raise ValueError('Release channel must explicitly match this version')
        prerelease = channel['prerelease']
    return {'version': version, 'prerelease': prerelease, 'notes': notes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag')
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    result = metadata(Path(__file__).resolve().parents[1], args.tag)
    if args.notes:
        args.notes.write_text(result['notes'])
    output = f"version={result['version']}\nprerelease={str(result['prerelease']).lower()}\n"
    if path := os.environ.get('GITHUB_OUTPUT'):
        with Path(path).open('a') as stream:
            stream.write(output)
    print(output, end='')


if __name__ == '__main__':
    main()
