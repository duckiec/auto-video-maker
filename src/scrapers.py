"""Content sourcing functions for autonomous short-video generation.

Phase 1 scope:
- Reddit stories via PRAW
- Wikipedia random facts
- AI story generation via OpenRouter (OpenAI-compatible API)
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import logging
from typing import Any, Callable, Dict, List

import praw
import requests
import wikipediaapi
from dotenv import load_dotenv

from config_store import get_config

load_dotenv()
LOGGER = logging.getLogger(__name__)
MAX_OPENROUTER_MODEL_ATTEMPTS = 2  # initial model + one fallback model
STORY_PERSPECTIVE_OPTIONS = [
    "a first-person confession",
    "a dramatic third-person observer",
    "the antagonist's perspective",
    "a second-person warning",
]
FORBIDDEN_FLOWERY_TERMS = (
    "labyrinthine",
    "ethereal",
    "tapestry",
    "hauntingly",
    "cacophony",
    "delve",
    "whimsical",
    "resonated",
)
MAX_HOOK_WORDS_FOR_3_SECONDS = 45


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


def _extract_sentences(text: str) -> list[str]:
    cleaned = _normalize_text(text)
    if not cleaned:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    return parts


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


def _retry(
    operation: Callable[[], Any], attempts: int = 3, delay_seconds: float = 1.5
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if isinstance(result, str) and not result:
                raise ScraperError("Operation returned empty text")
            return result
        except Exception as error:  # noqa: BLE001 - broad for resilient pipeline wrapper
            last_error = error
            if attempt == attempts:
                break
            time.sleep(delay_seconds * attempt)
    raise ScraperError(f"Operation failed after {attempts} attempts: {last_error}")


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = _normalize_text(content)
    if not text:
        return None

    fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.DOTALL)
    candidate = fenced_match.group(1) if fenced_match else content
    candidate = candidate.strip()

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(candidate[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _call_openrouter_chat(
    *,
    messages: list[dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    retry_attempts: int,
    retry_delay_seconds: float,
) -> str:
    config = get_config()
    api_config = config.get("api", {})
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ScraperError("Missing OPENROUTER_API_KEY in environment.")

    openrouter_base_url = str(
        api_config.get("openrouter_base_url", "https://openrouter.ai/api/v1")
    ).rstrip("/")
    openrouter_referer = api_config.get("openrouter_referer", "https://local.video-factory")
    openrouter_title = api_config.get("openrouter_title", "Auto Video Maker")

    selected_model = model
    fallback_attempted = False
    last_error: Exception | None = None

    for attempt in range(1, max(1, retry_attempts) + 1):
        for _ in range(MAX_OPENROUTER_MODEL_ATTEMPTS):
            payload = {
                "model": selected_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": str(openrouter_referer),
                "X-Title": str(openrouter_title),
            }
            try:
                response = requests.post(
                    f"{openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=45,
                )
            except Exception as error:  # noqa: BLE001
                last_error = ScraperError(f"OpenRouter request transport error: {error}")
                break

            if response.status_code >= 500:
                last_error = ScraperError(
                    f"OpenRouter upstream error ({response.status_code}): {response.text}"
                )
                break

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
                last_error = ScraperError(
                    f"OpenRouter request failed ({response.status_code}): {error_message}"
                )
                break

            completion = response.json()
            if not isinstance(completion, dict):
                last_error = ScraperError("OpenRouter returned a non-object JSON payload.")
                break

            choices = completion.get("choices")
            if not isinstance(choices, list) or not choices:
                last_error = ScraperError("OpenRouter response is missing choices.")
                break

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                last_error = ScraperError("OpenRouter response has invalid choice format.")
                break

            message = first_choice.get("message")
            if not isinstance(message, dict):
                last_error = ScraperError("OpenRouter response is missing message payload.")
                break

            raw_content = message.get("content")
            text = _normalize_text(str(raw_content or ""))
            if not text:
                last_error = ScraperError("OpenRouter returned empty content.")
                break
            return text

        if attempt < max(1, retry_attempts):
            time.sleep(max(0.2, retry_delay_seconds) * attempt)

    raise ScraperError(
        f"OpenRouter call failed after {max(1, retry_attempts)} attempts: {last_error}"
    )


def _validate_story_hook(script: str) -> None:
    sentences = _extract_sentences(script)
    if len(sentences) < 2:
        raise ScraperError("Story must start with at least two hook-ready sentences.")

    hook_text = f"{sentences[0]} {sentences[1]}".strip()
    if _word_count(hook_text) > MAX_HOOK_WORDS_FOR_3_SECONDS:
        raise ScraperError("Hook is too long for the first 3 seconds.")


def _validate_forbidden_vocabulary(script: str) -> None:
    lowered = script.lower()
    blocked = [term for term in FORBIDDEN_FLOWERY_TERMS if term.lower() in lowered]
    if blocked:
        raise ScraperError(f"Story used forbidden flowery vocabulary: {', '.join(blocked)}")


def _sanitize_dialogue_segments(raw_segments: Any, script_fallback: str) -> list[dict[str, str]]:
    if not isinstance(raw_segments, list):
        return [{"speaker": "Narrator", "text": script_fallback}]

    segments: list[dict[str, str]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        speaker = _normalize_text(str(item.get("speaker", "Narrator"))) or "Narrator"
        text = _normalize_text(str(item.get("text", "")))
        if not text:
            continue
        segments.append({"speaker": speaker[:40], "text": text})

    if not segments:
        return [{"speaker": "Narrator", "text": script_fallback}]
    return segments


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


def get_ai_story_package() -> dict[str, Any]:
    """Generate a high-drama 60s script with optional multi-voice dialogue segments."""

    config = get_config()
    ai_config = config.get("scrapers", {}).get("ai", {})

    openrouter_model = os.getenv(
        "OPENROUTER_MODEL",
        ai_config.get("model", "deepseek/deepseek-chat-v3-0324:free"),
    )
    target_ai_words = int(ai_config.get("target_words", 165))
    ai_max_words = int(ai_config.get("max_words", 180))
    ai_min_words = int(ai_config.get("min_words", 150))
    llm_retry_attempts = int(ai_config.get("llm_retry_attempts", 4))
    llm_retry_delay_seconds = float(ai_config.get("llm_retry_delay_seconds", 1.5))
    selected_pov = random.choice(STORY_PERSPECTIVE_OPTIONS)

    system_prompt = (
        "You write viral short-form social drama scripts for voiceover. "
        "The output must feel raw, conversational, human, and emotionally intense. "
        "Never use poetic, flowery, corporate, or AI-sounding language. "
        "Output ONLY valid JSON."
    )
    user_prompt = (
        "Write a high-drama story about messy divorces, explosive family secrets, brutal betrayals, "
        "or shocking confessions.\n"
        f"Narrative perspective for this run: {selected_pov}.\n"
        "STRICT RULES:\n"
        "1) First 1-2 sentences must be an aggressive shocking hook designed for the first 3 seconds.\n"
        "2) Keep the full script between 150 and 180 words for a 60-second voiceover.\n"
        "3) Ban flowery and AI-sounding words. Do not use these terms: "
        f"{', '.join(FORBIDDEN_FLOWERY_TERMS)}.\n"
        "4) Use plain conversational English only.\n"
        "5) End on a cliffhanger or unresolved punchline to trigger comments.\n"
        "Return this exact JSON shape:\n"
        "{\n"
        "  \"script\": \"full script text\",\n"
        "  \"segments\": [\n"
        "    {\"speaker\": \"Narrator\", \"text\": \"line\"},\n"
        "    {\"speaker\": \"Protagonist\", \"text\": \"line\"},\n"
        "    {\"speaker\": \"Antagonist\", \"text\": \"line\"}\n"
        "  ]\n"
        "}\n"
        f"Target about {target_ai_words} words in script."
    )

    response_text = _call_openrouter_chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=str(openrouter_model),
        max_tokens=420,
        temperature=1.05,
        retry_attempts=llm_retry_attempts,
        retry_delay_seconds=llm_retry_delay_seconds,
    )

    parsed = _extract_json_object(response_text)
    if isinstance(parsed, dict):
        script = _normalize_text(str(parsed.get("script", "")))
        if not script:
            script = _normalize_text(response_text)
        segments = _sanitize_dialogue_segments(parsed.get("segments"), script)
    else:
        script = _normalize_text(response_text)
        segments = [{"speaker": "Narrator", "text": script}]

    if not script:
        raise ScraperError("AI story response was empty.")

    word_count = _word_count(script)
    if word_count > ai_max_words:
        script = _truncate_to_words(script, ai_max_words)

    word_count = _word_count(script)
    if word_count < ai_min_words or word_count > ai_max_words:
        raise ScraperError(
            f"AI story length invalid: {word_count} words (expected {ai_min_words}-{ai_max_words})."
        )

    _validate_story_hook(script)
    _validate_forbidden_vocabulary(script)

    return {
        "script": script,
        "segments": segments,
        "pov": selected_pov,
        "source": "ai",
    }


def get_ai_story() -> str:
    """Generate a high-retention 60-second story script using OpenRouter."""

    config = get_config()
    ai_config = config.get("scrapers", {}).get("ai", {})
    llm_retry_attempts = int(ai_config.get("llm_retry_attempts", 4))
    llm_retry_delay_seconds = float(ai_config.get("llm_retry_delay_seconds", 1.5))

    def _fetch() -> str:
        package = get_ai_story_package()
        story = _normalize_text(str(package.get("script", "")))
        if not story:
            raise ScraperError("AI story package returned empty script.")
        return story

    return _retry(
        _fetch,
        attempts=max(1, llm_retry_attempts),
        delay_seconds=max(0.2, llm_retry_delay_seconds),
    )


def generate_story_metadata(script_text: str) -> dict[str, Any]:
    """Generate title, description, and hashtags for an already generated story."""

    config = get_config()
    ai_config = config.get("scrapers", {}).get("ai", {})
    model = str(ai_config.get("metadata_model", ai_config.get("model", "deepseek/deepseek-chat-v3-0324:free")))
    retry_attempts = int(ai_config.get("metadata_retry_attempts", 3))

    cleaned_script = _normalize_text(script_text)
    if not cleaned_script:
        raise ScraperError("Cannot generate metadata from empty script.")

    system_prompt = (
        "You generate short-form viral metadata. "
        "Output ONLY valid JSON with keys: title, description, hashtags."
    )
    user_prompt = (
        "Generate optimized metadata for this story.\n"
        "Rules:\n"
        "- title: 45-80 characters, punchy, curiosity-inducing\n"
        "- description: 1-2 sentences, conversational\n"
        "- hashtags: list of 6-10 hashtags, each prefixed with #\n"
        "Return valid JSON only.\n\n"
        f"Story:\n{cleaned_script}"
    )

    def _generate() -> dict[str, Any]:
        raw = _call_openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            max_tokens=220,
            temperature=0.8,
            retry_attempts=retry_attempts,
            retry_delay_seconds=1.0,
        )
        parsed = _extract_json_object(raw) or {}

        title = _normalize_text(str(parsed.get("title", "")))
        description = _normalize_text(str(parsed.get("description", "")))
        hashtags_raw = parsed.get("hashtags", [])
        hashtags: list[str] = []
        if isinstance(hashtags_raw, list):
            for item in hashtags_raw:
                tag = _normalize_text(str(item))
                if not tag:
                    continue
                if not tag.startswith("#"):
                    stripped = tag.lstrip("#")
                    if not stripped:
                        continue
                    tag = f"#{stripped}"
                if tag not in hashtags:
                    hashtags.append(tag)

        if not title:
            title = _truncate_to_words(cleaned_script, 12).rstrip(".") + "..."
        if not description:
            description = _truncate_to_words(cleaned_script, 28)
        if not hashtags:
            hashtags = ["#shorts", "#story", "#drama", "#viral", "#plottwist", "#confession"]

        return {
            "title": title[:100],
            "description": description[:240],
            "hashtags": hashtags[:10],
        }

    return _retry(_generate, attempts=max(1, retry_attempts), delay_seconds=1.0)


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
