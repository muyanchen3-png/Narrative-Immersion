from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_prefix="HERMES_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765

    storage_dir: str = "storage"
    db_url: str = "sqlite:///storage/hermes.db"

    llm_provider: Literal["mock", "openai", "minimax"] = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o"

    #: 多模态（镜头理解等）；base/key 未填时回退到 llm_*。额外标签（如 gemma4）仅作文档/兼容，运行时仍走 OpenAI 兼容 HTTP。
    vlm_provider: Literal["mock", "openai", "minimax", "gemma4"] = "mock"
    vlm_base_url: Optional[str] = None
    vlm_api_key: Optional[str] = None
    vlm_model: str = "gpt-4o"

    #: 生图；未填时 base/key 回退 llm_*。OpenAI 用 `images/generations`；MiniMax 用 `POST /v1/image_generation`（见平台文档）。
    image_provider: Literal["mock", "openai", "replicate", "minimax"] = "mock"
    image_base_url: Optional[str] = None
    image_api_key: Optional[str] = None
    #: openai: dall-e-3 等；minimax: image-01 / image-01-live
    image_model: str = "dall-e-3"

    #: 生视频；minimax 走 MiniMax 官方异步接口（/v1/video_generation → 轮询 → /v1/files/retrieve）
    video_provider: Literal["mock", "runway", "luma", "replicate", "minimax"] = "mock"
    video_base_url: Optional[str] = None
    video_api_key: Optional[str] = None
    #: MiniMax：文生默认 MiniMax-Hailuo-2.3；图生可用 MiniMax-Hailuo-2.3-Fast（见 T2V/I2V 文档）
    video_model: str = "MiniMax-Hailuo-2.3"
    #: 主模型创建失败（无 task_id / 套餐不符等）时重试；可填 ``Hailuo-2.3-Fast-768P``（Fast + 768P）。空字符串关闭保底
    video_model_fallback: str = "Hailuo-2.3-Fast-768P"
    #: MiniMax 轮询间隔（秒），文档建议约 10
    video_minimax_poll_interval: float = 10.0
    #: MiniMax 生成分辨率：720P | 768P | 1080P（Token Plan 的 Hailuo-2.3 多为 768P 6s）
    video_minimax_resolution: str = "768P"
    #: 为 True 时：MiniMax 真实出片**必须**提供角色库参照图（首帧或主体参考），禁止纯文生「凭空」生成；无图则仅用 FFmpeg 占位。脚本试跑可设为 False。
    video_require_character_reference: bool = True

    #: 配音；未填时回退 llm_*。minimax 与 openai 相同走 POST …/audio/speech（OpenAI 兼容网关）。
    tts_provider: Literal["mock", "openai", "elevenlabs", "minimax"] = "mock"
    tts_base_url: Optional[str] = None
    tts_api_key: Optional[str] = None
    tts_model: str = "tts-1"
    #: MiniMax 快速复刻 / 同步 T2A（``/v1/t2a_v2``）使用的 speech 模型（如 speech-2.8-hd）
    minimax_voice_clone_model: str = "speech-2.8-hd"
    #: MiniMax T2A：当 voice_id 为 OpenAI 风格（alloy 等）或 mock 占位时的默认平台音色 id
    tts_minimax_default_voice_id: str = "male-qn-qingse"

    #: 叙事生成镜头混音：本地 BGM 文件路径（相对 ``HERMES_STORAGE_DIR`` 或绝对路径）；空则只压 TTS 不混 BGM
    generation_bgm_path: Optional[str] = None
    generation_bgm_voice_gain: float = 1.35
    generation_bgm_track_gain: float = 0.14

    #: 镜头音轨语音转写（OpenAI 兼容 /v1/audio/transcriptions）。mock=不调用。
    #: minimax 表示同一兼容网关上的转写（仍 POST …/audio/transcriptions）。
    #: asr_base_url / asr_api_key 未填时回退到 llm_*。
    asr_provider: Literal["mock", "openai", "minimax"] = "mock"
    asr_base_url: Optional[str] = None
    asr_api_key: Optional[str] = None
    asr_model: str = "whisper-1"
    #: ISO-639-1，如 zh、en；空则让模型自判
    asr_language: Optional[str] = None

    default_profile: Literal["fast", "quality", "fallback"] = "fast"

    #: 叙事干预为 True 时：禁用分镜备份链与视频占位片（须分镜 LLM 成功、MiniMax 等真实出片）
    #: 为 True 时：干预流程不使用分镜/视频占位兜底；模型或 TTS 失败即标记干预失败（见 orchestrator 文案）
    intervention_no_fallback: bool = True

    #: 为 True 时读取 SQLite ``model_configs``（设置页）。
    #: **合并顺序**（见 ``model_resolve._merge_credentials``）：有匹配 kind 的配置行时，
    #: **provider / model / base_url 以库为准**（缺省项用 .env 补齐）；**api_key** 优先库内保存，否则用 ``HERMES_*``。
    #: 库内也可只配模型不设密钥，此时密钥完全来自环境变量。
    #: 为 False 时不读库，仅用下方 HERMES_*。
    use_sqlite_model_configs: bool = True

    #: 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = "INFO"
    #: 单文件上限（字节），轮转旧文件为 hermes.log.1 ...
    log_max_bytes: int = Field(default=10 * 1024 * 1024)
    log_backup_count: int = Field(default=5)

    @property
    def storage_path(self) -> Path:
        path = ROOT_DIR / self.storage_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def uploads_dir(self) -> Path:
        return self._sub("uploads")

    @property
    def segments_dir(self) -> Path:
        return self._sub("segments")

    @property
    def generated_dir(self) -> Path:
        return self._sub("generated")

    @property
    def audio_dir(self) -> Path:
        return self._sub("audio")

    @property
    def hls_dir(self) -> Path:
        return self._sub("hls")

    def _sub(self, name: str) -> Path:
        path = self.storage_path / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        """应用日志目录（storage/logs）。"""
        p = self.storage_path / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

os.makedirs(settings.storage_path, exist_ok=True)
