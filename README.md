# OrcaSlicer Profile Sync (Mac + Windows)

This repo includes a fast MVP CLI: `sync.py`.

## Storage Locations (explicit)

- Local Orca data (live files on your machine): configured in `./.orcasync/config.json` as `local_orca_dir`
- Repo mirror (Git-tracked files pushed to GitHub): `./profiles/`
- Last-sync baseline for conflict detection: `./.orcasync/state.json`
- Tool config: `./.orcasync/config.json`

The script also prints these paths every run so you can verify exactly where data is being read/written.

## Commands

```bash
python3 sync.py status
python3 sync.py push -m "Sync OrcaSlicer profiles"
python3 sync.py pull
```

## Workflow

1. On machine A, make profile changes in OrcaSlicer, then run:

```bash
python3 sync.py push -m "Update profiles"
```

2. On machine B, run:

```bash
python3 sync.py pull
```

## Conflict Behavior

- A conflict is detected when both local and mirror changed the same file since last successful sync.
- On conflict, `push`/`pull` stops and lists files.
- Resolve manually, then run the command again.

## First Run Notes

- `sync.py` auto-creates `./.orcasync/config.json` with a default OrcaSlicer path.
- If OrcaSlicer is installed in a custom location, edit `local_orca_dir` in `./.orcasync/config.json`.
