"""Audio generation utilities using Microsoft Edge TTS.

Phase 2 scope:
- Convert narration text to MP3 using edge-tts
- Provide a simple sync API for orchestration modules
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import edge_tts
from dotenv import load_dotenv

from config_store import get_config

load_dotenv()

DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"


class AudioGenerationError(RuntimeError):
    """Raised when TTS generation fails."""


def _validate_text(text: str) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        raise AudioGenerationError("Narration text is empty.")
    return cleaned


def _build_output_path(output_dir: str | os.PathLike[str], prefix: str = "voice") -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return directory / f"{prefix}_{timestamp}.mp3"


async def _synthesize_async(
    text: str,
    output_path: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = DEFAULT_VOLUME,
) -> Path:
    communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicator.save(str(output_path))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioGenerationError("TTS finished but output file is missing or empty.")

    return output_path


def generate_voiceover(
    text: str,
    output_dir: str | os.PathLike[str] = "output",
    voice: str | None = None,
    rate: str = DEFAULT_RATE,
    volume: str = DEFAULT_VOLUME,
) -> str:
    """Generate an MP3 voiceover from text and return the output file path."""

    cleaned_text = _validate_text(text)
    config = get_config()
    audio_config = config.get("audio", {})

    chosen_voice = voice or os.getenv("EDGE_TTS_VOICE", audio_config.get("voice", DEFAULT_VOICE))
    chosen_rate = rate if rate != DEFAULT_RATE else audio_config.get("rate", DEFAULT_RATE)
    chosen_volume = volume if volume != DEFAULT_VOLUME else audio_config.get("volume", DEFAULT_VOLUME)
    output_path = _build_output_path(output_dir=output_dir)

    try:
        asyncio.run(
            _synthesize_async(
                text=cleaned_text,
                output_path=output_path,
                voice=chosen_voice,
                rate=chosen_rate,
                volume=chosen_volume,
            )
        )
    except RuntimeError as runtime_error:
        # Supports execution from environments that already run an event loop.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _synthesize_async(
                    text=cleaned_text,
                    output_path=output_path,
                    voice=chosen_voice,
                    rate=chosen_rate,
                    volume=chosen_volume,
                )
            )
        except Exception as error:  # noqa: BLE001
            raise AudioGenerationError(f"Edge TTS generation failed: {error}") from error
        finally:
            loop.close()
    except Exception as error:  # noqa: BLE001
        raise AudioGenerationError(f"Edge TTS generation failed: {error}") from error

    return str(output_path)
