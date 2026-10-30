"""Keep remote workflow dependencies immutable without requiring a YAML library."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def unpinned_uses(text):
    bad = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('- '):
            stripped = stripped[2:].lstrip()
        if not stripped.startswith('uses:'):
            continue
        value = stripped[5:].split('#', 1)[0].strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                bad.append(number)
                continue
            value = value[1:-1]
        valid = (value.startswith('./') and not any(part == '..' for part in value.split('/'))
                 or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}', value)
                 or re.fullmatch(r'docker://[^\s@]+@sha256:[0-9a-f]{64}', value))
        if not valid:
            bad.append(number)
    return bad


class WorkflowPinTests(unittest.TestCase):
    def test_every_remote_use_in_current_workflows_is_pinned(self):
        workflows = ROOT / '.github/workflows'
        for path in sorted(list(workflows.glob('*.yml')) + list(workflows.glob('*.yaml'))):
            with self.subTest(workflow=path.name):
                self.assertEqual(unpinned_uses(path.read_text()), [])

    def test_tags_branches_and_dynamic_action_references_are_rejected(self):
        for target in ('actions/checkout@v7', 'actions/checkout@main', '${{ inputs.action }}',
                       'docker://python:3.13', './outside/../action'):
            self.assertEqual(unpinned_uses('- uses: ' + target), [1])
        for target in ('actions/checkout@' + 'a' * 40, './.github/actions/local',
                       'docker://example/image@sha256:' + 'b' * 64):
            self.assertEqual(unpinned_uses('  - uses: "' + target + '" # pinned'), [])


if __name__ == '__main__':
    unittest.main()
