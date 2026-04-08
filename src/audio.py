"""Audio generation utilities using Edge TTS and optional ElevenLabs."""

from __future__ import annotations

import asyncio
import os
import random
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import edge_tts
import requests
from dotenv import load_dotenv

from config_store import get_config

load_dotenv()

DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_RATE = "+8%"
DEFAULT_PITCH = "+2Hz"
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


def _validate_audio_file(output_path: Path, label: str = "audio") -> None:
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise AudioGenerationError(f"{label} file is missing or empty: {output_path}")


def _select_random_edge_voice(audio_config: dict[str, Any], fallback_voice: str) -> str:
    configured_pool = audio_config.get("edge_voice_pool", [])
    pool = [str(v).strip() for v in configured_pool if str(v).strip()]
    if fallback_voice and fallback_voice not in pool:
        pool.append(fallback_voice)
    if not pool:
        return DEFAULT_VOICE
    return random.choice(pool)


def _pick_speaker_voice(
    speaker: str,
    speaker_voice_map: dict[str, Any],
    default_voice: str,
    audio_config: dict[str, Any],
) -> str:
    mapped = str(speaker_voice_map.get(speaker, "")).strip()
    if mapped:
        return mapped
    return _select_random_edge_voice(audio_config, default_voice)


def _run_with_retry(
    operation,
    *,
    attempts: int,
    delay_seconds: float,
    error_prefix: str,
):
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt == max(1, attempts):
                break
            time.sleep(max(0.2, delay_seconds) * attempt)
    raise AudioGenerationError(f"{error_prefix} after {max(1, attempts)} attempts: {last_error}")


async def _synthesize_edge_async(
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
) -> Path:
    communicator = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        volume=volume,
    )
    await communicator.save(str(output_path))
    _validate_audio_file(output_path, label="Edge TTS output")
    return output_path


def _synthesize_edge_sync(
    *,
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
) -> Path:
    try:
        asyncio.run(
            _synthesize_edge_async(
                text=text,
                output_path=output_path,
                voice=voice,
                rate=rate,
                pitch=pitch,
                volume=volume,
            )
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _synthesize_edge_async(
                    text=text,
                    output_path=output_path,
                    voice=voice,
                    rate=rate,
                    pitch=pitch,
                    volume=volume,
                )
            )
        finally:
            loop.close()
    _validate_audio_file(output_path, label="Edge TTS output")
    return output_path


def _synthesize_elevenlabs(
    *,
    text: str,
    output_path: Path,
    voice_id: str,
    model_id: str,
    api_key: str,
) -> Path:
    """Modular drop-in ElevenLabs generator for future premium voice swaps."""
    if not voice_id:
        raise AudioGenerationError("ElevenLabs voice_id is required.")
    if not api_key:
        raise AudioGenerationError("Missing ELEVENLABS_API_KEY in environment.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model_id or "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.75, "style": 0.7, "use_speaker_boost": True},
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    response = requests.post(url, json=payload, headers=headers, timeout=60)
    if response.status_code >= 400:
        raise AudioGenerationError(
            f"ElevenLabs request failed ({response.status_code}): {response.text}"
        )

    output_path.write_bytes(response.content)
    _validate_audio_file(output_path, label="ElevenLabs output")
    return output_path


def _clean_dialogue_segments(dialogue_segments: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not dialogue_segments:
        return []

    cleaned: list[dict[str, str]] = []
    for item in dialogue_segments:
        if not isinstance(item, dict):
            continue
        speaker = " ".join(str(item.get("speaker", "Narrator")).split()).strip() or "Narrator"
        text = " ".join(str(item.get("text", "")).split()).strip()
        if not text:
            continue
        cleaned.append({"speaker": speaker[:40], "text": text})
    return cleaned


def _concat_audio_segments(segment_paths: list[Path], output_path: Path) -> Path:
    if not segment_paths:
        raise AudioGenerationError("No segment audio files were generated.")

    from moviepy.editor import AudioFileClip, concatenate_audioclips

    clips = [AudioFileClip(str(path)) for path in segment_paths]
    try:
        if len(clips) == 1:
            merged = clips[0]
        else:
            merged = concatenate_audioclips(clips)

        merged.write_audiofile(str(output_path), fps=44100, nbytes=2, bitrate="192k", logger=None)
        _validate_audio_file(output_path, label="Concatenated dialogue audio")
    finally:
        for clip in clips:
            clip.close()
        if "merged" in locals() and merged is not clips[0]:
            merged.close()
    return output_path


def _estimate_keyword_timestamps(
    script_text: str,
    duration_seconds: float,
    keywords: list[str],
) -> dict[str, list[float]]:
    lowered = script_text.lower()
    tokens = re.findall(r"[a-z']+", lowered)
    if not tokens or duration_seconds <= 0:
        return {}

    word_step = duration_seconds / max(1, len(tokens))
    word_starts: list[tuple[str, float]] = [
        (token, index * word_step) for index, token in enumerate(tokens)
    ]

    timeline: dict[str, list[float]] = {}
    for keyword in [item.strip().lower() for item in keywords if item and item.strip()]:
        pieces = keyword.split()
        hits: list[float] = []
        if len(pieces) == 1:
            for token, start in word_starts:
                if token == pieces[0]:
                    hits.append(start)
        else:
            for idx in range(0, len(tokens) - len(pieces) + 1):
                if tokens[idx : idx + len(pieces)] == pieces:
                    hits.append(idx * word_step)
        if hits:
            timeline[keyword] = hits
    return timeline


def _overlay_background_and_sfx(
    *,
    voice_path: Path,
    script_text: str,
    audio_config: dict[str, Any],
) -> Path:
    enable_bgm = bool(audio_config.get("enable_background_music", True))
    enable_dynamic_sfx = bool(audio_config.get("enable_dynamic_sfx", True))
    if not enable_bgm and not enable_dynamic_sfx:
        return voice_path

    from moviepy.audio.fx.all import audio_loop
    from moviepy.editor import AudioFileClip, CompositeAudioClip

    narration = AudioFileClip(str(voice_path))
    bgm_clip = None
    sfx_layers: list[Any] = []
    mixed_audio = None

    try:
        layers: list[Any] = [narration]

        if enable_bgm:
            bgm_path = Path(str(audio_config.get("background_music_path", "assets/drama_bgm.mp3")))
            if bgm_path.exists():
                base_bgm = AudioFileClip(str(bgm_path))
                bgm_volume = float(audio_config.get("background_music_volume", 0.15))
                ducking_ratio = float(audio_config.get("ducking_ratio", 0.28))
                bgm_clip = audio_loop(base_bgm, duration=narration.duration).volumex(max(0.0, bgm_volume * ducking_ratio))
                layers.append(bgm_clip)

        if enable_dynamic_sfx:
            keyword_sfx_map = audio_config.get("keyword_sfx_map", {})
            keywords = list(keyword_sfx_map.keys()) if isinstance(keyword_sfx_map, dict) else []
            keyword_times = _estimate_keyword_timestamps(
                script_text=script_text,
                duration_seconds=float(narration.duration or 0.0),
                keywords=keywords,
            )
            sfx_volume = float(audio_config.get("sfx_volume", 0.35))
            for keyword, hits in keyword_times.items():
                sfx_file = Path(str(keyword_sfx_map.get(keyword, "")))
                if not sfx_file.exists():
                    continue
                for hit in hits:
                    sfx_clip = AudioFileClip(str(sfx_file)).volumex(max(0.0, sfx_volume)).set_start(max(0.0, hit))
                    sfx_layers.append(sfx_clip)
                    layers.append(sfx_clip)

        mixed_audio = CompositeAudioClip(layers).set_duration(narration.duration)
        temp_mix = voice_path.with_name(f"{voice_path.stem}_mixed{voice_path.suffix}")
        mixed_audio.write_audiofile(str(temp_mix), fps=44100, nbytes=2, bitrate="192k", logger=None)
        _validate_audio_file(temp_mix, label="Mixed audio")
        temp_mix.replace(voice_path)
        _validate_audio_file(voice_path, label="Final mixed audio")
        return voice_path
    finally:
        if mixed_audio is not None:
            mixed_audio.close()
        for clip in sfx_layers:
            try:
                clip.close()
            except Exception:  # noqa: BLE001
                pass
        if bgm_clip is not None:
            try:
                bgm_clip.close()
            except Exception:  # noqa: BLE001
                pass
        narration.close()


def _synthesize_one_segment(
    *,
    provider: str,
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    audio_config: dict[str, Any],
    retry_attempts: int,
    retry_delay_seconds: float,
) -> Path:
    provider_normalized = provider.strip().lower()

    def _edge_call() -> Path:
        return _synthesize_edge_sync(
            text=text,
            output_path=output_path,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )

    def _elevenlabs_call() -> Path:
        return _synthesize_elevenlabs(
            text=text,
            output_path=output_path,
            voice_id=str(audio_config.get("elevenlabs_voice_id", "")).strip(),
            model_id=str(audio_config.get("elevenlabs_model_id", "eleven_multilingual_v2")),
            api_key=str(os.getenv("ELEVENLABS_API_KEY", "")).strip(),
        )

    if provider_normalized == "elevenlabs":
        return _run_with_retry(
            _elevenlabs_call,
            attempts=retry_attempts,
            delay_seconds=retry_delay_seconds,
            error_prefix="ElevenLabs generation failed",
        )

    return _run_with_retry(
        _edge_call,
        attempts=retry_attempts,
        delay_seconds=retry_delay_seconds,
        error_prefix="Edge TTS generation failed",
    )


def generate_voiceover(
    text: str,
    output_dir: str | os.PathLike[str] = "output",
    voice: str | None = None,
    rate: str = DEFAULT_RATE,
    volume: str = DEFAULT_VOLUME,
    pitch: str = DEFAULT_PITCH,
    dialogue_segments: list[dict[str, str]] | None = None,
) -> str:
    """Generate an MP3 voiceover from text and return the output file path."""

    cleaned_text = _validate_text(text)
    config = get_config()
    audio_config = config.get("audio", {})

    provider = str(audio_config.get("provider", os.getenv("TTS_PROVIDER", "edge"))).strip().lower()
    fallback_voice = voice or os.getenv("EDGE_TTS_VOICE", audio_config.get("voice", DEFAULT_VOICE))
    chosen_rate = rate if rate != DEFAULT_RATE else str(audio_config.get("rate", DEFAULT_RATE))
    chosen_volume = volume if volume != DEFAULT_VOLUME else str(audio_config.get("volume", DEFAULT_VOLUME))
    chosen_pitch = pitch if pitch != DEFAULT_PITCH else str(audio_config.get("pitch", DEFAULT_PITCH))
    retry_attempts = int(audio_config.get("tts_retry_attempts", 4))
    retry_delay_seconds = float(audio_config.get("tts_retry_delay_seconds", 1.5))

    output_path = _build_output_path(output_dir=output_dir)

    cleaned_segments = _clean_dialogue_segments(dialogue_segments)

    if cleaned_segments:
        with tempfile.TemporaryDirectory(prefix="tts_segments_", dir=str(Path(output_dir))) as temp_dir:
            segment_paths: list[Path] = []
            speaker_voice_map = audio_config.get("speaker_voice_map", {})
            for idx, segment in enumerate(cleaned_segments):
                speaker = segment["speaker"]
                segment_text = segment["text"]
                segment_voice = _pick_speaker_voice(
                    speaker=speaker,
                    speaker_voice_map=speaker_voice_map if isinstance(speaker_voice_map, dict) else {},
                    default_voice=fallback_voice,
                    audio_config=audio_config,
                )
                segment_path = Path(temp_dir) / f"segment_{idx:03d}.mp3"
                generated = _synthesize_one_segment(
                    provider=provider,
                    text=segment_text,
                    output_path=segment_path,
                    voice=segment_voice,
                    rate=chosen_rate,
                    pitch=chosen_pitch,
                    volume=chosen_volume,
                    audio_config=audio_config,
                    retry_attempts=retry_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                )
                segment_paths.append(generated)

            _concat_audio_segments(segment_paths=segment_paths, output_path=output_path)
    else:
        selected_voice = _select_random_edge_voice(audio_config, fallback_voice)
        _synthesize_one_segment(
            provider=provider,
            text=cleaned_text,
            output_path=output_path,
            voice=selected_voice,
            rate=chosen_rate,
            pitch=chosen_pitch,
            volume=chosen_volume,
            audio_config=audio_config,
            retry_attempts=retry_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )

    _validate_audio_file(output_path, label="Voiceover")
    _overlay_background_and_sfx(
        voice_path=output_path,
        script_text=cleaned_text,
        audio_config=audio_config,
    )
    _validate_audio_file(output_path, label="Final voiceover")
    return str(output_path)
