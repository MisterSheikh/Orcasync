# OrcaSlicer Profile Sync (Mac + Windows)

This repo includes a fast MVP CLI: `sync.py`.

## Storage Locations (explicit)

- Local Orca base dir (live files on your machine): configured as `local_orca_dir`
- Local sync scope under that base dir: configured as `local_scope_subdir` (default `user/default`)
- Only these folders are synced from that scope: `sync_folders` (default `filament`, `machine`, `process`)
- Repo mirror (Git-tracked files pushed to GitHub): `./profiles/`
- Last-sync baseline for conflict detection: `./.orcasync/state.json`
- Tool config: `./.orcasync/config.json`

The script also prints these paths every run so you can verify exactly where data is being read/written.

## Commands

```bash
python3 sync.py status
python3 sync.py push -m "Sync OrcaSlicer profiles"
python3 sync.py pull
python3 sync.py apply
python3 sync.py apply --prune
python3 sync.py wipe-profiles --yes
```

## Workflow

1. On machine A, make profile changes in OrcaSlicer, then run:

```bash
python3 sync.py push -m "Update profiles"
```

2. On machine B, fetch latest repo changes only (no local overwrite):

```bash
python3 sync.py pull
```

3. When you are ready to overwrite local Orca profile files from repo mirror:

```bash
python3 sync.py apply
```

If you also want to remove local scoped files not present in mirror:

```bash
python3 sync.py apply --prune
```

## Command Behavior

- `push`: sync local scoped folders -> `profiles/`, then commit + push.
- `pull`: `git pull --rebase` only; does not touch local Orca folders.
- `apply`: copy/overwrite `profiles/` -> local scoped Orca folders (no deletions unless `--prune`).
- `wipe-profiles --yes`: clears all files under `profiles/` only (never touches local Orca folders).

## Conflict Behavior

- A conflict is detected when both local and mirror changed the same file since last successful sync.
- On conflict, `push` stops and lists files.

## First Run Notes

- `sync.py` auto-creates `./.orcasync/config.json` with a default OrcaSlicer path.
- If OrcaSlicer is installed in a custom location, edit `local_orca_dir` in `./.orcasync/config.json`.
- By default the script syncs only `user/default/filament`, `user/default/machine`, and `user/default/process`.
