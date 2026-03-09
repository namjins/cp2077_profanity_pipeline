# CP2077 Profanity Filter Mod

CLI toolchain that extracts English text from Cyberpunk 2077 game files, detects profane words using a configurable wordlist, replaces them with asterisks of equal length, and packages the result as a distributable REDmod.

## Prerequisites

- Python 3.11+
- [WolvenKit CLI](https://github.com/WolvenKit/WolvenKit) — required for extracting/repacking `.archive` files

### Installing WolvenKit CLI

1. Download the latest release from [WolvenKit Releases](https://github.com/WolvenKit/WolvenKit/releases)
2. Extract `WolvenKit.CLI.exe` to a known location (e.g. `C:\Tools\WolvenKit\`)
3. Set the path in `config.toml` or pass it via `--wolvenkit-path`

> **Note:** WolvenKit CLI is a Windows executable. On macOS/Linux it can be run via Wine, but native use requires Windows.

## Setup

```bash
# Clone and install
git clone https://github.com/your-username/cp2077_profanity_pipeline.git
cd cp2077_profanity_pipeline
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

## Configuration

Copy the example config and edit it:

```bash
cp config.toml.example config.toml
```

Key settings in `config.toml`:

| Setting | Description |
|---------|-------------|
| `wolvenkit.cli_path` | Path to `WolvenKit.CLI.exe` |
| `paths.game_dir` | Root of your Cyberpunk 2077 installation |
| `paths.work_dir` | Working directory for intermediate files (default: `./work`) |
| `paths.output_dir` | Output directory for the final mod (default: `./output`) |
| `profanity.wordlist` | Path to the profanity wordlist file |

## Usage

```bash
# Full pipeline: extract → scan → patch → repack → package
cp2077-profanity run --config config.toml

# Scan only — no files modified, just report matches
cp2077-profanity run --config config.toml --scan-only

# Skip extraction (reuse previously extracted files)
cp2077-profanity run --config config.toml --skip-extract

# Patch only (skip repack/packaging)
cp2077-profanity run --config config.toml --skip-repack

# Override any setting via CLI flags
cp2077-profanity run \
  --wolvenkit-path "C:\Tools\WolvenKit\WolvenKit.CLI.exe" \
  --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077" \
  --wordlist ./my_wordlist.txt
```

## Pipeline Steps

| Step | What happens |
|------|-------------|
| **1. Extract** | Unpacks `lang_en_text.archive` using WolvenKit `unbundle`, then converts CR2W files to JSON with `cr2w -s` |
| **2. Scan** | Detects profanity using whole-word regex matching on normalized text |
| **3. Patch** | Replaces matched words with asterisks of equal length (`damn` → `****`) |
| **4. Repack** | Converts JSON back to CR2W with `cr2w -d`, then repacks with WolvenKit `pack` |
| **5. Package** | Assembles REDmod layout (`localization/en-us/`), writes `info.json`, creates `.zip` |

## Output

| File | Description |
|------|-------------|
| `output/patch_log.csv` | Audit trail of every string changed (filepath, key, original, replacement) |
| `output/summary.txt` | Counts of files modified, strings changed, unique words flagged |
| `output/CP2077ProfanityFilter.zip` | Installable REDmod package |

## Installing the Mod

1. Extract `CP2077ProfanityFilter.zip` into your Cyberpunk 2077 `mods/` folder:
   ```
   Cyberpunk 2077\mods\CP2077ProfanityFilter\
   ```
2. Enable the mod via the REDmod launcher or add it to `mods.json`

## Profanity Wordlist

Edit `profanity_list.txt` to customize which words are filtered:

- One word or phrase per line
- Lines starting with `#` are comments; blank lines are ignored
- Matching is **whole-word only** and **case-insensitive**
- Elongated spellings are matched automatically (e.g. `fuuuuuck` matches `fuck`)

## How It Works

- Only `femaleVariant` and `maleVariant` fields in locale JSONs are modified
- Non-English locales are skipped entirely
- Whole-word boundaries prevent false positives (`classic` does not match `ass`)
- **Elongation detection:** runs of 3+ identical characters are collapsed before matching (`fuuuuuck` → `fuck`), while legitimate double letters (`good`, `ass`) are preserved
- Asterisks replace the full original character span, including elongation (`fffffffuuuuuuuuck` → `*****************`)
