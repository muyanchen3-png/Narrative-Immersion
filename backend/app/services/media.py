"""FFmpeg 媒体处理工具：探测、切片、拼接、占位生成。"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..config import settings


class FFmpegError(RuntimeError):
    pass


def _run(cmd: List[str], capture: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed: {' '.join(shlex.quote(c) for c in cmd)}\n{proc.stderr}"
        )
    return proc


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(file_path: str) -> MediaInfo:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        file_path,
    ]
    out = _run(cmd).stdout
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    duration = float(data.get("format", {}).get("duration", 0.0))
    width = height = 0
    fps = 0.0
    has_audio = False
    for s in streams:
        if s.get("codec_type") == "video" and width == 0:
            width = int(s.get("width", 0))
            height = int(s.get("height", 0))
            r = s.get("r_frame_rate", "0/1")
            try:
                num, den = r.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except Exception:
                fps = 0.0
        if s.get("codec_type") == "audio":
            has_audio = True
    return MediaInfo(duration=duration, width=width, height=height, fps=fps, has_audio=has_audio)


def mux_video_with_tts_audio(video_path: str, audio_path: str, dst: str) -> None:
    """
    将配音/TTS 音轨并入视频：以视频时长为准。
    配音长于视频则截断；短于尾部补静音，避免 ``-shortest`` 把画面裁短。
    """
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    v_info = probe(video_path)
    a_info = probe(audio_path)
    dur_v = max(0.05, float(v_info.duration))
    dur_a = max(0.0, float(a_info.duration))

    if dur_a > dur_v + 0.05:
        fc = f"[1:a]atrim=start=0:duration={dur_v},asetpts=PTS-STARTPTS[aout]"
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-filter_complex",
            fc,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{dur_v:.4f}",
            "-movflags",
            "+faststart",
            dst,
        ]
    elif dur_a < dur_v - 0.05:
        pad = dur_v - dur_a
        fc = f"[1:a]apad=pad_dur={pad:.4f},asetpts=PTS-STARTPTS[aout]"
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-filter_complex",
            fc,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{dur_v:.4f}",
            "-movflags",
            "+faststart",
            dst,
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{dur_v:.4f}",
            "-movflags",
            "+faststart",
            dst,
        ]
    _run(cmd)


def make_chord_pad_bgm(
    duration: float,
    dst: str,
    *,
    freqs: tuple[float, float, float] = (196.0, 246.94, 293.66),
    gain_each: float = 0.05,
) -> None:
    """无版权风险的轻柔和弦铺底（大三和弦近似），比单频 sine 更像背景音乐。"""
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.2, float(duration))
    f0, f1, f2 = freqs
    fc = (
        f"[0:a]volume={gain_each}[a0];"
        f"[1:a]volume={gain_each}[a1];"
        f"[2:a]volume={gain_each}[a2];"
        f"[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0[bg]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={f0}:sample_rate=44100:duration={dur}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={f1}:sample_rate=44100:duration={dur}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={f2}:sample_rate=44100:duration={dur}",
        "-filter_complex",
        fc,
        "-map",
        "[bg]",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        dst,
    ]
    _run(cmd)


def mix_voice_with_bgm_track(
    voice_path: str,
    bgm_path: str,
    video_duration: float,
    dst: str,
    *,
    voice_gain: float = 1.25,
    bgm_gain: float = 0.16,
) -> None:
    """
    人声与任意 BGM 文件（和弦铺底 / 用户下载的 mp3）混合，总时长对齐 ``video_duration``。
    """
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    dur_v = max(0.2, float(video_duration))
    dv = max(0.0, float(probe(voice_path).duration))
    dbgm = max(0.0, float(probe(bgm_path).duration))

    if dv > dur_v + 0.05:
        vprep = f"[0:a]atrim=start=0:duration={dur_v},asetpts=PTS-STARTPTS[vp]"
    elif dv < dur_v - 0.05:
        vprep = f"[0:a]apad=pad_dur={dur_v - dv:.4f},asetpts=PTS-STARTPTS[vp]"
    else:
        vprep = "[0:a]asetpts=PTS-STARTPTS[vp]"

    if dbgm > dur_v + 0.05:
        bprep = f"[1:a]atrim=start=0:duration={dur_v},asetpts=PTS-STARTPTS[bg]"
    elif dbgm < dur_v - 0.05:
        # BGM 短则循环拼接（atable）复杂；改为 apad 静音尾 —— 对音乐不理想，长 BGM 建议裁好再用
        bprep = f"[1:a]apad=pad_dur={dur_v - dbgm:.4f},asetpts=PTS-STARTPTS[bg]"
    else:
        bprep = "[1:a]asetpts=PTS-STARTPTS[bg]"

    fc = (
        f"{vprep};"
        f"{bprep};"
        f"[vp]volume={voice_gain}[v];"
        f"[bg]volume={bgm_gain}[b];"
        f"[v][b]amix=inputs=2:duration=first:normalize=0[aout]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        voice_path,
        "-i",
        bgm_path,
        "-filter_complex",
        fc,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        dst,
    ]
    _run(cmd)


def mix_voice_with_soft_bgm(
    voice_path: str,
    video_duration: float,
    dst: str,
    *,
    sine_hz: float = 392.0,
    bgm_volume: float = 0.14,
) -> None:
    """
    将 TTS 人声与轻柔铺底混成单轨（默认改为和弦铺底 + ``mix_voice_with_bgm_track``）。
    保留函数名以兼容旧脚本调用。
    """
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    dur_v = max(0.2, float(video_duration))
    chord = str(Path(dst).with_suffix(".chord_pad.m4a"))
    make_chord_pad_bgm(dur_v, chord)
    try:
        mix_voice_with_bgm_track(
            voice_path,
            chord,
            dur_v,
            dst,
            voice_gain=1.25,
            bgm_gain=min(0.22, bgm_volume + 0.06),
        )
    finally:
        try:
            Path(chord).unlink(missing_ok=True)
        except Exception:
            pass


def extract_audio_wav(src_video: str, dst_wav: str) -> bool:
    """
    从视频抽取单声道 16kHz PCM WAV，供 Whisper 等 ASR。
    无音轨或失败时返回 False。
    """
    Path(dst_wav).parent.mkdir(parents=True, exist_ok=True)
    try:
        info = probe(src_video)
        if not info.has_audio:
            return False
    except FFmpegError:
        return False
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        src_video,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        dst_wav,
    ]
    try:
        _run(cmd)
    except FFmpegError:
        return False
    return Path(dst_wav).is_file() and Path(dst_wav).stat().st_size > 80


def cut(src: str, start: float, end: float, dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        src,
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        dst,
    ]
    _run(cmd)


def thumbnail(src: str, t: float, dst: str, width: int = 480) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{t:.3f}",
        "-i",
        src,
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-1",
        dst,
    ]
    try:
        _run(cmd)
    except FFmpegError:
        # 缩略图失败不阻塞主流程
        pass


def concat_reencode(parts: List[str], dst: str) -> None:
    """以重编码的方式拼接，保证不同来源片段也能无缝合并。"""

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    list_path = Path(dst).with_suffix(".concat.txt")
    with list_path.open("w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{Path(p).resolve()}'\n")
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        dst,
    ]
    try:
        _run(cmd)
    finally:
        try:
            list_path.unlink()
        except Exception:
            pass


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _find_font() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _hex_color(value: str) -> tuple:
    """支持 0xRRGGBB / #RRGGBB / 0xAARRGGBB。"""

    s = value.lstrip("#")
    if s.lower().startswith("0x"):
        s = s[2:]
    if len(s) == 6:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (r, g, b, 255)
    if len(s) == 8:
        a = int(s[0:2], 16)
        r = int(s[2:4], 16)
        g = int(s[4:6], 16)
        b = int(s[6:8], 16)
        return (r, g, b, a)
    return (17, 24, 39, 255)


def _render_overlay_png(
    *,
    title: str,
    subtitle: str,
    badge: str,
    width: int,
    height: int,
    bg_color: str,
    accent_color: str,
    dst: str,
) -> None:
    """用 Pillow 渲染一张静态 PNG（背景 + 文字），后续作为视频底图覆盖。"""

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), color=_hex_color(bg_color)[:3])
    draw = ImageDraw.Draw(img, "RGBA")

    font_path = _find_font()

    def font_at(size: int):
        if font_path:
            try:
                return ImageFont.truetype(font_path, size=size)
            except Exception:
                pass
        return ImageFont.load_default()

    accent_rgba = _hex_color(accent_color)
    draw.rectangle((48, 48, 248, 100), fill=(accent_rgba[0], accent_rgba[1], accent_rgba[2], int(accent_rgba[3] * 0.9)))
    f_badge = font_at(24)
    draw.text((72, 60), badge or "", font=f_badge, fill=(0, 0, 0, 255))

    f_title = font_at(56)
    f_subtitle = font_at(30)
    f_footer = font_at(20)

    def text_size(text: str, font) -> tuple:
        try:
            box = draw.textbbox((0, 0), text, font=font)
            return box[2] - box[0], box[3] - box[1]
        except Exception:
            return font.getsize(text)

    cx, cy = width // 2, height // 2

    if title:
        tw, th = text_size(title, f_title)
        draw.text((cx - tw // 2, cy - th // 2 - 50), title, font=f_title, fill=(255, 255, 255, 255))

    if subtitle:
        tw, th = text_size(subtitle, f_subtitle)
        draw.text((cx - tw // 2, cy + 30), subtitle, font=f_subtitle, fill=(203, 213, 245, 255))

    footer = "Hermes 互动叙事 · 占位预览"
    tw, th = text_size(footer, f_footer)
    draw.text((width - tw - 40, height - th - 40), footer, font=f_footer, fill=(148, 163, 184, 255))

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="PNG")


def make_placeholder_clip(
    duration: float,
    title: str,
    subtitle: str = "",
    badge: str = "AI 生成",
    bg_color: str = "0x111827",
    accent_color: str = "0xf59e0b",
    width: int = 1280,
    height: int = 720,
    audio_path: Optional[str] = None,
    *,
    dst: str,
) -> None:
    """生成一段带文字的占位视频。先用 Pillow 渲染静态画面，再用 FFmpeg 包成 mp4。

    这样无需 FFmpeg 编进 drawtext/libfreetype，兼容性更好。
    """

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, float(duration))

    overlay_path = Path(dst).with_suffix(".bg.png")
    _render_overlay_png(
        title=title,
        subtitle=subtitle,
        badge=badge,
        width=width,
        height=height,
        bg_color=bg_color,
        accent_color=accent_color,
        dst=str(overlay_path),
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(overlay_path),
    ]
    if audio_path and Path(audio_path).exists():
        cmd += ["-i", str(audio_path)]
    else:
        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=r=44100:cl=stereo",
        ]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-vf",
        f"scale={width}:{height}:flags=lanczos,format=yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        dst,
    ]
    try:
        _run(cmd)
    finally:
        try:
            overlay_path.unlink()
        except Exception:
            pass


def make_silent_audio(duration: float, dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-t",
        f"{max(0.5, duration):.3f}",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        dst,
    ]
    _run(cmd)


def make_tts_placeholder_audio(text: str, duration: float, dst: str) -> None:
    """合成低音量正弦波作为 mock TTS 输出，保证音轨连续可播。"""

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    seed = (sum(ord(c) for c in text) % 5) + 1
    freq = 220 + seed * 60
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={max(0.4, duration):.3f}",
        "-af",
        "volume=0.05",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        dst,
    ]
    _run(cmd)


def make_color_video_with_voice(
    duration: float,
    title: str,
    subtitle: str,
    voice_text: str,
    *,
    dst: str,
) -> str:
    """生成一段“占位视频 + 占位配音”的整合片段。"""

    audio_dst = str(Path(dst).with_suffix(".m4a"))
    if voice_text.strip():
        make_tts_placeholder_audio(voice_text, duration, audio_dst)
    else:
        make_silent_audio(duration, audio_dst)
    make_placeholder_clip(
        duration=duration,
        title=title,
        subtitle=subtitle,
        audio_path=audio_dst,
        dst=dst,
    )
    return audio_dst


def file_size(path: str) -> int:
    p = Path(path)
    return p.stat().st_size if p.exists() else 0
