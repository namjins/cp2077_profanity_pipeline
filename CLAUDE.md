# CP2077 Profanity Filter Pipeline

## Testing

**Always run tests after making code changes:**
```bash
python -m pytest tests/ -v --tb=short
```

All 64 tests must pass before committing. If a test fails, fix the code or the test before proceeding.

## Key Rules

- Use `rglob("*.ogg")` (single glob), never `rglob("*.Ogg") + rglob("*.ogg")` — Windows is case-insensitive, the double glob duplicates every file
- Always identify files by **full relative depot path**, never by basename alone — basename collisions exist across vo/, vo_holocall/, vo_helmet/ directories
- WolvenKit `uncook` must extract **one file per invocation** — batch uncook produces identical .Ogg content for every file (internal ww2ogg shared-buffer bug). Concurrent single-file invocations with ThreadPoolExecutor are safe.
- sound2wem must be invoked in **batches** (never one file per invocation) with pre-batch cleanup — sequential single-file invocations cause Wwise to produce duplicate/stale output
- Always pass `--samplerate:48000` to sound2wem — CP2077 expects 48 kHz audio
- Always clean `wem_files/`, `processed_ogg/`, `processed_wem/` before a fresh audio pipeline run — stale cached files from broken runs propagate silently through the entire pipeline
- Use `atomic_write()` from fileutil.py for all file output that must survive crashes
- For git commits, switch the model to haiku first

## Project Structure

- `cp2077_profanity/` — pipeline source code
- `tests/` — pytest test suite
- `.claude/agents/` — specialized agents for audio, WolvenKit, QA, etc.
- `config.toml` — runtime configuration
- `profanity_list.txt` — wordlist driving all matching
