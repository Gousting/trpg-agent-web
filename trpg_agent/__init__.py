"""TRPG Agent — 中文 COC 跑团 KP，本地 AI 主持人。"""

from __future__ import annotations

__all__ = ["DungeonMap", "GameMap", "Room", "generate_tile_map", "render_map_png", "map_to_dict"]


def __getattr__(name: str):
	if name in __all__:
		from . import mapgen

		return getattr(mapgen, name)
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
	return sorted(list(globals()) + __all__)
