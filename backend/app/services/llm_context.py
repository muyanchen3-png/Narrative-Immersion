"""为 get_llm() 提供可选的数据库模型绑定（profile + kind → ModelConfig → api_key）。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional, Tuple

from sqlalchemy.orm import Session

_llm_binding: ContextVar[Optional[Tuple[Session, str, str]]] = ContextVar("_llm_binding", default=None)


def bind_model_kind(db: Session, profile: str, kind: str) -> None:
    """绑定任意文本/多模态 chat 类模型：llm / vlm。"""

    _llm_binding.set((db, profile, kind))


def bind_llm(db: Session, profile: str = "fast") -> None:
    bind_model_kind(db, profile, "llm")


def unbind_llm() -> None:
    _llm_binding.set(None)


def get_llm_binding() -> Optional[Tuple[Session, str, str]]:
    return _llm_binding.get()
