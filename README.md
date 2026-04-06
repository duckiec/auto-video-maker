# Auto Video Maker

Chaotic, vibe-coded content farm with a web dashboard.

It can run 24/7 in Docker and do the whole loop automatically:

1. pull source text from Reddit, Wikipedia, or OpenRouter
2. generate narration with Edge TTS
3. cut gameplay footage to match narration duration
4. burn in word-group subtitles from Whisper timestamps
5. upload to YouTube Shorts or TikTok via saved Playwright sessions
6. log every successful upload into SQLite so duplicates are skipped

## Project Structure

- assets/
- cookies/
- output/
- templates/
- src/
- config.json
- history.db
- requirements.txt
- Dockerfile
- docker-compose.yml
- README.md

Inside src/:

- config_store.py
- db.py
- scrapers.py
- audio.py
- video.py
- uploader.py
- bot.py
- app.py

## Prerequisites

- Docker Desktop with Compose v2
- API keys in .env (OpenRouter, optional Reddit)
- Saved Playwright storage-state files for accounts
- A gameplay background video at assets/gameplay.mp4

If you run outside Docker, also install:

- Python 3.10+
- ffmpeg
- imagemagick

## Folder Setup

1. Ensure folders exist:
  - assets
  - output
  - cookies
2. Drop your background clip at:
  - assets/gameplay.mp4
3. Add cookie storage-state files:
  - cookies/youtube_state.json
  - cookies/tiktok_state.json

## Configuration

Primary runtime config now lives in config.json.

Edit settings in one of two ways:

1. Dashboard settings page at /settings
2. Manual JSON edit in config.json

Examples of configurable values:

- schedule times
- default background video path
- TTS voice/rate/volume
- subreddit list
- OpenRouter model
- uploader platform and headless mode

## Environment Variables (.env)

Create .env in project root.

Required keys:

- OPENROUTER_API_KEY

Optional keys (still supported):

- REDDIT_CLIENT_ID
- REDDIT_CLIENT_SECRET
- REDDIT_USER_AGENT
- OPENROUTER_MODEL
- WIKI_USER_AGENT

Minimal .env example:

OPENROUTER_API_KEY=your_openrouter_key_here
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret

## Run With Docker

1. docker compose build
2. docker compose up -d

Dashboard URL:

- http://localhost:5000

Logs:

1. docker compose logs -f

Stop:

1. docker compose down

## Local Testing Plan

1. Run one forced pipeline execution now:

python src/bot.py --run-once

2. Start scheduler-only mode from CLI:

python src/bot.py

3. Start dashboard mode (includes scheduler thread + manual button):

python src/app.py

4. Open browser:

http://localhost:5000

5. Click Generate Now to trigger background execution without freezing the page.

## Notes

- Duplicate prevention is done with history.db fingerprints of source text.
- History table powers the dashboard feed and video links.
- If uploader selectors break, refresh cookie sessions and re-test.

## Disclaimer

This rig is held together by duct tape, caffeine, and suspiciously confident AI.
If it posts bangers at 3AM, that is feature-complete chaos.
