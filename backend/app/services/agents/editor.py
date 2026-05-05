"""剪辑智能体：把生成片段（含复用片段）顺序整理为时间线补丁所需的 SegmentSpec 列表。"""

from __future__ import annotations

from typing import Dict, List

from ..timeline import SegmentSpec


def assemble_specs(*, generated_shots: List[Dict], reused_shots: List[Dict]) -> List[SegmentSpec]:
    """简化策略：先放过渡/桥接，再按生成顺序拼接，复用片段插入到对应位置。"""

    specs: List[SegmentSpec] = []
    if reused_shots:
        for r in reused_shots:
            specs.append(
                SegmentSpec(
                    file_path=r["file_path"],
                    duration=r["duration"],
                    source="reused",
                    shot_id=r.get("shot_id"),
                    audio_path=r.get("audio_path"),
                    caption=r.get("caption"),
                    note="媒资库复用：" + ", ".join(r.get("reasons") or []),
                )
            )
    for g in generated_shots:
        cap = (g.get("brief") or g.get("caption") or "").strip()
        specs.append(
            SegmentSpec(
                file_path=g["file_path"],
                duration=g["duration"],
                source="generated" if not g.get("fallback") else "fallback",
                shot_id=None,
                audio_path=g.get("audio_path"),
                caption=cap or None,
                note=("AI 生成" if not g.get("fallback") else "兜底占位"),
            )
        )
    return specs
