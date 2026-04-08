"""Autonomous pipeline orchestrator.

Phase 5 scope:
- In-process scheduling with schedule library (no OS cron)
- End-to-end pipeline execution with resilient error handling
- Continuous loop suitable for long-running containers
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import schedule
from dotenv import load_dotenv

from config_store import get_config

load_dotenv()


@dataclass
class PipelineResult:
    source: str
    audio_path: str
    video_path: str
    platform: str


def _pirate_log(message: str) -> None:
    print(message, flush=True)


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _choose_scraper() -> tuple[str, Callable[[], object]]:
    from scrapers import get_ai_story_package, get_reddit_story, get_wiki_fact, has_reddit_credentials

    config = get_config()
    selection_pool = config.get("scrapers", {}).get("selection_pool", ["reddit", "wiki", "ai"])

    sources: dict[str, Callable[[], object]] = {
        "reddit": get_reddit_story,
        "wiki": get_wiki_fact,
        "ai": get_ai_story_package,
    }
    available = [name for name in selection_pool if name in sources]
    if not available:
        available = list(sources.keys())
    random.shuffle(available)
    for name in available:
        if name == "reddit" and not has_reddit_credentials():
            logging.getLogger("pipeline").warning(
                "Missing Reddit credentials, falling back to next source..."
            )
            continue
        return name, sources[name]
    raise RuntimeError("No available scraper source in selection pool.")


def _safe_trim(text: str, max_chars: int = 120) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def run_pipeline(
    progress_callback: Callable[[str, str, int], None] | None = None,
) -> PipelineResult | None:
    """Run one full content-to-upload pipeline pass.

    Returns a PipelineResult on success, or None on failure.
    Exceptions are intentionally contained so the scheduler loop keeps running.
    """

    from audio import generate_voiceover
    from db import has_content_fingerprint, init_db, log_history_entry
    from scrapers import generate_story_metadata
    from uploader import upload_video, upload_video_random_platform
    from video import generate_video

    logger = logging.getLogger("pipeline")
    config = get_config()
    path_config = config.get("paths", {})
    uploader_config = config.get("uploader", {})
    video_config = config.get("video", {})
    audio_config = config.get("audio", {})

    _pirate_log("[+] Pipeline run started")
    logger.info("Pipeline run started")

    def _emit(stage: str, message: str, progress: int) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(stage, message, progress)
        except Exception:  # noqa: BLE001
            logger.exception("Progress callback failed for stage=%s", stage)

    output_dir = path_config.get("output_dir", "output")
    assets_video = path_config.get("background_video", "assets/gameplay.mp4")
    cookies_dir = path_config.get("cookies_dir", "cookies")
    history_db = path_config.get("history_db", "history.db")
    assets_dir = str(Path(assets_video).expanduser().parent)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(cookies_dir).mkdir(parents=True, exist_ok=True)
    Path(assets_dir).mkdir(parents=True, exist_ok=True)
    init_db(history_db)

    try:
        _emit("fetching_script", "Fetching script and selecting source...", 15)
        scraper_name, scraper_func = _choose_scraper()
        _pirate_log(f"[+] Selected source: {scraper_name}")
        logger.info("Selected source=%s", scraper_name)
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Failed to select scraper: {error}")
        logger.exception("Failed to select scraper: %s", error)
        _emit("error", f"Failed to select scraper: {error}", 100)
        return None

    dialogue_segments: list[dict[str, str]] | None = None
    metadata: dict[str, object] = {}

    try:
        scraper_output = scraper_func()
        source_text = scraper_output if isinstance(scraper_output, str) else ""
        if scraper_name == "ai":
            try:
                ai_package = scraper_output if isinstance(scraper_output, dict) else {}
                package_text = " ".join(str(ai_package.get("script", "")).split()).strip()
                source_text = package_text or source_text
                raw_segments = ai_package.get("segments")
                if isinstance(raw_segments, list):
                    dialogue_segments = [
                        {
                            "speaker": " ".join(str(item.get("speaker", "Narrator")).split()).strip()
                            or "Narrator",
                            "text": " ".join(str(item.get("text", "")).split()).strip(),
                        }
                        for item in raw_segments
                        if isinstance(item, dict) and " ".join(str(item.get("text", "")).split()).strip()
                    ]
            except Exception as package_error:  # noqa: BLE001
                logger.warning("AI dialogue package fallback to plain script: %s", package_error)

        source_text = " ".join((source_text or "").split()).strip()
        if not source_text:
            raise RuntimeError("Generated script was empty.")

        if scraper_name == "ai":
            try:
                metadata = generate_story_metadata(source_text)
            except Exception as metadata_error:  # noqa: BLE001
                logger.warning("Metadata generation failed; uploader fallback will be used: %s", metadata_error)
                metadata = {}

        _pirate_log(f"[+] Scraped story/fact: {_safe_trim(source_text)}")
        logger.info("Scraped text preview: %s", _safe_trim(source_text))
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Scraper failed ({scraper_name}), skipping run: {error}")
        logger.exception("Scraper step failed (%s): %s", scraper_name, error)
        _emit("error", f"Script fetching failed ({scraper_name}): {error}", 100)
        return None

    if has_content_fingerprint(source_text, db_path=history_db):
        _pirate_log("[-] Duplicate content detected in history.db. Skipping generation.")
        logger.info("Duplicate content skipped for source=%s", scraper_name)
        _emit("error", "Duplicate content detected. Run skipped.", 100)
        return None

    try:
        _emit("generating_audio", "Generating voiceover audio...", 45)
        _pirate_log("[+] Generating voiceover audio...")
        audio_path = generate_voiceover(
            text=source_text,
            output_dir=output_dir,
            voice=audio_config.get("voice", os.getenv("EDGE_TTS_VOICE", "en-US-ChristopherNeural")),
            rate=audio_config.get("rate", "+8%"),
            volume=audio_config.get("volume", "+0%"),
            pitch=audio_config.get("pitch", "+2Hz"),
            dialogue_segments=dialogue_segments,
        )
        _pirate_log(f"[+] Audio generated: {audio_path}")
        logger.info("Audio generated: %s", audio_path)
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Failed to generate audio, skipping run: {error}")
        logger.exception("Audio generation failed: %s", error)
        _emit("error", f"Audio generation failed: {error}", 100)
        return None

    try:
        _emit("rendering_video", "Rendering final video...", 75)
        _pirate_log("[+] Building subtitle video from gameplay + narration...")
        video_path = generate_video(
            audio_path=audio_path,
            background_video_path=assets_video,
            output_dir=output_dir,
            whisper_model_name=video_config.get("whisper_model", os.getenv("WHISPER_MODEL", "base")),
        )
        _pirate_log(f"[+] Video rendered: {video_path}")
        logger.info("Video generated: %s", video_path)
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Video generation failed, skipping upload: {error}")
        logger.exception("Video generation failed: %s", error)
        _emit("error", f"Video rendering failed: {error}", 100)
        return None

    uploads_enabled = bool(uploader_config.get("enabled", True))
    upload_platform = str(uploader_config.get("platform", os.getenv("UPLOAD_PLATFORM", "random"))).strip().lower()
    headless_mode = bool(uploader_config.get("headless", os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"))
    upload_result = None
    metadata_title = str(metadata.get("title", "")).strip() if isinstance(metadata, dict) else ""
    metadata_tags = " ".join(
        [
            str(tag).strip()
            for tag in (
                metadata.get("hashtags", [])
                if isinstance(metadata, dict) and isinstance(metadata.get("hashtags"), list)
                else []
            )
            if str(tag).strip()
        ]
    )
    try:
        _emit("uploading", "Uploading video...", 90)
        if not uploads_enabled:
            _pirate_log("[+] Upload stage skipped (uploader.enabled=false)")
            logger.info("Upload skipped because uploader.enabled=false")
        else:
            _pirate_log(f"[+] Upload stage started (platform mode: {upload_platform})")
            if upload_platform in {"youtube", "tiktok"}:
                upload_result = upload_video(
                    video_path=video_path,
                    source_text=source_text,
                    platform=upload_platform,
                    cookies_dir=cookies_dir,
                    headless=headless_mode,
                    custom_title=metadata_title or None,
                    custom_tags=metadata_tags or None,
                )
            else:
                upload_result = upload_video_random_platform(
                    video_path=video_path,
                    source_text=source_text,
                    cookies_dir=cookies_dir,
                    headless=headless_mode,
                    custom_title=metadata_title or None,
                    custom_tags=metadata_tags or None,
                )

            _pirate_log(
                f"[+] Upload successful on {upload_result.platform} | title: {upload_result.title}"
            )
            logger.info(
                "Upload successful: platform=%s title=%s",
                upload_result.platform,
                upload_result.title,
            )
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Upload failed, will retry next schedule: {error}")
        logger.exception("Upload step failed: %s", error)
        _emit("error", f"Upload failed: {error}", 100)
        return None

    try:
        created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        entry_title = upload_result.title if upload_result is not None else _safe_trim(source_text, max_chars=90)
        log_history_entry(
            created_at=created_at,
            source=scraper_name,
            title=entry_title,
            video_filename=Path(video_path).name,
            content_text=source_text,
            db_path=history_db,
        )
        _pirate_log("[+] History logged to SQLite memory bank")
    except Exception as error:  # noqa: BLE001
        _pirate_log(f"[-] Failed to log history entry (non-fatal): {error}")
        logger.exception("History log failed: %s", error)

    _pirate_log("[+] Pipeline run completed successfully")
    logger.info("Pipeline run completed successfully")
    _emit("complete", "Video generation completed successfully.", 100)
    return PipelineResult(
        source=scraper_name,
        audio_path=audio_path,
        video_path=video_path,
        platform=upload_result.platform if upload_result is not None else "none",
    )


def _register_default_schedules() -> None:
    config = get_config()
    scheduler = config.get("scheduler", {})
    primary_times = scheduler.get("times", ["08:00", "17:00"])
    extra_times = scheduler.get("extra_times", [])
    all_times = [*primary_times, *extra_times]

    for time_value in all_times:
        try:
            schedule.every().day.at(str(time_value)).do(run_pipeline)
            logging.getLogger("pipeline").info("Registered schedule time: %s", time_value)
        except schedule.ScheduleValueError:
            logging.getLogger("pipeline").warning(
                "Skipping invalid schedule value: %s", time_value
            )


def start_scheduler_loop() -> None:
    """Run the long-lived scheduler loop for containerized execution."""

    _configure_logging()
    logger = logging.getLogger("pipeline")

    _register_default_schedules()
    _pirate_log("[+] Scheduler armed for 08:00 and 17:00 daily runs")
    logger.info("Scheduler initialized. Waiting for scheduled runs.")

    config = get_config()
    scheduler = config.get("scheduler", {})

    run_on_start = bool(scheduler.get("run_on_start", os.getenv("RUN_ON_START", "false").lower() == "true"))
    if run_on_start:
        _pirate_log("[+] RUN_ON_START enabled, executing immediate run")
        logger.info("RUN_ON_START enabled; executing immediate pipeline run")
        run_pipeline()

    while True:
        try:
            schedule.run_pending()
        except Exception as error:  # noqa: BLE001
            recovery_sleep = int(
                scheduler.get(
                    "recovery_sleep_seconds",
                    os.getenv("SCHEDULER_RECOVERY_SLEEP_SECONDS", "5"),
                )
            )
            _pirate_log(
                f"[-] Scheduler loop error: {error}. Sleeping {recovery_sleep}s and continuing."
            )
            logger.exception("Unexpected scheduler error: %s", error)
            time.sleep(recovery_sleep)
        time.sleep(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous video pipeline scheduler")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one immediate pipeline execution and exit",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _configure_logging()
    if args.run_once:
        _pirate_log("[+] Forced one-shot pipeline run requested")
        run_pipeline()
    else:
        start_scheduler_loop()
