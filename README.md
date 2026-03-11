# CP2077 Profanity Filter Mod

CLI toolchain that extracts English text and voice audio from Cyberpunk 2077 game files, detects profane words using a configurable wordlist, replaces them with asterisks in text and silence in audio, and packages the result as a distributable mod.

## What This Does

- **Text filtering**: Replaces profane words in all English UI text/dialogue with asterisks of equal length (e.g. `fuck` → `****`, `fuuuuuck` → `*********`)
- **Audio filtering**: Silences the corresponding voice lines using AI speech recognition (via [monkeyplug](https://github.com/mmguero/monkeyplug) + Whisper)
- Covers both the **base game** and **Phantom Liberty** expansion
- Produces an installable `.archive` file — just extract and play

---

## Prerequisites

### Required (text pipeline)

| Tool | Purpose | Where to get |
|------|---------|--------------|
| **Python 3.11+** | Runs the pipeline | [python.org](https://www.python.org/downloads/) — check "Add to PATH" |
| **Git** | Clone the repo | [git-scm.com](https://git-scm.com/download/win) |
| **WolvenKit CLI** | Extracts/repacks CP2077 archives | [GitHub Releases](https://github.com/WolvenKit/WolvenKit/releases) |

> WolvenKit CLI is Windows-only. The entire pipeline must run on Windows.

### Required (audio pipeline)

| Tool | Purpose | Where to get |
|------|---------|--------------|
| **Audiokinetic Wwise 2019.2.15** | Converts OGG → WEM | [audiokinetic.com](https://www.audiokinetic.com/en/download) (free account) |
| **sound2wem** | Wwise CLI wrapper script | [GitHub](https://github.com/EternalLeo/sound2wem) |
| **WSL2** | Runs monkeyplug on Linux | Windows Features (built-in) |
| **monkeyplug** | AI audio profanity filter | `pip install monkeyplug` (inside WSL) |
| **openai-whisper** | Speech recognition for monkeyplug | `pip install openai-whisper` (inside WSL) |
| **ffmpeg** | Audio processing | `sudo apt install ffmpeg` (inside WSL) |

---

## One-Time Setup

### 1. Install WolvenKit CLI

1. Download the latest release from [WolvenKit Releases](https://github.com/WolvenKit/WolvenKit/releases)
2. Extract `WolvenKit.CLI.exe` to `C:\Tools\WolvenKit\`

### 2. Install Wwise (for audio pipeline only)

1. Create a free account at [audiokinetic.com](https://www.audiokinetic.com/en/download)
2. Download and install **Audiokinetic Launcher**
3. From the Launcher, install **Wwise version 2019.2.15** — you only need the **Authoring** component
4. Default install path: `C:\Audiokinetic\Wwise2019.2.15.7667\`

### 3. Install sound2wem (for audio pipeline only)

```powershell
git clone https://github.com/EternalLeo/sound2wem C:\Tools\sound2wem
```

### 4. Set up WSL2 + monkeyplug (for audio pipeline only)

Open PowerShell as Administrator:
```powershell
wsl --install
```

Restart, then open the Ubuntu terminal and run:
```bash
sudo apt update && sudo apt install -y ffmpeg python3-pip python3-venv
pip install monkeyplug openai-whisper
```

If you have an NVIDIA GPU (recommended for speed):
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install openai-whisper
```

### 5. Clone and install the pipeline

```powershell
git clone https://github.com/namjins/cp2077_profanity_pipeline.git
cd cp2077_profanity_pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

> If you get a script execution error, run this first:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

## Configuration

Copy the example config and edit it:

```powershell
copy config.toml.example config.toml
notepad config.toml
```

### config.toml settings

```toml
[wolvenkit]
cli_path = "C:\\Tools\\WolvenKit\\WolvenKit.CLI.exe"

[paths]
game_dir = "D:\\SteamLibrary\\steamapps\\common\\Cyberpunk 2077"
work_dir = "./work"       # intermediate files (can get large — several GB)
output_dir = "./output"   # final zip goes here

[mod]
name = "CP2077ProfanityFilter"
version = "1.0.0"
description = "Replaces profane words in English localization with asterisks"

[profanity]
wordlist = "./profanity_list.txt"

[performance]
workers = 8   # parallel CR2W conversion workers — increase if you have more CPU cores

[audio]
sound2wem_script = "C:\\Tools\\sound2wem\\zSound2wem.cmd"
wwise_dir = "C:\\Audiokinetic\\Wwise2019.2.15.7667"
whisper_model = "base"   # tiny/base/small/medium/large — larger = more accurate but slower
```

---

## Running the Pipeline

Activate the venv first (required every new terminal session):
```powershell
cd cp2077_profanity_pipeline
.venv\Scripts\activate
```

### Full pipeline (text + audio)
```powershell
cp2077-profanity --config config.toml
```

### Text only (skip audio — much faster)
```powershell
cp2077-profanity --config config.toml --skip-audio
```

### Re-run without re-extracting (saves ~30–60 min)
```powershell
cp2077-profanity --config config.toml --skip-extract
cp2077-profanity --config config.toml --skip-extract --skip-audio
```

### Scan only — preview what would be changed, no files modified
```powershell
cp2077-profanity --config config.toml --scan-only
```

### All flags
```powershell
cp2077-profanity --help
```

---

## Pipeline Steps

| Step | What happens |
|------|-------------|
| **1. Extract** | Unpacks `lang_en_text.archive` from base game + Phantom Liberty using WolvenKit `unbundle`, then converts CR2W binary files to JSON with `cr2w -s` (parallelized) |
| **2. Scan** | Detects profanity using whole-word regex matching with elongation normalization |
| **3. Patch** | Replaces matched words with asterisks of equal original length, writes `patch_log.csv` |
| **4. Repack text** | Converts patched JSON back to CR2W binary with `cr2w -d` (parallelized), repacks with WolvenKit `pack` → produces `.archive` |
| **5. Process audio** | Parses voiceover map files to find voice lines matching patched strings, extracts them via WolvenKit `uncook`, processes each `.Ogg` through monkeyplug (WSL + Whisper), converts back to `.wem` via Wwise, repacks → produces voice `.archive` |
| **6. Package** | Zips all `.archive` files into an installable package |

---

## Output Files

| File | Description |
|------|-------------|
| `output/CP2077ProfanityFilter.zip` | Installable mod package |
| `output/patch_log.csv` | Audit trail of every string changed |
| `output/summary.txt` | Count of files, strings, and words changed |
| `work/` | Intermediate files — safe to delete after packaging |

---

## Installing the Mod

1. Create the mod folder if it doesn't exist:
   ```powershell
   mkdir "D:\SteamLibrary\steamapps\common\Cyberpunk 2077\archive\pc\mod"
   ```

2. Extract `CP2077ProfanityFilter.zip` into the game root:
   ```powershell
   Expand-Archive output\CP2077ProfanityFilter.zip -DestinationPath "D:\SteamLibrary\steamapps\common\Cyberpunk 2077"
   ```
   This places the `.archive` file(s) at `archive\pc\mod\` automatically.

3. Launch the game — no REDmod deployment needed.

To uninstall, delete the `.archive` file(s) from `archive\pc\mod\`.

---

## Profanity Wordlist

Edit `profanity_list.txt` to customize which words are filtered:

- One word or phrase per line
- Lines starting with `#` are comments; blank lines are ignored
- Matching is **whole-word only** and **case-insensitive**
- Elongated spellings are matched automatically (`fuuuuuck` matches `fuck`)
- The replacement preserves the full original length (`fuuuuuck` → `*********`)

---

## How It Works

### Text matching
- Only `femaleVariant` and `maleVariant` fields in locale JSONs are scanned
- Whole-word boundaries prevent false positives (`classic` does not match `ass`)
- Elongation detection: runs of 3+ identical characters are collapsed before matching (`fuuuuuck` → `fuck`) while legitimate double letters (`good`, `ass`) are preserved
- Only modified files are included in the final package

### Audio matching
- Voiceover map files (`voiceovermap.json` etc.) link each text `stringId` to `.wem` voice files
- Only voice lines whose text was patched are extracted and processed
- Audio is processed through Whisper (AI speech recognition) + monkeyplug to identify and silence profane segments
- Processed audio is re-encoded to `.wem` using Audiokinetic Wwise and repacked into a separate voice archive

### Archive mod vs REDmod
- This mod uses the `archive/pc/mod/` approach (not REDmod)
- Archive mods override base game files without needing `redmod deploy`
- Both text and voice archives install to `archive/pc/mod/`

---

## Troubleshooting

**`running scripts is disabled on this system`**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**`Multiple top-level packages discovered`** (pip install error)
Delete any `work/` or `output/` directories created at the repo root, then retry.

**`No English locale files found`**
Check that `game_dir` in `config.toml` points to the Cyberpunk 2077 root (the folder containing the `archive/` subfolder).

**`WolvenKit pack failed`**
Make sure `work/extracted/` exists and was populated by a previous extraction run. Use `--skip-extract` only after a successful extraction.

**Audio pipeline skipped / no voice lines matched**
The audio pipeline requires the text pipeline to have found matches with `stringId` values that map to voice files. Use `--scan-only` first to verify matches are being found.

**monkeyplug not found in WSL**
Make sure monkeyplug is installed inside WSL (not in Windows). Open the Ubuntu terminal and run `pip install monkeyplug`.
