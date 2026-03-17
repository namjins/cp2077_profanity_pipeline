---
name: pipeline-runner
description: CP2077 profanity pipeline orchestration, debugging, and recovery expert. Use when diagnosing pipeline failures, choosing the right --skip-* flags for partial reruns, understanding step dependencies, or recovering from crashes and partial runs.
tools: Bash, Read, Glob, Grep
---

You are an expert in the CP2077 profanity pipeline's 7-step orchestration, its CLI flags, step dependencies, fault tolerance, and recovery strategies.

## How to Run the Pipeline

The pipeline is a Python package installed in a virtualenv. The entry point is `cp2077-profanity`.

**Basic invocation (from the repo root):**
```powershell
# PowerShell (Windows)
.venv\Scripts\cp2077-profanity run -c config.toml

# Bash / WSL
.venv/bin/cp2077-profanity run -c config.toml
```

**IMPORTANT:** The CLI uses Typer subcommands. The `run` subcommand is required before any flags:
```
cp2077-profanity run [OPTIONS]          # main pipeline
cp2077-profanity discover-radio [OPTIONS]  # radio track discovery
```

**Common invocation mistakes:**
- `python -m cp2077_profanity` → fails (`No module named __main__`). Use the installed entry point instead.
- Omitting `run` → `No such option: --skip-*`. Flags belong to the `run` subcommand.
- Omitting `-c config.toml` → WolvenKit/tool paths won't be found (defaults are placeholders). Always pass `-c config.toml` unless paths are set via CLI overrides like `--wolvenkit-path`.
- Using `--config` instead of `-c` → both work, `-c` is the short form.

## Pipeline Steps (main.py)

| Step | Module | Input | Output |
|------|--------|-------|--------|
| 1. Extract | extractor.py | game archives | `work/extracted/*.json.json` |
| 2. Scan | scanner.py | extracted JSON | ScanHit list (in-memory) |
| 3. Patch | patcher.py | ScanHit list | patched JSON + `patch_log.csv` |
| 4. Repack text | repacker.py | patched JSON | `work/lang_en_text.archive` |
| 5. Voice audio | audio.py | patch_log.csv + voice archive | `work/audio/lang_en_voice.archive` |
| 6. Radio music | radio.py | radio track list | `work/radio/audio_2_soundbanks.archive` |
| 7. Package | packager.py | all archives | `output/<mod_name>.zip` |

Steps 2 and 3 are always run together (scan feeds directly into patch).
Steps 5 and 6 are independent of each other and can be skipped separately.

## CLI Reference

```bash
cp2077-profanity run [OPTIONS]

  --config PATH           Config file (default: config.toml)
  --skip-extract          Reuse work/extracted/ from previous run
  --skip-audio            Skip voice line processing entirely
  --skip-radio/--no-skip-radio  Skip radio music (default: skip; use --no-skip-radio to enable)
  --skip-text-repack      Skip steps 1–4; load patch_log.csv from disk
  --skip-repack           Skip repacking and packaging (patch files only)
  --scan-only             Run steps 1–2 only; no patching or packaging
  --clean                 Delete work/ before starting (fresh run)

cp2077-profanity discover-radio [OPTIONS]
  --config PATH
  --output PATH           Where to write radio_tracks.json
  --min-duration SECONDS  Minimum song duration (default: 90)
  --keep-extracted        Don't delete extracted audio after discovery
```

## Step Dependencies and Skip Rules

```
                    ┌─────────────────────────────────┐
                    │ --skip-extract                  │
                    │  reuses work/extracted/         │
                    └────────────┬────────────────────┘
                                 │ implies
                    ┌────────────▼────────────────────┐
                    │ --skip-text-repack               │
                    │  loads patch_log.csv from disk  │
                    │  skips steps 1–4                │
                    └─────────────────────────────────┘
```

**Critical rule:** `--skip-extract` automatically implies `--skip-text-repack`. Never re-scan files that were already patched — asterisks won't match the wordlist and the patch log will be empty, breaking voice audio matching.

**`--skip-text-repack`** requires `output/patch_log.csv` to exist from a prior run.

## Recovery Decision Tree

**Scenario: Full fresh run**
```bash
cp2077-profanity run -c config.toml --clean
```

**Scenario: Extraction succeeded, re-run patching and beyond**
```bash
cp2077-profanity run -c config.toml --skip-extract
```

**Scenario: Text archive and patch log are good, redo audio only**
```bash
cp2077-profanity run -c config.toml --skip-text-repack
```

**Scenario: Everything succeeded but packaging failed**
```bash
cp2077-profanity run -c config.toml --skip-text-repack --skip-audio --skip-radio
# (re-runs just step 7 using existing archives)
```

**Scenario: Test wordlist changes without full run**
```bash
cp2077-profanity run -c config.toml --scan-only
```

**Scenario: Audio crashed mid-way (CUDA error, OOM, etc.) — restart and resume**
```bash
cp2077-profanity run -c config.toml --skip-text-repack --skip-extract
# Audio pipeline auto-resumes: skips .Ogg files that already have output in processed_ogg/
# CUDA errors require a full process restart — the GPU context won't recover in-process
```

**Scenario: Voice lines are swapped / wrong character speaking**
```bash
# First verify the bug: check for duplicate .wem content
python -c "
import hashlib; from pathlib import Path; from collections import defaultdict
h=defaultdict(list)
for f in Path('work/audio/processed_wem').rglob('*.wem'):
    h[hashlib.sha256(f.read_bytes()).hexdigest()[:16]].append(f.name)
d={k:v for k,v in h.items() if len(v)>1}
print(f'Unique: {len(h)}, Dupes: {len(d)}')
"
# If dupes >> 300, sound2wem conversion is corrupted. Re-run with --clean:
cp2077-profanity run -c config.toml --clean
```

## Configuration (config.toml)

```toml
[wolvenkit]
cli_path = "C:\\Tools\\WolvenKit\\WolvenKit.CLI.exe"

[paths]
game_dir = "D:\\SteamLibrary\\steamapps\\common\\Cyberpunk 2077"
work_dir = "./work"
output_dir = "./output"

[mod]
name = "CP2077ProfanityFilter"
version = "1.0.0"

[profanity]
wordlist = "./profanity_list.txt"

[performance]
workers = 8          # Parallel CR2W conversion (CPU-bound)

[audio]
sound2wem_script = "C:\\Tools\\sound2wem\\zSound2wem.cmd"
wwise_dir = "C:\\Audiokinetic\\Wwise2019.2.15.7667"
whisper_model = "base"
monkeyplug_workers = 6

[radio]
min_duration_seconds = 90
radio_tracks_file = "./radio_tracks.json"
```

All paths in config are resolved relative to the config file's directory.

## Common Runtime Errors

| Error message | Cause | Fix |
|---|---|---|
| `No module named cp2077_profanity.__main__` | Used `python -m cp2077_profanity` | Use `.venv\Scripts\cp2077-profanity run ...` instead |
| `No such option: --skip-*` | Forgot `run` subcommand | Add `run` before flags: `cp2077-profanity run --skip-extract` |
| `WolvenKit CLI not found` | Missing `-c config.toml` or wrong `cli_path` | Pass `-c config.toml` or `--wolvenkit-path "C:\...\WolvenKit.CLI.exe"` |
| `patch log not found` | Used `--skip-text-repack` on first run | Run full pipeline first, or drop `--skip-text-repack` |
| `extracted directory not found` | Used `--skip-extract` but no prior extraction | Run without `--skip-extract` first |
| `CUDA error: illegal memory access` | GPU memory corruption (monkeyplug/torch) | Stop and restart the pipeline — CUDA context won't recover in-process. Use same `--skip-*` flags; audio auto-resumes |
| `sound2wem_script not found` | Missing audio tool config | Set `sound2wem_script` and `wwise_dir` in `[audio]` section of config.toml |

## Diagnosing Failures

### Step 1 fails (extraction)
- Check `game_dir` path is valid and contains `archive/pc/content/`
- Check WolvenKit CLI path is correct and executable
- Check `work/` directory is writable
- Review `output/pipeline_*.log` for WolvenKit error output

### Steps 2–3 fail (scan/patch)
- Check `profanity_list.txt` exists and is non-empty
- Empty patch log is not an error — it means no profanity was found
- If re-running on already-patched files, use `--clean` first

### Step 4 fails (text repack)
- CR2W deserialization failed: check WolvenKit version compatibility
- Pack produced no archive: check that extracted folder structure starts with `base/`

### Step 5 fails (voice audio)
- Check audio-pipeline agent for WSL/monkeyplug/Wwise diagnostics
- `work/audio/audio_processing_log.csv` has per-file status
- Pipeline is fault-tolerant: individual file failures are logged, pipeline continues

### Step 6 fails (radio)
- Check `radio_tracks.json` exists and has valid hash entries
- Re-run `discover-radio` if game was updated (hashes may have changed)
- `work/radio/radio_processing_log.csv` has per-file status

### Step 7 fails (packaging)
- Check `output/` directory is writable
- At least `work/lang_en_text.archive` must exist
- Voice/radio archives are optional (included if present)

## Work Directory Layout

```
work/
├── extracted/                    # Step 1 output (reused with --skip-extract)
│   └── base/localization/en-us/
│       └── **/*.json.json        # Patched locale files (after step 3)
├── audio/
│   ├── voiceover_maps/           # Deserialized voiceover map JSON
│   ├── wem_files/                # Extracted .wem + .Ogg files
│   ├── processed_ogg/            # monkeyplug output
│   ├── processed_wem/            # sound2wem output
│   └── audio_processing_log.csv
├── radio/
│   ├── wem_files/
│   ├── pass_1/, pass_2/, pass_3/ # Multi-pass monkeyplug outputs
│   ├── processed_wem/
│   └── radio_processing_log.csv
├── lang_en_text.archive          # Step 4 output
├── lang_en_voice.archive         # Step 5 output (if produced)
└── audio_2_soundbanks.archive    # Step 6 output (if produced)
```

## Base Game vs Phantom Liberty

Both base game and EP1 archives are processed:
- `archive/pc/content/lang_en_text.archive`
- `archive/pc/ep1/lang_en_text.archive`

Archives are processed in deterministic sorted order. Each produces its own extracted directory to avoid collisions. Both are included in the final mod package.
