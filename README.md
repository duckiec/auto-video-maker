# Auto Video Maker

Auto Video Maker is a Python app that turns short text content into vertical videos and can upload them to YouTube Shorts or TikTok.

It includes:
- a Flask web dashboard
- a scheduled pipeline runner
- content sourcing (Reddit, Wikipedia, OpenRouter)
- narration generation (Edge TTS)
- subtitle video rendering (Whisper + MoviePy)
- browser automation uploader (Playwright)
- SQLite history logging to avoid duplicate posts

---

## What the app does (end-to-end)

One pipeline run does this:
1. Pick a source (`reddit`, `wiki`, or `ai`) from `config.json`.
2. Fetch/generate narration text.
3. Skip the run if the same normalized text was already uploaded (SHA-256 fingerprint in SQLite).
4. Generate MP3 narration with Edge TTS.
5. Create a 1080x1920 video from your background gameplay clip.
6. Transcribe narration with Whisper and build center subtitles in 1–3 word chunks.
7. Upload with Playwright using saved logged-in browser state (`cookies/*.json`).
8. Log successful uploads to `history.db`.

---

## Repository layout

- `src/app.py` — Flask dashboard (`/`, `/settings`, `/generate-now`, `/health`, `/videos/<file>`)
- `src/bot.py` — scheduler loop + one-shot pipeline CLI
- `src/scrapers.py` — Reddit/Wikipedia/OpenRouter content sources
- `src/audio.py` — TTS MP3 generation
- `src/video.py` — subtitle video rendering
- `src/uploader.py` — YouTube/TikTok upload automation
- `src/db.py` — SQLite schema, duplicate detection, history reads/writes
- `src/config_store.py` — config load/merge/save logic
- `templates/` — dashboard pages
- `config.json` — main runtime config
- `history.db` — upload history database
- `assets/` — put `gameplay.mp4` here
- `output/` — generated audio/video files
- `cookies/` — Playwright storage state files

---

## Requirements

### Required for Docker usage
- Docker + Docker Compose v2

### Required for local (non-Docker) usage
- Python 3.11 recommended
- ffmpeg
- ImageMagick
- Chromium installed for Playwright

Python dependencies are in:
- `requirements.txt` (dev/CI)
- `requirements.docker.txt` (container build)

---

## Easy Quickstart (first successful run)

This path is optimized for getting the app running quickly with Docker.

### 1) Prepare folders and files
From repo root:

- Ensure these folders exist:
  - `assets/`
  - `output/`
  - `cookies/`
- Put a background video at:
  - `assets/gameplay.mp4`

### 2) Create `.env`
Create `/home/runner/work/auto-video-maker/auto-video-maker/.env`.

Minimum recommended:

```env
OPENROUTER_API_KEY=your_openrouter_key
```

If you want Reddit source enabled, also add:

```env
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
# optional override
REDDIT_USER_AGENT=video-factory/1.0 (by u/auto-video-bot)
```

### 3) Add Playwright account state files
Uploader requires saved logged-in browser state files:
- `cookies/youtube_state.json` for YouTube uploads
- `cookies/tiktok_state.json` for TikTok uploads

If either file is missing and that platform is selected, upload fails.

### 4) Build and start
```bash
docker compose build
docker compose up -d
```

### 5) Open dashboard
- `http://localhost:5000`

Use:
- **Generate Now** to trigger a manual run in a background thread
- **Settings** to edit `config.json` from the UI

### 6) Confirm health
- `http://localhost:5000/health` should return `{"status":"ok"}`

### 7) Verify output
After a successful run:
- generated files appear in `output/`
- upload record appears in dashboard history
- files are downloadable via `/videos/<filename>`

---

## Power-user guide

## Run modes

### A) Dashboard mode (recommended for most users)
```bash
python src/app.py
```
Behavior:
- starts Flask server on port `5000`
- initializes DB on requests (except `/health`)
- starts scheduler thread once (on first non-health request)
- allows manual run via `POST /generate-now`

### B) Scheduler-only CLI mode
```bash
python src/bot.py
```
Behavior:
- registers all configured schedule times
- runs forever with `schedule.run_pending()`
- optional immediate first run when `scheduler.run_on_start=true`

### C) One-shot run
```bash
python src/bot.py --run-once
```
Behavior:
- runs one full pipeline pass and exits

---

## Configuration deep dive (`config.json`)

The app merges your file with defaults, so partial configs are valid.
Invalid or malformed config is automatically reset to defaults.

Top-level sections:

- `scheduler`
  - `times`: primary daily run times (`HH:MM`)
  - `extra_times`: additional daily run times
  - `run_on_start`: run once when scheduler starts
  - `recovery_sleep_seconds`: delay after scheduler loop errors

- `paths`
  - `output_dir`
  - `cookies_dir`
  - `background_video`
  - `history_db`

- `scrapers`
  - `selection_pool`: subset/order source pool (`reddit`, `wiki`, `ai`)
  - `reddit`: subreddits + limits
  - `wiki`: min/max summary words
  - `ai`: model and target/min/max words

- `audio`
  - `voice`, `rate`, `volume`

- `video`
  - `whisper_model`
  - `subtitle`: `min_words`, `max_words`, `font_size`, `stroke_width`
  - `output`: `width`, `height`, `fps`

- `uploader`
  - `platform`: `youtube`, `tiktok`, or `random`
  - `headless`
  - `timeout_ms`
  - state filenames: `youtube_state_file`, `tiktok_state_file`
  - `base_tags`

- `api`
  - OpenRouter URL/referer/title
  - default user agents for Reddit/Wiki

You can edit config in:
- UI: `/settings`
- file: `config.json`

---

## Environment variables

### Common
- `CONFIG_PATH` — custom path for config file

### Content
- `OPENROUTER_API_KEY` — required for AI source
- `OPENROUTER_MODEL` — optional model override
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` — required for Reddit source
- `REDDIT_USER_AGENT` — optional override
- `WIKI_USER_AGENT` — optional override

### Media
- `EDGE_TTS_VOICE` — optional voice override
- `WHISPER_MODEL` — optional whisper model override

### Runtime
- `LOG_LEVEL` — logging level for pipeline logger
- `RUN_ON_START` — fallback/override for immediate run behavior
- `SCHEDULER_RECOVERY_SLEEP_SECONDS` — scheduler error backoff
- `UPLOAD_PLATFORM` — fallback uploader platform
- `PLAYWRIGHT_HEADLESS` — fallback uploader browser mode

Notes:
- If `config.json` provides a setting, it is usually primary.
- Some env vars are used as fallback defaults and compatibility overrides.

---

## Upload automation details

Uploader uses Playwright Chromium and `storage_state` auth files.

Platform behavior:
- YouTube: opens Studio, selects upload flow, sets title+tags, advances dialogs, publishes
- TikTok: opens upload page, sets caption, publishes

If selectors/UI change, automation can fail. Typical fix:
- refresh logged-in state file(s)
- rerun in non-headless mode for debugging (`uploader.headless=false`)

---

## Database and duplicate prevention

`history.db` table stores:
- `created_at`
- `source`
- `title`
- `video_filename`
- `content_fingerprint` (unique)
- `content_text`

Duplicate rule:
- fingerprint is SHA-256 of normalized text (trim/collapse whitespace + lowercase)
- if fingerprint already exists, the run is skipped before generation/upload

SQLite connection uses WAL mode and a 5000ms busy timeout for improved reliability under concurrent access.

---

## Docker usage details

### Start
```bash
docker compose build
docker compose up -d
```

### Logs
```bash
docker compose logs -f
```

### Stop
```bash
docker compose down
```

`docker-compose.yml` mounts:
- `./assets -> /video-factory/assets`
- `./output -> /video-factory/output`
- `./cookies -> /video-factory/cookies`
- `./config.json -> /video-factory/config.json`
- `./history.db -> /video-factory/history.db`

Container entrypoint runs:
- `python src/app.py`

---

## CI/CD behavior

Workflow: `.github/workflows/ci-cd.yml`

On PR/push:
1. Installs Python deps
2. Compiles all `src/*.py` modules
3. Runs unit tests (`tests/test_*.py`)
4. Builds Docker image
5. Runs image smoke test (`/health`)

On push to `main`:
- pushes GHCR images:
  - `ghcr.io/<owner>/<repo>:latest`
  - `ghcr.io/<owner>/<repo>:sha-<commit-sha>`

---

## Local validation commands

From repo root:

```bash
python -m py_compile src/config_store.py src/db.py src/scrapers.py src/audio.py src/video.py src/uploader.py src/bot.py src/app.py
python -m unittest discover -s tests -p "test_*.py" -v
```

Optional image smoke test:

```bash
docker build -t local/video-factory:test .
RUN_IMAGE_TESTS=1 IMAGE_UNDER_TEST=local/video-factory:test python -m unittest tests.test_image_smoke -v
```

---

## Troubleshooting

- **Run skips immediately**: duplicate content detected in `history.db`; expected behavior.
- **Reddit failures**: missing/invalid Reddit credentials or no qualifying posts under limits.
- **AI failures**: missing `OPENROUTER_API_KEY` or provider/model issues.
- **No subtitles/video failure**: Whisper/model/media dependency problem.
- **Upload failure**: missing/expired `cookies/*_state.json`, UI selector drift, or platform timeout.
- **Background video error**: missing `assets/gameplay.mp4` (or configured path).
- **Dashboard reachable but no scheduled runs yet**: scheduler thread in `app.py` starts on first non-health request.

---

## Disclaimer

Use responsibly and comply with platform terms, copyright rules, and local regulations.
You are responsible for the content and accounts used by this automation.
