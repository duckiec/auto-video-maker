"""Video generation utilities using MoviePy and Whisper.

Phase 3 scope:
- Slice random gameplay segment to match narration length
- Generate word-level subtitles from Whisper timestamps
- Render 1-3 word center subtitles and export MP4
"""

from __future__ import annotations

import os
import random
import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import PIL.Image
import whisper
from PIL import ImageDraw, ImageFont
from moviepy.editor import AudioFileClip, CompositeVideoClip, ImageClip, VideoFileClip, vfx

from config_store import get_config

DEFAULT_BACKGROUND_VIDEO = "assets/gameplay.mp4"
DEFAULT_WHISPER_MODEL = "base"
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
SUBTITLE_SIDE_MARGIN = 120
SUBTITLE_MIN_TEXT_WIDTH = 200
SUBTITLE_MAX_LINES = 6
SUBTITLE_MIN_PROBE_HEIGHT = 200
SUBTITLE_MIN_LINE_HEIGHT = 1
SUBTITLE_MIN_HORIZONTAL_PADDING = 24
SUBTITLE_MIN_VERTICAL_PADDING = 14
SUBTITLE_MIN_LINE_SPACING = 6

# Pillow >=10 removed Image.ANTIALIAS; MoviePy 1.0.3 still references it.
if not hasattr(PIL.Image, "ANTIALIAS") and hasattr(PIL.Image, "Resampling"):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS
elif not hasattr(PIL.Image, "ANTIALIAS"):
    warnings.warn(
        "Pillow does not expose ANTIALIAS or Resampling; MoviePy resize may fail.",
        RuntimeWarning,
    )


class VideoGenerationError(RuntimeError):
    """Raised when final video generation fails."""


@dataclass
class WordToken:
    text: str
    start: float
    end: float


@dataclass
class SubtitleChunk:
    text: str
    start: float
    end: float


def _normalize_words(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _build_output_path(output_dir: str | os.PathLike[str], prefix: str = "final") -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return directory / f"{prefix}_{timestamp}.mp4"


def _extract_word_tokens(audio_path: str, model_name: str) -> list[WordToken]:
    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path, word_timestamps=True, fp16=False)

    tokens: list[WordToken] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            text = _normalize_words(word.get("word", ""))
            start = float(word.get("start", 0.0))
            end = float(word.get("end", start + 0.15))
            if not text:
                continue
            if end <= start:
                end = start + 0.15
            tokens.append(WordToken(text=text, start=start, end=end))

    if not tokens:
        raise VideoGenerationError("Whisper returned no word-level timestamps.")

    return tokens


def _group_words(tokens: list[WordToken], min_words: int = 1, max_words: int = 3) -> list[SubtitleChunk]:
    if min_words < 1 or max_words < min_words:
        raise VideoGenerationError("Invalid subtitle chunk settings.")

    chunks: list[SubtitleChunk] = []
    index = 0
    while index < len(tokens):
        size = random.randint(min_words, max_words)
        group = tokens[index : index + size]
        if not group:
            break

        text = " ".join(token.text for token in group).upper()
        start = group[0].start
        end = max(group[-1].end, start + 0.1)
        chunks.append(SubtitleChunk(text=text, start=start, end=end))
        index += size

    return chunks


def _prepare_background_clip(
    background_video_path: str | os.PathLike[str],
    target_duration: float,
    output_width: int,
    output_height: int,
) -> VideoFileClip:
    clip = VideoFileClip(str(background_video_path))

    if clip.duration < target_duration:
        prepared = clip.fx(vfx.loop, duration=target_duration)
    else:
        max_start = max(0.0, clip.duration - target_duration)
        start_time = random.uniform(0.0, max_start)
        prepared = clip.subclip(start_time, start_time + target_duration)

    target_ratio = output_width / output_height
    clip_ratio = prepared.w / prepared.h

    if clip_ratio > target_ratio:
        resized = prepared.resize(height=output_height)
    else:
        resized = prepared.resize(width=output_width)

    fitted = resized.crop(
        x_center=resized.w / 2,
        y_center=resized.h / 2,
        width=output_width,
        height=output_height,
    )
    return fitted


def _build_subtitle_clips(
    subtitles: list[SubtitleChunk],
    clip_width: int,
    font_size: int,
    stroke_width: int,
) -> list[ImageClip]:
    subtitle_clips: list[ImageClip] = []
    max_text_width = max(clip_width - SUBTITLE_SIDE_MARGIN, SUBTITLE_MIN_TEXT_WIDTH)
    max_text_height = max(font_size * SUBTITLE_MAX_LINES, SUBTITLE_MIN_PROBE_HEIGHT)

    font_candidates = [
        # Optional override for a custom TTF/OTF subtitle font file path.
        os.getenv("SUBTITLE_FONT_PATH", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    font = None
    for candidate in font_candidates:
        if not candidate:
            continue
        try:
            font = ImageFont.truetype(candidate, size=font_size)
            break
        except OSError:
            continue
    if font is None:
        # Final fallback when no system/custom TTF font is available; set the SUBTITLE_FONT_PATH environment variable for better quality.
        font = ImageFont.load_default()

    for item in subtitles:
        text = _normalize_words(item.text).upper()
        if not text:
            continue

        probe_image = PIL.Image.new("RGBA", (max_text_width, max_text_height), (0, 0, 0, 0))
        probe_draw = ImageDraw.Draw(probe_image)

        words = text.split(" ")
        lines: list[str] = []
        current_line = ""
        for word in words:
            if not word:
                continue
            candidate = word if not current_line else f"{current_line} {word}"
            candidate_bbox = probe_draw.textbbox(
                (0, 0),
                candidate,
                font=font,
                stroke_width=stroke_width,
            )
            candidate_width = candidate_bbox[2] - candidate_bbox[0]
            if current_line and candidate_width > max_text_width:
                lines.append(current_line)
                current_line = word
            else:
                current_line = candidate
        if current_line:
            lines.append(current_line)
        if not lines:
            continue

        line_heights: list[int] = []
        line_widths: list[int] = []
        for line in lines:
            bbox = probe_draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(max(SUBTITLE_MIN_LINE_HEIGHT, bbox[3] - bbox[1]))

        horizontal_padding = max(SUBTITLE_MIN_HORIZONTAL_PADDING, font_size // 2)
        vertical_padding = max(SUBTITLE_MIN_VERTICAL_PADDING, font_size // 4)
        line_spacing = max(SUBTITLE_MIN_LINE_SPACING, font_size // 6)
        subtitle_width = min(max_text_width, max(line_widths) + (horizontal_padding * 2))
        subtitle_height = sum(line_heights) + (line_spacing * (len(lines) - 1)) + (vertical_padding * 2)
        # Guard against tiny rendered text boxes for unusual font metric edge cases.
        subtitle_height = max(subtitle_height, font_size + (vertical_padding * 2))

        subtitle_image = PIL.Image.new("RGBA", (subtitle_width, subtitle_height), (0, 0, 0, 0))
        subtitle_draw = ImageDraw.Draw(subtitle_image)

        y_offset = vertical_padding
        for line, line_height in zip(lines, line_heights):
            bbox = subtitle_draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            line_width = bbox[2] - bbox[0]
            x_pos = (subtitle_width - line_width) // 2
            subtitle_draw.text(
                (x_pos, y_offset),
                line,
                font=font,
                fill="white",
                stroke_fill="black",
                stroke_width=stroke_width,
            )
            y_offset += line_height + line_spacing

        subtitle = (
            ImageClip(np.array(subtitle_image))
            .set_position(("center", "center"))
            .set_start(item.start)
            .set_end(item.end)
        )
        subtitle_clips.append(subtitle)

    return subtitle_clips


def generate_video(
    audio_path: str | os.PathLike[str],
    background_video_path: str | os.PathLike[str] = DEFAULT_BACKGROUND_VIDEO,
    output_dir: str | os.PathLike[str] = "output",
    whisper_model_name: str | None = None,
) -> str:
    """Create a final short-form video by compositing gameplay, voiceover, and subtitles."""

    audio_file = Path(audio_path)
    if not audio_file.exists():
        raise VideoGenerationError(f"Audio file not found: {audio_file}")

    config = get_config()
    video_config = config.get("video", {})
    subtitle_config = video_config.get("subtitle", {})
    output_config = video_config.get("output", {})
    path_config = config.get("paths", {})

    resolved_background = (
        background_video_path
        if background_video_path != DEFAULT_BACKGROUND_VIDEO
        else path_config.get("background_video", DEFAULT_BACKGROUND_VIDEO)
    )

    background_file = Path(resolved_background)
    if not background_file.exists():
        raise VideoGenerationError(f"Background video not found: {background_file}")

    resolved_output_dir = output_dir if output_dir != "output" else path_config.get("output_dir", "output")
    output_path = _build_output_path(resolved_output_dir)
    model_name = whisper_model_name or os.getenv(
        "WHISPER_MODEL",
        video_config.get("whisper_model", DEFAULT_WHISPER_MODEL),
    )

    subtitle_min_words = int(subtitle_config.get("min_words", 1))
    subtitle_max_words = int(subtitle_config.get("max_words", 3))
    subtitle_font_size = int(subtitle_config.get("font_size", 84))
    subtitle_stroke_width = int(subtitle_config.get("stroke_width", 6))

    output_width = int(output_config.get("width", OUTPUT_WIDTH))
    output_height = int(output_config.get("height", OUTPUT_HEIGHT))
    output_fps = int(output_config.get("fps", 30))

    base_clip: VideoFileClip | None = None
    audio_clip: AudioFileClip | None = None
    final_clip: CompositeVideoClip | None = None
    subtitle_clips: list[ImageClip] = []

    try:
        audio_clip = AudioFileClip(str(audio_file))
        target_duration = audio_clip.duration
        if not target_duration or target_duration <= 0:
            raise VideoGenerationError("Audio duration is invalid.")

        base_clip = _prepare_background_clip(
            str(background_file),
            target_duration,
            output_width=output_width,
            output_height=output_height,
        )
        tokens = _extract_word_tokens(str(audio_file), model_name=model_name)
        subtitle_data = _group_words(
            tokens=tokens,
            min_words=subtitle_min_words,
            max_words=subtitle_max_words,
        )
        subtitle_clips = _build_subtitle_clips(
            subtitle_data,
            clip_width=int(base_clip.w),
            font_size=subtitle_font_size,
            stroke_width=subtitle_stroke_width,
        )

        final_clip = CompositeVideoClip([base_clip, *subtitle_clips]).set_audio(audio_clip)
        final_clip.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=int(base_clip.fps or output_fps),
            threads=2,
            temp_audiofile=str(Path(resolved_output_dir) / "temp-audio.m4a"),
            remove_temp=True,
        )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise VideoGenerationError("Final video file was not created.")

        return str(output_path)
    except Exception as error:  # noqa: BLE001
        raise VideoGenerationError(f"Video generation failed: {error}") from error
    finally:
        for subtitle in subtitle_clips:
            subtitle.close()
        if final_clip is not None:
            final_clip.close()
        if base_clip is not None:
            base_clip.close()
        if audio_clip is not None:
            audio_clip.close()
