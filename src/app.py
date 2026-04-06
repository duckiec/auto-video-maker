"""Flask dashboard for history, settings, and manual pipeline trigger."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, List

from flask import Flask, redirect, render_template, request, send_from_directory, url_for

from config_store import get_config, save_config
from db import fetch_recent_history, init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "templates"

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

JOB_STATE: dict[str, Any] = {
    "running": False,
    "last_status": "idle",
    "last_message": "No manual run started yet.",
}
JOB_LOCK = threading.Lock()
_SCHEDULER_STARTED = False
LOGGER = logging.getLogger(__name__)


def _load_bot_functions() -> tuple[Callable[[], Any], Callable[[], None]]:
    """Lazily import pipeline entry points so app startup stays lightweight.

    This defers importing heavier pipeline dependencies until they are actually
    needed (scheduler start or manual run).

    Returns:
        Tuple containing (run_pipeline, start_scheduler_loop) callables.
    """
    from bot import run_pipeline, start_scheduler_loop

    return run_pipeline, start_scheduler_loop


def _start_scheduler_thread_once() -> None:
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return

    try:
        _, start_scheduler_loop = _load_bot_functions()
        thread = threading.Thread(target=start_scheduler_loop, daemon=True, name="pipeline-scheduler")
        thread.start()
        _SCHEDULER_STARTED = True
    except Exception as error:  # noqa: BLE001
        LOGGER.exception("Failed to start scheduler thread")
        with JOB_LOCK:
            JOB_STATE["last_status"] = "failed"
            JOB_STATE["last_message"] = f"Scheduler unavailable ({type(error).__name__}): {error}"


def _manual_pipeline_runner() -> None:
    with JOB_LOCK:
        if JOB_STATE["running"]:
            return
        JOB_STATE["running"] = True
        JOB_STATE["last_status"] = "running"
        JOB_STATE["last_message"] = "Pipeline run started..."

    try:
        run_pipeline, _ = _load_bot_functions()
        result = run_pipeline()
        with JOB_LOCK:
            if result is None:
                JOB_STATE["last_status"] = "failed"
                JOB_STATE["last_message"] = "Run finished with no upload (error or duplicate skip)."
            else:
                JOB_STATE["last_status"] = "success"
                if result.platform == "none":
                    JOB_STATE["last_message"] = (
                        f"Generated {Path(result.video_path).name} from {result.source} (upload disabled)."
                    )
                else:
                    JOB_STATE["last_message"] = (
                        f"Uploaded {Path(result.video_path).name} via {result.platform} from {result.source}."
                    )
    except Exception as error:  # noqa: BLE001
        LOGGER.exception("Manual pipeline run crashed")
        with JOB_LOCK:
            JOB_STATE["last_status"] = "failed"
            JOB_STATE["last_message"] = f"Manual run crashed ({type(error).__name__}): {error}"
    finally:
        with JOB_LOCK:
            JOB_STATE["running"] = False


@app.before_request
def _ensure_scheduler_and_db() -> None:
    if request.path == "/health":
        return

    config = get_config()
    db_path = config.get("paths", {}).get("history_db", "history.db")
    init_db(db_path)
    _start_scheduler_thread_once()


@app.get("/health")
def health():
    return {"status": "ok"}, 200


@app.get("/")
def index():
    config = get_config()
    db_path = config.get("paths", {}).get("history_db", "history.db")
    history = fetch_recent_history(limit=100, db_path=db_path)
    path_config = config.get("paths", {})
    output_dir = Path(path_config.get("output_dir", "output"))
    cookies_dir = Path(path_config.get("cookies_dir", "cookies"))
    background_video = Path(path_config.get("background_video", "assets/gameplay.mp4"))

    with JOB_LOCK:
        state = dict(JOB_STATE)

    setup_status = {
        "output_dir_exists": output_dir.exists(),
        "cookies_dir_exists": cookies_dir.exists(),
        "assets_dir_exists": background_video.parent.exists(),
        "background_video_exists": background_video.exists(),
    }
    return render_template(
        "index.html",
        history=history,
        job_state=state,
        config=config,
        setup_status=setup_status,
    )


@app.post("/generate-now")
def generate_now():
    with JOB_LOCK:
        running = JOB_STATE["running"]

    if not running:
        thread = threading.Thread(target=_manual_pipeline_runner, daemon=True, name="manual-run")
        thread.start()

    return redirect(url_for("index"))


@app.get("/settings")
def settings_page():
    config = get_config()
    reddit_credentials_loaded = False
    openrouter_models: List[str] = []
    openrouter_models_error = ""
    try:
        from scrapers import get_openrouter_models, has_reddit_credentials

        reddit_credentials_loaded = has_reddit_credentials()
        openrouter_models = get_openrouter_models()
    except Exception as error:  # noqa: BLE001
        openrouter_models_error = str(error)
    return render_template(
        "settings.html",
        config=config,
        reddit_credentials_loaded=reddit_credentials_loaded,
        openrouter_models=openrouter_models,
        openrouter_models_error=openrouter_models_error,
    )


@app.post("/settings")
def save_settings():
    config = get_config()

    schedule_times_raw = request.form.get("scheduler_times", "08:00,17:00")
    schedule_extra_raw = request.form.get("scheduler_extra_times", "")
    selected_sources = request.form.getlist("selection_pool")
    reddit_subreddits_raw = request.form.get("reddit_subreddits", "AskReddit,AmItheAsshole")
    uploader_tags_raw = request.form.get("uploader_base_tags", "#shorts,#story,#viral")

    config["scheduler"]["times"] = [
        value.strip() for value in schedule_times_raw.split(",") if value.strip()
    ] or ["08:00", "17:00"]
    config["scheduler"]["extra_times"] = [
        value.strip() for value in schedule_extra_raw.split(",") if value.strip()
    ]
    config["scheduler"]["run_on_start"] = request.form.get("run_on_start") == "on"

    config["paths"]["background_video"] = request.form.get(
        "background_video", config["paths"]["background_video"]
    )

    config["audio"]["voice"] = request.form.get("audio_voice", config["audio"]["voice"])
    config["video"]["whisper_model"] = request.form.get(
        "whisper_model", config["video"]["whisper_model"]
    )

    config["scrapers"]["selection_pool"] = [
        value.strip() for value in selected_sources if value.strip() in {"reddit", "wiki", "ai"}
    ] or ["wiki", "ai"]
    config["scrapers"]["reddit"]["subreddits"] = [
        value.strip() for value in reddit_subreddits_raw.split(",") if value.strip()
    ]
    manual_openrouter_model = request.form.get("openrouter_model", "").strip()
    picked_openrouter_model = request.form.get("openrouter_model_picker", "").strip()
    if manual_openrouter_model:
        config["scrapers"]["ai"]["model"] = manual_openrouter_model
    elif picked_openrouter_model:
        config["scrapers"]["ai"]["model"] = picked_openrouter_model

    config["uploader"]["platform"] = request.form.get(
        "uploader_platform", config["uploader"].get("platform", "random")
    )
    config["uploader"]["enabled"] = request.form.get("uploader_enabled") == "on"
    config["uploader"]["headless"] = request.form.get("uploader_headless") == "on"
    config["uploader"]["base_tags"] = [
        value.strip() for value in uploader_tags_raw.split(",") if value.strip()
    ]

    save_config(config)
    return redirect(url_for("settings_page"))


@app.post("/setup/prepare")
def prepare_setup():
    config = get_config()
    path_config = config.get("paths", {})
    output_dir = Path(path_config.get("output_dir", "output"))
    cookies_dir = Path(path_config.get("cookies_dir", "cookies"))
    background_video = Path(path_config.get("background_video", "assets/gameplay.mp4"))
    assets_dir = background_video.parent

    output_dir.mkdir(parents=True, exist_ok=True)
    cookies_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    return redirect(url_for("index"))


@app.post("/setup/upload-background")
def upload_background_asset():
    config = get_config()
    background_video = Path(
        config.get("paths", {}).get("background_video", "assets/gameplay.mp4")
    )
    upload = request.files.get("background_video_file")
    if upload is None or not upload.filename:
        return redirect(url_for("index"))

    background_video.parent.mkdir(parents=True, exist_ok=True)
    upload.save(str(background_video))
    return redirect(url_for("index"))


@app.get("/videos/<path:filename>")
def video_file(filename: str):
    config = get_config()
    output_dir = Path(config.get("paths", {}).get("output_dir", "output")).resolve()
    requested = (output_dir / filename).resolve()
    try:
        requested.relative_to(output_dir)
    except ValueError:
        return {"error": "Invalid path"}, 400
    return send_from_directory(str(output_dir), filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
