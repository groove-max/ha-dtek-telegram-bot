"""Require deliberate release channels and version-scoped changelog notes."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('bot_release_tools', Path(__file__).resolve().parents[1]/'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def prepare(self, version):
        (self.root/'config.yaml').write_text(f'version: "{version}"\n')
        (self.root/'CHANGELOG.md').write_text(f'# Changelog\n\n## [{version}] - 2026-10-20\n\nCurrent notes.\n\n## [0.4.1] - 2026-10-19\nOld notes.\n')

    def test_stable_and_prerelease_notes_are_scoped(self):
        for version, pre in [('0.5.0-rc1', True), ('0.5.0', False)]:
            with self.subTest(version=version):
                self.prepare(version)
                result = release.metadata(self.root, 'v'+version)
                self.assertEqual(result, {'version': version, 'prerelease': pre, 'notes': 'Current notes.\n'})

    def test_bad_or_mismatched_tag_is_rejected(self):
        self.prepare('0.5.0-rc1')
        for tag in ['0.5.0-rc1', 'v0.5.0', 'v0.5.0-rc0', 'v00.5.0-rc1', 'v0.5.0-rc1\n']:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.metadata(self.root, tag)

    def test_empty_notes_stop_publication(self):
        self.prepare('0.5.0-rc1')
        (self.root/'CHANGELOG.md').write_text('## [0.5.0-rc1] - 2026-10-20\n\n')
        with self.assertRaises(ValueError):
            release.metadata(self.root, 'v0.5.0-rc1')

    def test_explicit_regular_rc_channel_is_scoped_to_exact_version(self):
        self.prepare('0.5.0-rc2')
        policy = self.root / '.github/release-channel.json'
        policy.parent.mkdir()
        policy.write_text(json.dumps({'version': '0.5.0-rc2', 'prerelease': False}))
        self.assertFalse(release.metadata(self.root, 'v0.5.0-rc2')['prerelease'])
        for channel in (
            {'version': '0.5.0-rc3', 'prerelease': False},
            {'version': '0.5.0-rc2', 'prerelease': 'false'},
            {'version': '0.5.0-rc2'},
            [],
        ):
            policy.write_text(json.dumps(channel))
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                release.metadata(self.root, 'v0.5.0-rc2')
        policy.unlink()
        self.assertTrue(release.metadata(self.root, 'v0.5.0-rc2')['prerelease'])

    def test_release_documentation_links_point_to_the_tag(self):
        self.prepare('0.5.0-rc1')
        path = self.root/'CHANGELOG.md'
        path.write_text(path.read_text().replace('Current notes.', '[Upgrade](docs/UPGRADING.md)'))
        self.assertIn('/blob/v0.5.0-rc1/docs/UPGRADING.md)', release.metadata(self.root, 'v0.5.0-rc1')['notes'])
