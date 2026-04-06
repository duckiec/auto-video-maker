# Auto Video Maker

A chaotic, vibe-coded content farm that runs 24/7 in a container and does this loop on autopilot:

1. grabs source text (Reddit, Wikipedia, or AI story)
2. converts it to voiceover
3. slices gameplay footage to match narration length
4. burns in viral word-group subtitles (1-3 words)
5. uploads to YouTube Shorts or TikTok with a headless browser session

It is reckless in spirit, but engineered to fail safely and keep running.

## Current Project Structure

- assets/
- cookies/
- output/
- src/
- requirements.txt
- Dockerfile
- docker-compose.yml
- README.md

Inside src/:

- scrapers.py
- audio.py
- video.py
- uploader.py
- bot.py

## Prerequisites

Local prerequisites:

- Docker Desktop with Compose v2
- A background gameplay file at assets/gameplay.mp4
- API keys for the sources you want enabled
- Saved Playwright storage-state cookie files for upload accounts

If running outside Docker, also install:

- Python 3.10+
- ffmpeg
- imagemagick

## Folder Setup

1. Make sure these folders exist in the project root:
   - assets
   - output
   - cookies
2. Drop your background video at:
   - assets/gameplay.mp4
3. Add Playwright storage state files:
   - cookies/youtube_state.json
   - cookies/tiktok_state.json

## Environment Variables

Create a .env file in the project root.

Required by source type:

- Reddit scraper:
  - REDDIT_CLIENT_ID
  - REDDIT_CLIENT_SECRET
  - optional: REDDIT_USER_AGENT
- AI story scraper (OpenRouter):
  - OPENROUTER_API_KEY
  - optional: OPENROUTER_MODEL
- Upload/browser behavior:
  - UPLOAD_PLATFORM=random|youtube|tiktok
  - PLAYWRIGHT_HEADLESS=true|false
- Pipeline defaults:
  - EDGE_TTS_VOICE=en-US-ChristopherNeural
  - WHISPER_MODEL=base
  - BACKGROUND_VIDEO_PATH=assets/gameplay.mp4
  - OUTPUT_DIR=output
  - COOKIES_DIR=cookies
- Runtime behavior:
  - RUN_ON_START=false
  - EXTRA_SCHEDULE_TIMES=12:30,21:15
  - LOG_LEVEL=INFO
  - SCHEDULER_RECOVERY_SLEEP_SECONDS=5

Example minimal .env:

OPENROUTER_API_KEY=your_openrouter_key_here
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
UPLOAD_PLATFORM=random
PLAYWRIGHT_HEADLESS=true
RUN_ON_START=true

## How To Run In Docker

Build and start:

1. docker compose build
2. docker compose up -d

Check logs:

1. docker compose logs -f

Stop:

1. docker compose down

## Local Testing Plan (Before Server Deploy)

### 1) Validate your inputs

1. Confirm assets/gameplay.mp4 exists.
2. Confirm at least one upload storage-state file exists in cookies/.
3. Confirm .env has keys for at least one scraper source.

### 2) Force a single pipeline run now

Run one immediate execution and exit (no scheduler wait):

python src/bot.py --run-once

This is the fastest integration test of scraper -> audio -> video -> uploader.

### 3) Run scheduler mode locally

python src/bot.py

Scheduler will register 08:00 and 17:00 runs, plus any EXTRA_SCHEDULE_TIMES.

### 4) Read runtime behavior from logs

The bot emits stage logs like:

- [+] Selected source: ...
- [+] Generating voiceover audio...
- [-] Video generation failed, skipping upload: ...

Failures are contained per run so the process stays alive.

## Notes On Stability

- The uploader uses UI selectors that can break when platform UIs change.
- If YouTube/TikTok upload fails, refresh cookies storage-state and retry.
- Whisper first run may download model assets and take longer.

## Disclaimer

This machine is held together by duct tape, caffeine, hope, and several confident AI guesses.
If it ships 30 shorts overnight, it is genius.
If it catches fire, that was always part of the experiment.
# auto-video-maker
