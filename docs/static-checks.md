# Python static checks and Actions maintenance

The CI correctness gate uses **Ruff 0.17.3**, pinned in
[`requirements-static.txt`](../requirements-static.txt). It selects syntax/error
rules `E9`, `F63`, `F7` and `F82`, including undefined names. It does not format
the source, enforce a new style, or replace tests and security review. There are
no file exclusions or blanket ignores for existing findings.

The checker selects every tracked `.py` file and the extensionless `dots-ts`
entry point, including `scripts/`, `tests/`, **all of `research/`** and future directories.
New source files must be added to Git before this tracked-file check includes
them. Untracked worktrees and local installation/state files are not scanned.
The Ruff default target is Python 3.8; research uses Python 3.12 and developer
scripts use Python 3.11. Those settings guide selected version-sensitive rules;
they are not evidence that every path runs on every Python version. The existing
runtime compatibility and integration tests remain necessary.

Run from the repository checkout using a dedicated developer environment:

```bash
python3 -m venv .venv-static
.venv-static/bin/python -m pip install -r requirements-static.txt
.venv-static/bin/python scripts/check_python_static.py --self-test
.venv-static/bin/python scripts/check_python_static.py
```

The self-test writes a real isolated extensionless file named `dots-ts` and passes
its path through the same config. It succeeds only when Ruff rejects the undefined name
with `F821`, rather than accepting the fixture or failing to load its config.
The ordinary gate checks the selected repository sources without changing them
or creating a Ruff cache. A wrong/missing Ruff version fails with an installation
instruction instead of silently running another version. The tooling is not
installed into Dots by the setup script or updater.

`Checks` runs the self-test and source gate on every push and pull request in the
**Python static checks** job. Core tests, the source-manifest check and ShellCheck
remain separate gates. Research runtime tests remain path-filtered; the Python
static gate includes research even for other changes.

All GitHub Actions remain pinned to full commit SHAs. Dependabot checks the
`github-actions` ecosystem weekly and opens grouped update PRs. The configuration
does not enable automatic merging or deployment. Review an update's upstream
release and exact commit, then let applicable checks pass before merging. Ruff
updates are separate reviewed changes to its requirement and wrapper version;
this issue does not enable broader package-dependency updates.
