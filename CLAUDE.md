# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# test_summit_utils

This repository contains `test_summit_utils`, a Python library to experiment with using Claude to test new summit_utils code. This document contains critical information about working with this codebase. Follow these guidelines precisely.

## Current state (read first)

This repo was cloned from an upstream template (`metroid`, an unrelated astronomy simulation
project) and has been de-templated but has **almost no production code yet**:
`src/claude_scons_db/__init__.py` is empty, `utils/` is an empty subpackage, and the architecture
is still `TBD` in the subpackage `CLAUDE.md`. Building out that architecture is the main work ahead.

The importable package is `test_summit_utils` (underscores — the repo dir uses a hyphen, but the
Python package must not). `pyproject.toml`/`environment.yaml` have been reset to this project's
identity with `numpy` as the only runtime dependency; add real dependencies as the code grows.

## Development environment

Developed against the shared Rubin/LSST stack on SDF. There is no `python`/`black`/`pytest` on the
bare login PATH — you must set up the stack first.  After setting up the stack, we need to connect to the correct branch of summit_utils, i.e., the branch we want to test.
```bash
source /sdf/group/rubin/sw/w_latest/loadLSST.sh
setup lsst_distrib
cd src
mkdir WORK
cd WORK
git clone https://github.com/lsst-so/summit_utils.git
cd summit_utils
git checkout tickets/DM-56137
setup -j -r src/WORK/summit_utils
```

See `CONTRIBUTING.md` for the Claude Code Bash-tool env-caching setup (`BASH_ENV` loader) that
avoids re-running this slow NFS-backed init on every command. A conda `environment.yaml` is also
provided (`conda env create -f environment.yaml`) and adds `matplotlib` + `notebook` for
interactive work.

## Common commands

```bash
# Editable install with dev extras
pip install -e ".[dev]"

# Format (line length 110, enforced repo-wide)
black .

# Static type checking (required by the rules; not yet in dev extras)
mypy src

# Run the full test suite
pytest

# Run a single test file / test / pattern
pytest tests/utils/test_foo.py
pytest tests/utils/test_foo.py::test_bar
pytest -k "pattern"
```

## Core Development Rules

1. Development Philosophy
  - **Simplicity**: Write simple, straightforward code
  - **Readability**: Make code easy to understand
  - **Performance**: Consider performance without sacrificing readability
  - **Maintainability**: Write code that's easy to update
  - **Testability**: Ensure code is testable
  - **Reusability**: Create reusable components and functions
  - **Less Code = Less Debt**: Minimize code footprint

2. Code Style
  - Modern type hints required for all code
  - PEP 8
  - Class names in PascalCase
  - Constants in UPPER_SNAKE_CASE
  - Document with docstrings
  - Line length: 110 chars maximum
  - Lines that intentionally deviate from PEP 8 must include a `noqa` comment with flake8 error code

3. Documenting Python APIs
  - PEP 257
  - Numpydoc Style
  - Docstring line length: 79 chars maximum

## Python Tools

  - use `black` to fix PEP 8
  - use `mypy` for static type checking
  - use `pytest` for unit tests

## Git Workflow

- Always use feature branches; do not commit directly to `main`
  - Name branches descriptively: `fix/auth-timeout`, `documentation/code_examples`, `feature/api-pagination`
  - Keep one logical change per branch to simplify review and rollback
- Create pull requests for all changes
  - Open a draft PR early for visibility; convert to ready when complete
  - Ensure tests pass locally before marking ready for review
- Link issues
  - Before starting, reference an existing issue or create one
  - Use commit/PR messages like `Fixes #123` for auto-linking and closure
- Commit practices
  - Make atomic commits (one logical change per commit)
  - Prefer conventional commit style: `type(scope): short description`
    - Examples: `feature(eval): group OBS logs per test`, `fix(cli): handle missing API key`
  - Squash only when merging to `main`; keep granular history on the feature branch
- Practical workflow
  1. Create or reference an issue
  2. `git checkout -b feature/issue-123-description`
  3. Commit in small, logical increments
  4. Open a draft PR early
  5. Convert to ready PR when functionally complete and tests pass
  6. Never merge automatically, always prompt first

## Repository Layout

- Production code in `src`
- Tests in `tests` mirror the package structure
- Subpackages in subdirectories of `src/`
- Public utilities in `src/utils`

## Context Management

- CLAUDE.md provides context of the directory contents
  - At top-level and in each subdirectory containing production code
- Include list of Known issues / gotchas
  - Include the GitHub issues referencing an item
  - Remove item if GitHub issue is closed
- Update context if new bugs are found or fundamental changes are made to code architecture
</content>
</invoke>
