"""Content sourcing functions for autonomous short-video generation.

Phase 1 scope:
- Reddit stories via PRAW
- Wikipedia random facts
- AI story generation via OpenRouter (OpenAI-compatible API)
"""

from __future__ import annotations

import os
import random
import re
import time
import logging
from typing import Callable, Dict, List

import praw
import requests
import wikipediaapi
from dotenv import load_dotenv

from config_store import get_config

load_dotenv()
LOGGER = logging.getLogger(__name__)
MAX_OPENROUTER_MODEL_ATTEMPTS = 2  # initial model + one fallback model
# Point of view (POV) perspective styles used for generated narratives.
STORY_PERSPECTIVE_OPTIONS = [
    "a first-person confession (example tone: 'I found out...')",
    "a dramatic third-person observer perspective (example tone: 'She never knew that...')",
    "a second-person warning (example tone: 'If your husband does this, run...')",
    "the perspective of the villain or antagonist in the drama",
]

class ScraperError(RuntimeError):
    """Raised when a scraper cannot return valid content."""


def has_reddit_credentials() -> bool:
    """Return True if both REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set, else False."""
    return bool(os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET"))


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned


def _word_count(text: str) -> int:
    return len(_normalize_text(text).split())


def _truncate_to_words(text: str, max_words: int) -> str:
    words = _normalize_text(text).split()
    return " ".join(words[:max_words])


def _is_openrouter_model_unavailable_error(error: Exception, model_name: str) -> bool:
    """Return True when an error indicates the specified OpenRouter model has no active endpoints."""
    message = str(error)
    return "No endpoints found for" in message and model_name in message


def _pick_openrouter_fallback_model(current_model: str) -> str | None:
    """Pick an alternate OpenRouter model; prefer free-tier fallback for free current model, otherwise any."""
    try:
        available_models = get_openrouter_models()
    except Exception:  # noqa: BLE001 - non-fatal fallback probe
        return None

    if not available_models:
        return None

    free_only = current_model.endswith(":free")
    candidates = [model for model in available_models if model != current_model]
    if free_only:
        free_candidates = [model for model in candidates if model.endswith(":free")]
        if free_candidates:
            return free_candidates[0]
    return candidates[0] if candidates else None


def _extract_openrouter_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return (response.text or "").strip() or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            message = str(error_obj.get("message", "")).strip()
            if message:
                return message
    return str(payload)


def _retry(operation: Callable[[], str], attempts: int = 3, delay_seconds: float = 1.5) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if not result:
                raise ScraperError("Operation returned empty text")
            return result
        except Exception as error:  # noqa: BLE001 - broad for resilient pipeline wrapper
            last_error = error
            if attempt == attempts:
                break
            time.sleep(delay_seconds * attempt)
    raise ScraperError(f"Operation failed after {attempts} attempts: {last_error}")


def get_reddit_story() -> str:
    """Fetch a short Reddit story from top daily posts under 200 words."""

    config = get_config()
    scraper_config = config.get("scrapers", {}).get("reddit", {})
    api_config = config.get("api", {})

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    reddit_user_agent = os.getenv(
        "REDDIT_USER_AGENT",
        api_config.get("reddit_user_agent", "video-factory/1.0 (by u/auto-video-bot)"),
    )
    subreddit_names = scraper_config.get("subreddits", ["AskReddit", "AmItheAsshole"])
    post_limit = int(scraper_config.get("post_limit", 50))
    max_reddit_words = int(scraper_config.get("max_words", 200))
    time_filter = str(scraper_config.get("time_filter", "day"))

    if not has_reddit_credentials():
        raise ScraperError(
            "Missing Reddit credentials. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."
        )

    def _fetch() -> str:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=reddit_user_agent,
            check_for_async=False,
        )

        candidates: List[str] = []
        for subreddit_name in subreddit_names:
            subreddit = reddit.subreddit(subreddit_name)
            for post in subreddit.top(time_filter=time_filter, limit=post_limit):
                if post.stickied or post.over_18:
                    continue

                body = _normalize_text(getattr(post, "selftext", ""))
                title = _normalize_text(getattr(post, "title", ""))

                text = body if body else title
                words = _word_count(text)

                if not text or words == 0:
                    continue
                if words <= max_reddit_words:
                    candidates.append(text)

        if not candidates:
            raise ScraperError("No suitable Reddit posts found under configured max words.")

        return random.choice(candidates)

    return _retry(_fetch)


def get_wiki_fact() -> str:
    """Fetch a random Wikipedia summary suitable for short narration."""

    config = get_config()
    scraper_config = config.get("scrapers", {}).get("wiki", {})
    api_config = config.get("api", {})

    wiki_user_agent = os.getenv(
        "WIKI_USER_AGENT",
        api_config.get("wiki_user_agent", "video-factory/1.0 (contact: local)"),
    )
    wiki_max_words = int(scraper_config.get("max_words", 120))
    wiki_min_words = int(scraper_config.get("min_words", 15))

    def _fetch() -> str:
        session = requests.Session()
        session.headers.update({"User-Agent": wiki_user_agent})

        response = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "random",
                "rnnamespace": 0,
                "rnlimit": 1,
            },
            timeout=15,
        )
        response.raise_for_status()

        random_title = (
            response.json()
            .get("query", {})
            .get("random", [{}])[0]
            .get("title", "")
        )
        if not random_title:
            raise ScraperError("Wikipedia random title lookup returned empty result.")

        wiki = wikipediaapi.Wikipedia(user_agent=wiki_user_agent, language="en")
        page = wiki.page(random_title)

        if not page.exists() or not page.summary:
            raise ScraperError("Wikipedia page has no summary.")

        summary = _normalize_text(page.summary)
        if _word_count(summary) > wiki_max_words:
            summary = _truncate_to_words(summary, wiki_max_words)

        if _word_count(summary) < wiki_min_words:
            raise ScraperError("Wikipedia summary too short to be useful.")

        return summary

    return _retry(_fetch, attempts=4, delay_seconds=1.2)


def get_ai_story() -> str:
    """Generate a high-retention ~100-word story using OpenRouter."""

    config = get_config()
    ai_config = config.get("scrapers", {}).get("ai", {})
    api_config = config.get("api", {})

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ScraperError("Missing OPENROUTER_API_KEY in environment.")

    openrouter_base_url = api_config.get("openrouter_base_url", "https://openrouter.ai/api/v1")
    openrouter_model = os.getenv(
        "OPENROUTER_MODEL",
        ai_config.get("model", "deepseek/deepseek-chat-v3-0324:free"),
    )
    target_ai_words = int(ai_config.get("target_words", 100))
    ai_max_words = int(ai_config.get("max_words", 140))
    ai_min_words = int(ai_config.get("min_words", 60))
    openrouter_referer = api_config.get("openrouter_referer", "https://local.video-factory")
    openrouter_title = api_config.get("openrouter_title", "Auto Video Maker")
    selected_model = openrouter_model
    fallback_attempted = False
    selected_pov = random.choice(STORY_PERSPECTIVE_OPTIONS)

    def _fetch() -> str:
        nonlocal selected_model, fallback_attempted
        base_url = str(openrouter_base_url).rstrip("/")
        chat_url = f"{base_url}/chat/completions"
        for _ in range(MAX_OPENROUTER_MODEL_ATTEMPTS):
            payload = {
                "model": selected_model,
                "temperature": 1.0,
                "max_tokens": 220,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You write punchy, high-retention short-form stories for voiceover narration. "
                            "Output only plain text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Write a short, gripping narrative story (about 100-150 words) designed "
                            "for a viral short-form video (TikTok/YouTube Shorts). "
                            "Focus on high-drama, relatable themes with universal appeal, such as: "
                            "family secrets, relationship betrayals, dramatic divorces, hidden "
                            "identities, or shocking confessions. "
                            "The story must have a strong hook in the first sentence and a dramatic "
                            "or unresolved ending to encourage comments. "
                            f"Write from {selected_pov}. "
                            f"Target around {target_ai_words} words and output only plain text."
                        ),
                    },
                ],
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": openrouter_referer,
                "X-Title": openrouter_title,
            }
            response = requests.post(
                chat_url,
                headers=headers,
                json=payload,
                timeout=45,
            )
            if response.status_code >= 400:
                error_message = _extract_openrouter_error_message(response)
                if (
                    response.status_code == 404
                    and not fallback_attempted
                    and _is_openrouter_model_unavailable_error(
                        ScraperError(error_message), selected_model
                    )
                ):
                    fallback_model = _pick_openrouter_fallback_model(selected_model)
                    if fallback_model:
                        LOGGER.warning(
                            "OpenRouter model '%s' unavailable, retrying with fallback '%s'.",
                            selected_model,
                            fallback_model,
                        )
                        selected_model = fallback_model
                        fallback_attempted = True
                        continue
                raise ScraperError(
                    f"OpenRouter request failed ({response.status_code}): {error_message}"
                )
            completion = response.json()
            break

        if not isinstance(completion, dict):
            raise ScraperError("OpenRouter returned a non-object JSON payload.")
        choices = completion.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ScraperError("OpenRouter response is missing choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ScraperError("OpenRouter response has invalid choice format.")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ScraperError("OpenRouter response is missing message payload.")
        raw_content = message.get("content")
        text = _normalize_text(str(raw_content or "").strip())
        if not text:
            raise ScraperError("OpenRouter returned empty content.")

        word_count = _word_count(text)
        if word_count > ai_max_words:
            text = _truncate_to_words(text, ai_max_words)

        if _word_count(text) < ai_min_words:
            raise ScraperError("AI story was too short.")

        return text

    return _retry(_fetch)


def get_openrouter_models() -> List[str]:
    """Return available OpenRouter model identifiers sorted alphabetically."""

    config = get_config()
    api_config = config.get("api", {})
    openrouter_base_url = str(
        api_config.get("openrouter_base_url", "https://openrouter.ai/api/v1")
    ).rstrip("/")
    models_url = f"{openrouter_base_url}/models"

    response = requests.get(models_url, timeout=20)
    response.raise_for_status()

    payload = response.json()
    entries = payload.get("data", []) if isinstance(payload, dict) else []

    model_ids: List[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if model_id:
            model_ids.append(model_id)

    deduped = sorted(set(model_ids), key=str.lower)
    return deduped


def get_random_content() -> str:
    """Pick one source function at random and return narration text."""

    config = get_config()
    selection_pool = config.get("scrapers", {}).get("selection_pool", ["reddit", "wiki", "ai"])

    sources: Dict[str, Callable[[], str]] = {
        "reddit": get_reddit_story,
        "wiki": get_wiki_fact,
        "ai": get_ai_story,
    }
    available = [name for name in selection_pool if name in sources]
    if not available:
        available = list(sources.keys())

    random.shuffle(available)
    for source_name in available:
        if source_name == "reddit" and not has_reddit_credentials():
            LOGGER.warning("Missing Reddit credentials, falling back to next source...")
            continue
        return sources[source_name]()

    raise ScraperError("No available content source could be selected from the configured pool.")
