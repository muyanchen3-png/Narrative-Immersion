#!/usr/bin/env python3
"""用 ``_smoke_minimax.mp4`` 联调：清晰中文配音 + 可联网 BGM + 合成 mp4。

- **配音**：已配置 ``HERMES_TTS_*`` 或复用 **MiniMax 视频密钥** 时，优先 ``TTSClient``（MiniMax 走官方 ``/v1/t2a_v2``）；否则再试 ``edge-tts``；再回退 ``get_tts``（mock 为蜂鸣）。
- **BGM**：默认尝试下载免版税曲目（Kevin MacLeod / incompetech，便于试听）；失败则用和弦铺底；也可用 ``--bgm-file`` 指定本地 mp3。

用法::

    cd backend && source ../.venv/bin/activate
    pip install -r requirements.txt   # 含 edge-tts
    python scripts/smoke_video_love_mux.py
    python scripts/smoke_video_love_mux.py --text 我爱你 --edge-voice zh-CN-YunxiNeural
    python scripts/smoke_video_love_mux.py --bgm-file ~/Music/my.mp3 --no-download-bgm
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.services import media  # noqa: E402
from app.services.tts import TTSClient, get_tts  # noqa: E402

# Kevin MacLeod — Fluffing a Duck（CC-BY，试听常见；正式上架请保留署名）
DEFAULT_BGM_URL = (
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
    "Fluffing%20a%20Duck.mp3"
)


async def _edge_synthesize(text: str, dst: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(dst))


def _try_edge_tts(text: str, dst: Path, voice: str) -> bool:
    try:
        asyncio.run(_edge_synthesize(text, dst, voice))
        return dst.is_file() and dst.stat().st_size > 80
    except Exception as exc:
        print(f"[edge-tts 不可用，将回退 Hermes TTS：{exc}]", file=sys.stderr)
        return False


def _download_bgm(url: str, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            dst.write_bytes(r.content)
        ok = dst.is_file() and dst.stat().st_size > 2000
        if ok:
            print(f"已下载 BGM -> {dst}")
        return ok
    except Exception as exc:
        print(f"[BGM 下载失败，将用和弦铺底：{exc}]", file=sys.stderr)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke: _smoke mp4 + 中文 TTS + BGM")
    ap.add_argument(
        "--video",
        type=Path,
        default=ROOT / "storage" / "generated" / "_smoke_minimax.mp4",
        help="输入视频",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "storage" / "generated" / "_smoke_minimax_love.mp4",
        help="输出成片",
    )
    ap.add_argument("--text", default="我爱你", help="配音文案")
    ap.add_argument(
        "--edge-voice",
        default="zh-CN-XiaoxiaoNeural",
        help="edge-tts 发音人（男声示例 zh-CN-YunxiNeural）",
    )
    ap.add_argument(
        "--voice",
        default="male-qn-qingse",
        help="Hermes 配音的 voice_id（MiniMax 平台音色，如 male-qn-qingse；alloy 等会映射为默认中文男声）",
    )
    ap.add_argument("--no-bgm", action="store_true", help="只要人声，不要任何 BGM")
    ap.add_argument(
        "--bgm-file",
        type=Path,
        default=None,
        help="本地背景音乐文件（mp3/wav/aac 等 ffmpeg 可读格式）",
    )
    ap.add_argument(
        "--bgm-url",
        default="",
        help="自定义 BGM 下载地址；留空且未指定 --bgm-file 时使用内置默认曲",
    )
    ap.add_argument(
        "--no-download-bgm",
        action="store_true",
        help="不下载网络 BGM，改用内置和弦铺底（仍可与 --bgm-file 同用）",
    )
    args = ap.parse_args()

    video_path = args.video.resolve()
    if not video_path.is_file():
        print(f"找不到视频：{video_path}", file=sys.stderr)
        sys.exit(1)

    out_final = args.out.resolve()
    out_final.parent.mkdir(parents=True, exist_ok=True)
    work = out_final.parent

    vi = media.probe(str(video_path))
    dur_v = max(0.2, float(vi.duration))

    tts = TTSClient()
    hermes_first = bool(tts.api_key) and tts.provider in ("openai", "minimax")
    voice_out: Path | None = None
    used_edge = False
    used_hermes = False
    if hermes_first:
        v_hermes = work / "_smoke_love_voice_hermes.m4a"
        try:
            tts.synthesize(args.text, voice_id=args.voice, dst=str(v_hermes))
            if v_hermes.is_file() and v_hermes.stat().st_size > 80:
                voice_out = v_hermes
                used_hermes = True
        except Exception as exc:
            print(f"[Hermes TTS 失败，将试 edge-tts：{exc}]", file=sys.stderr)
    if voice_out is None:
        v_edge = work / "_smoke_love_voice_edge.mp3"
        used_edge = _try_edge_tts(args.text, v_edge, args.edge_voice)
        if used_edge:
            voice_out = v_edge
    if voice_out is None:
        voice_out = work / "_smoke_love_voice_tts.m4a"
        tts2 = get_tts(db=None, profile="fast")
        tts2.synthesize(args.text, voice_id=args.voice, dst=str(voice_out))
        print(
            "[提示] 当前为 Hermes mock/回退；人声可能为蜂鸣。请配置 HERMES_TTS_*、复用 MiniMax 视频 KEY，或安装 edge-tts。",
            file=sys.stderr,
        )

    if args.no_bgm:
        audio_for_mux = str(voice_out)
    else:
        mixed = work / "_smoke_love_voice_bgm.m4a"
        bgm_track: Path | None = None
        if args.bgm_file:
            bgm_track = args.bgm_file.expanduser().resolve()
            if not bgm_track.is_file():
                print(f"找不到 --bgm-file：{bgm_track}", file=sys.stderr)
                sys.exit(1)
        elif not args.no_download_bgm:
            dl = work / "_smoke_bgm_download.mp3"
            url = (args.bgm_url or "").strip() or DEFAULT_BGM_URL
            if _download_bgm(url, dl):
                bgm_track = dl
                print("BGM 来源：网络下载（默认 Kevin MacLeod / incompetech，CC-BY 请保留署名）")
        if bgm_track is None:
            chord = work / "_smoke_chord_pad.m4a"
            media.make_chord_pad_bgm(dur_v, str(chord))
            bgm_track = chord
            print("BGM：内置和弦铺底（未使用本地文件且下载失败或未启用下载时）")

        media.mix_voice_with_bgm_track(
            str(voice_out),
            str(bgm_track),
            dur_v,
            str(mixed),
            voice_gain=1.35,
            bgm_gain=0.14,
        )
        audio_for_mux = str(mixed)

    tmp_mux = str(work / "_smoke_love_muxing.mp4")
    media.mux_video_with_tts_audio(str(video_path), audio_for_mux, tmp_mux)
    Path(tmp_mux).replace(out_final)

    print("---")
    print("输入视频:", video_path)
    print("配音文案:", args.text)
    if used_edge:
        print("发音: edge-tts " + args.edge_voice)
    elif used_hermes:
        print("发音: Hermes TTS（MiniMax 时优先官方 /v1/t2a_v2，失败再兼容 /audio/speech）")
    else:
        print("发音: Hermes TTS（mock 或回退）")
    print("输出:", out_final)


if __name__ == "__main__":
    main()
