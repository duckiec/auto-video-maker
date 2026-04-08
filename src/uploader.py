"""Headless upload automation for YouTube Shorts and TikTok.

Phase 4 scope:
- Use Playwright with saved storage state in cookies/
- Upload generated video files
- Fill title/hashtags and publish
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page, TimeoutError, sync_playwright

from config_store import get_config

load_dotenv()

YOUTUBE_UPLOAD_URL = "https://studio.youtube.com"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/upload"

YOUTUBE_STATE_FILE = "youtube_state.json"
TIKTOK_STATE_FILE = "tiktok_state.json"

DEFAULT_TIMEOUT_MS = 120_000


class UploadError(RuntimeError):
    """Raised when automated upload fails."""


@dataclass
class UploadResult:
    platform: str
    video_path: str
    title: str


def _ensure_file_exists(path: str | os.PathLike[str], label: str) -> Path:
    resolved = Path(path)
    if not resolved.exists():
        raise UploadError(f"{label} not found: {resolved}")
    return resolved


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _generate_title(source_text: str, max_len: int = 90) -> str:
    cleaned = _normalize_text(source_text)
    if not cleaned:
        cleaned = "Wild story you need to hear"

    words = cleaned.split()
    sample = " ".join(words[:12])
    title = f"{sample}..."
    if len(title) > max_len:
        title = title[: max_len - 3].rstrip() + "..."
    return title


def _extract_tags(source_text: str, limit: int = 4) -> str:
    config = get_config()
    base_tags = config.get("uploader", {}).get("base_tags", ["#shorts", "#story", "#viral"])

    tokens = re.findall(r"[A-Za-z]{4,}", source_text.lower())
    unique = []
    for token in tokens:
        if token in unique:
            continue
        unique.append(token)
        if len(unique) >= limit:
            break

    dynamic_tags = [f"#{tag}" for tag in unique]
    combined = base_tags + dynamic_tags
    return " ".join(combined[: max(3, limit + 2)])


def _find_first(page: Page, selectors: list[str]):
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _youtube_upload(
    context: BrowserContext,
    video_path: Path,
    title: str,
    tags: str,
    timeout_ms: int,
) -> None:
    page = context.new_page()
    page.set_default_timeout(timeout_ms)
    page.goto(YOUTUBE_UPLOAD_URL, wait_until="domcontentloaded")

    page.wait_for_timeout(3000)

    create_button = _find_first(
        page,
        selectors=[
            'button[aria-label*="Create"]',
            'ytcp-button#create-icon',
            'tp-yt-paper-button:has-text("Create")',
        ],
    )
    if create_button is not None:
        create_button.click()

    upload_menu = _find_first(
        page,
        selectors=[
            'tp-yt-paper-item:has-text("Upload videos")',
            'ytcp-ve:has-text("Upload videos")',
        ],
    )
    if upload_menu is not None:
        upload_menu.click()

    file_input = _find_first(
        page,
        selectors=[
            'input[type="file"]',
            'input[name="Filedata"]',
        ],
    )
    if file_input is None:
        raise UploadError("YouTube file input was not found. Session may be logged out.")
    file_input.set_input_files(str(video_path))

    title_box = _find_first(
        page,
        selectors=[
            'div[aria-label^="Add a title"]',
            '#textbox[aria-label^="Add a title"]',
            'ytcp-social-suggestions-textbox #textbox',
        ],
    )
    if title_box is not None:
        title_box.click()
        title_box.fill("")
        title_box.type(f"{title} {tags}")

    for _ in range(3):
        next_button = _find_first(
            page,
            selectors=[
                'ytcp-button#next-button button',
                'button:has-text("Next")',
            ],
        )
        if next_button is None:
            break
        next_button.click()
        page.wait_for_timeout(900)

    not_kids_radio = _find_first(
        page,
        selectors=[
            'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
            'tp-yt-paper-radio-button:has-text("No, it\'s not made for kids")',
        ],
    )
    if not_kids_radio is not None:
        not_kids_radio.click()

    publish_button = _find_first(
        page,
        selectors=[
            'ytcp-button#done-button button',
            'button:has-text("Publish")',
            'button:has-text("Done")',
        ],
    )
    if publish_button is None:
        raise UploadError("YouTube publish button not found.")

    publish_button.click()
    page.wait_for_timeout(2500)


def _tiktok_upload(
    context: BrowserContext,
    video_path: Path,
    title: str,
    tags: str,
    timeout_ms: int,
) -> None:
    page = context.new_page()
    page.set_default_timeout(timeout_ms)
    page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded")

    page.wait_for_timeout(3000)

    file_input = _find_first(
        page,
        selectors=[
            'input[type="file"]',
            'input[accept*="video"]',
        ],
    )
    if file_input is None:
        raise UploadError("TikTok file input was not found. Session may be logged out.")
    file_input.set_input_files(str(video_path))

    caption_box = _find_first(
        page,
        selectors=[
            'div[contenteditable="true"]',
            'textarea[placeholder*="caption"]',
            'div.public-DraftEditor-content',
        ],
    )
    if caption_box is not None:
        caption_box.click()
        try:
            caption_box.fill("")
        except Exception:
            pass
        caption_box.type(f"{title} {tags}")

    page.wait_for_timeout(2000)

    publish_button = _find_first(
        page,
        selectors=[
            'button:has-text("Post")',
            'button:has-text("Publish")',
        ],
    )
    if publish_button is None:
        raise UploadError("TikTok publish button not found.")

    publish_button.click()
    page.wait_for_timeout(3000)


def upload_video(
    video_path: str | os.PathLike[str],
    source_text: str,
    platform: str = "youtube",
    cookies_dir: str | os.PathLike[str] = "cookies",
    headless: bool = True,
    custom_title: str | None = None,
    custom_tags: str | None = None,
) -> UploadResult:
    """Upload a generated video using saved Playwright storage state."""

    config = get_config()
    uploader_config = config.get("uploader", {})
    path_config = config.get("paths", {})

    valid_platforms = {"youtube", "tiktok"}
    selected_platform = platform.lower().strip() if platform else str(uploader_config.get("platform", "random"))
    if selected_platform == "random":
        selected_platform = random.choice(["youtube", "tiktok"])
    if selected_platform not in valid_platforms:
        raise UploadError(f"Unsupported platform: {platform}")

    video_file = _ensure_file_exists(video_path, "Video file")
    resolved_cookies_dir = cookies_dir if cookies_dir != "cookies" else path_config.get("cookies_dir", "cookies")
    cookies_path = Path(resolved_cookies_dir)
    cookies_path.mkdir(parents=True, exist_ok=True)

    timeout_ms = int(uploader_config.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    youtube_state_file = str(uploader_config.get("youtube_state_file", YOUTUBE_STATE_FILE))
    tiktok_state_file = str(uploader_config.get("tiktok_state_file", TIKTOK_STATE_FILE))

    state_file = (
        cookies_path / youtube_state_file
        if selected_platform == "youtube"
        else cookies_path / tiktok_state_file
    )
    _ensure_file_exists(state_file, "Storage state file")

    title = _normalize_text(custom_title) if custom_title else _generate_title(source_text)
    tags = _normalize_text(custom_tags) if custom_tags else _extract_tags(source_text)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(state_file))

            try:
                if selected_platform == "youtube":
                    _youtube_upload(
                        context=context,
                        video_path=video_file,
                        title=title,
                        tags=tags,
                        timeout_ms=timeout_ms,
                    )
                else:
                    _tiktok_upload(
                        context=context,
                        video_path=video_file,
                        title=title,
                        tags=tags,
                        timeout_ms=timeout_ms,
                    )
            finally:
                context.close()
                browser.close()

        return UploadResult(platform=selected_platform, video_path=str(video_file), title=title)
    except TimeoutError as error:
        raise UploadError(f"Upload timed out on {selected_platform}: {error}") from error
    except Exception as error:  # noqa: BLE001
        raise UploadError(f"Upload failed on {selected_platform}: {error}") from error


def upload_video_random_platform(
    video_path: str | os.PathLike[str],
    source_text: str,
    cookies_dir: str | os.PathLike[str] = "cookies",
    headless: bool = True,
    custom_title: str | None = None,
    custom_tags: str | None = None,
) -> UploadResult:
    """Upload to a randomly selected supported platform."""

    config = get_config()
    uploader_config = config.get("uploader", {})
    default_platform = str(uploader_config.get("platform", "random"))
    platform = random.choice(["youtube", "tiktok"]) if default_platform == "random" else default_platform
    return upload_video(
        video_path=video_path,
        source_text=source_text,
        platform=platform,
        cookies_dir=cookies_dir,
        headless=headless,
        custom_title=custom_title,
        custom_tags=custom_tags,
    )
