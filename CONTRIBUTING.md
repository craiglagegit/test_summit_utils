# Contributing to test_summit_utils

## Development environment

`test_summit_utils` is developed against the shared Rubin/LSST stack on SDF. Set up the
environment with:

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

## Speeding up Claude Code's Bash tool on the shared LSST stack

> This section is only relevant if you use [Claude Code](https://docs.claude.com/en/docs/claude-code)
> on SDF. It does not affect normal interactive use of the repo.

### The problem

Claude Code's Bash tool runs every command through a non-interactive shell that
sources your shell init. On SDF that triggers the full conda + EUPS setup
(`loadLSST.sh` + `setup lsst_distrib`) over NFS on **every single command**.
Trivial calls (`git log`, `ls`) routinely exceed the harness 2-minute timeout
and get killed.

The fix caches the *resolved* environment once and re-sources a static snapshot
(≈instant) instead of re-resolving it each call. Observed result: python
startup ~0.5s, `import metroid` ~0.8s.

### Setup

#### 1. Create the loader script `~/.claude/lsst-env-loader.sh`

```bash
# shellcheck shell=bash
# LSST env loader for Claude Code's Bash tool (wired via BASH_ENV in settings.json).
# Caches the resolved `setup lsst_distrib` env and re-sources it instead of re-resolving.

_LSST_CACHE="${HOME}/.cache/lsst-env.sh"
_LSST_LOAD_CMD='source /sdf/group/rubin/sw/w_latest/loadLSST.sh && setup lsst_distrib'

# Build the cache from a clean shell -> absolute, re-sourceable snapshot.
_lsst_build_cache() {
    local target
    target=$(readlink -f /sdf/group/rubin/sw/w_latest 2>/dev/null)
    mkdir -p "${HOME}/.cache"
    env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin bash --noprofile --norc -c '
        source /sdf/group/rubin/sw/w_latest/loadLSST.sh >/dev/null 2>&1
        setup lsst_distrib
        echo "# Auto-generated LSST env snapshot. Do not edit by hand."
        echo "# w_latest -> '"$target"'"
        export -p
        declare -f setup unsetup 2>/dev/null
    ' > "${_LSST_CACHE}.tmp" && mv -f "${_LSST_CACHE}.tmp" "$_LSST_CACHE"
}

# Force a rebuild on demand (backstop for in-place stack patches).
refresh-lsst-env() {
    echo "Rebuilding LSST env cache (running real setup once)..." >&2
    _lsst_build_cache && { unset _LSST_ENV_LOADED; _lsst_load_env; echo "Done." >&2; }
}

_lsst_load_env() {
    local target
    target=$(readlink -f /sdf/group/rubin/sw/w_latest 2>/dev/null)

    # Per-shell sentinel: child shells already inherit the env, so skip re-sourcing
    # unless the stack target changed.
    [ "$_LSST_ENV_LOADED" = "$target" ] && return 0

    # Rebuild if cache missing or recorded target no longer matches.
    if [ ! -f "$_LSST_CACHE" ] || ! grep -q "w_latest -> ${target}$" "$_LSST_CACHE" 2>/dev/null; then
        _lsst_build_cache
    fi

    # Fast path: source the snapshot. Fall back to the real setup if unusable.
    # shellcheck source=/dev/null
    if [ -f "$_LSST_CACHE" ] && source "$_LSST_CACHE" 2>/dev/null; then
        export _LSST_ENV_LOADED="$target"
    else
        eval "$_LSST_LOAD_CMD"
        export _LSST_ENV_LOADED="$target"
    fi
}

_lsst_load_env
```

#### 2. Wire it into Claude Code only — `~/.claude/settings.json`

Add to the `env` block (use **your own** home path):

```json
"env": {
    "BASH_ENV": "/sdf/home/<u>/<user>/.claude/lsst-env-loader.sh"
}
```

`BASH_ENV` is read by non-interactive `bash -c`, which is exactly what the Bash
tool uses — so this affects Claude only and leaves interactive shells alone.

#### 3. Keep `loadLSST`/`setup` out of interactive `.bashrc` (optional)

If you previously put the `loadLSST.sh` / `setup lsst_distrib` lines in
`~/.bashrc`, comment them out. The loader now handles the env for Claude;
leaving them in `.bashrc` re-introduces the slow per-command init.

### How it behaves

- **First call:** builds `~/.cache/lsst-env.sh` (an `export -p` +
  `declare -f setup unsetup` dump, ~300 lines) by running the real setup once.
- **Every subsequent call:** sources that static snapshot — assignments, not
  appends, so it's idempotent and ~instant.
- **Self-heals:** the cache header records the resolved `w_latest` target (e.g.
  `w_2026_26`). When the weekly stack rolls over, the target changes and the
  loader transparently rebuilds.
- **Per-shell sentinel** `$_LSST_ENV_LOADED` makes nested/child shells no-ops
  (they already inherit the env).
- **Safe fallback:** if the cache is ever unusable, it falls back to the
  guaranteed-correct `source loadLSST.sh && setup lsst_distrib`, so a bad cache
  can't strand you.

### Operational notes / gotchas

- **After an in-place stack patch** that does *not* move the `w_latest`
  symlink, the loader won't notice. Run `refresh-lsst-env` to force a rebuild.
- **Verify in the real harness shell** (e.g. `bash --login -c '...'`), not under
  a hand-stripped `env -i HOME=... PATH=/usr/bin:/bin` — over-stripping drops
  conda paths and *falsely* looks broken.
- The heavy **first import of astropy/galsim** (~15s cold over NFS) is a
  separate cost and is unchanged by this — it's import-time work, not shell init.
- Quick sanity check after setup:

  ```bash
  echo "$_LSST_ENV_LOADED"     # should print the resolved w_latest target
  type setup                   # should say "setup is a function"
  python -c "import sys; print(sys.executable)"   # lsst-scipipe conda python
  ```
