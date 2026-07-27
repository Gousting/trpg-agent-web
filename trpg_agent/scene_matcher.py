"""场景卡匹配器 — 根据跑团剧情关键词匹配预生成的场景图。

工作原理：
1. 加载 scene_tags.json（15维标签）
2. 接收 KP 叙述文本或 GM 场景描述
3. 提取关键词 → 多维度匹配 → 返回最佳场景图和元数据
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TAGS_PATH = _PROJECT_ROOT / "data" / "scene_tags.json"
_SCENES_DIR = _PROJECT_ROOT / "data" / "scenes" / "Sceneimage"

# 维度权重（用于匹配排序）
_DIMENSION_WEIGHTS = {
    "scene_type": 3.0,    # 场景类型最重要
    "location": 2.5,      # 地点描述
    "mood": 1.5,          # 氛围
    "weather": 1.5,       # 天气
    "lighting": 1.2,      # 光线
    "architecture": 1.0,  # 建筑风格
    "era": 1.0,           # 时代
    "key_objects": 1.0,   # 关键物件
    "coc_themes": 0.8,    # 克苏鲁主题
    "narrative_hook": 0.5, # 叙事钩子
    "art_style": 0.3,     # 画风（弱权重）
    "color_palette": 0.3, # 色调（弱权重）
    "composition": 0.2,   # 构图（弱权重）
    "density": 0.1,
    "reusability": 0.1,
}

# 场景类型中文映射（KP 常用描述 → scene_type 字段值）
_SCENE_TYPE_ALIASES = {
    "小巷": ["城市暗巷", "雨夜小巷", "巷道"],
    "暗巷": ["城市暗巷", "雨夜小巷"],
    "街道": ["城市暗巷", "城市小巷", "城市巷道"],
    "马路": ["城市暗巷", "城市小巷"],
    "教堂": ["室内宗教场所", "废弃教堂", "宗教场所"],
    "礼拜堂": ["室内宗教场所", "废弃教堂"],
    "医院": ["医院病房", "废弃走廊"],
    "病房": ["医院病房"],
    "走廊": ["室内走廊", "废弃走廊"],
    "书房": ["书房"],
    "图书室": ["书房"],
    "图书馆": ["书房", "大学讲堂"],
    "酒吧": ["室内场景 - 酒吧/夜总会", "酒吧"],
    "酒馆": ["室内场景 - 酒吧/夜总会", "酒吧"],
    "夜总会": ["室内场景 - 酒吧/夜总会"],
    "码头": ["港口码头", "码头夜景"],
    "港口": ["港口码头", "码头夜景"],
    "森林": ["森林秘境", "森林小屋"],
    "树林": ["森林秘境", "森林小屋"],
    "灯塔": ["风暴灯塔异象"],
    "博物馆": ["博物馆展厅", "博物馆展厅/异常现场"],
    "展厅": ["博物馆展厅", "博物馆展厅/异常现场"],
    "办公室": ["办公室", "调查办公室", "侦探办公室"],
    "事务所": ["调查办公室", "侦探办公室"],
    "隧道": ["地下隧道/下水道"],
    "下水道": ["地下隧道/下水道"],
    "地下": ["地下隧道/下水道", "超自然水下遗迹"],
    "沼泽": ["沼泽湿地", "沼泽木屋", "沼泽秘境"],
    "湿地": ["沼泽湿地"],
    "餐厅": ["路边餐馆 (Diner)"],
    "餐馆": ["路边餐馆 (Diner)"],
    "实验室": ["医院病房"],
    "工厂": [],
    "废弃": [],
}


class SceneMatch:
    """匹配结果。"""

    def __init__(self, filename: str, score: float, tags: dict):
        self.filename = filename
        self.score = score
        self.tags = tags

    @property
    def image_path(self) -> str:
        return f"/images/scenes/{self.filename}"

    @property
    def location(self) -> str:
        return self.tags.get("location", "")

    @property
    def mood(self) -> str:
        moods = self.tags.get("mood", [])
        return moods[0] if moods else ""

    def as_overlay_dict(self) -> dict:
        return {
            "image": self.filename,
            "location": self.location,
            "mood": self.mood,
        }


class SceneMatcher:
    """场景图匹配引擎。"""

    def __init__(self, tags_path: Path | None = None):
        self._tags_path = tags_path or _TAGS_PATH
        self._scenes_dir = _SCENES_DIR
        self._images: dict[str, dict] = {}
        self._loaded = False

    def load(self) -> int:
        """加载标签文件，返回场景数量。"""
        if not self._tags_path.is_file():
            log.warning("场景标签文件不存在: %s", self._tags_path)
            return 0

        with open(self._tags_path, encoding="utf-8") as f:
            data = json.load(f)

        self._images = data.get("images", {})
        self._loaded = True
        log.info("SceneMatcher 加载 %d 个场景", len(self._images))
        return len(self._images)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    @property
    def scene_count(self) -> int:
        return len(self._images)

    @property
    def images_dir(self) -> Path:
        return self._scenes_dir

    def _tokenize(self, text: str) -> list[str]:
        """中文分词：使用 jieba 分词，过滤短词。"""
        try:
            import jieba
        except ImportError:
            jieba = None

        if jieba:
            tokens = jieba.lcut(text)
        else:
            import re
            tokens = re.split(r"[，。、；：！？\s,.;:!?\n]+", text)

        # 过滤：保留 ≥2 字的词
        result = [t.strip() for t in tokens if t.strip() and len(t.strip()) >= 2]
        return result

    def _expand_keywords(self, keywords: list[str]) -> set[str]:
        """扩展关键词：添加别名、近义场景类型。"""
        expanded = set(keywords)
        for kw in keywords:
            for alias, targets in _SCENE_TYPE_ALIASES.items():
                if alias in kw:
                    expanded.update(targets)
        return expanded

    def _score_scene(self, scene_tags: dict, keywords: set[str]) -> float:
        """计算场景与关键词的匹配分数。"""
        score = 0.0

        for dim, weight in _DIMENSION_WEIGHTS.items():
            value = scene_tags.get(dim)
            if value is None:
                continue

            if isinstance(value, list):
                # 数组维度（mood, key_objects, coc_themes）
                for item in value:
                    item_lower = str(item).lower()
                    for kw in keywords:
                        if kw.lower() in item_lower or item_lower in kw.lower():
                            score += weight
                            break
            else:
                # 字符串维度
                val_lower = str(value).lower()
                for kw in keywords:
                    kw_lower = kw.lower()
                    if kw_lower in val_lower or val_lower in kw_lower:
                        score += weight
                        break

        return score

    def match(
        self,
        text: str,
        *,
        top_k: int = 3,
        min_score: float = 0.5,
    ) -> list[SceneMatch]:
        """根据文本描述匹配场景图。

        Args:
            text: KP 叙述文本或场景描述
            top_k: 返回前 K 个匹配
            min_score: 最低匹配分

        Returns:
            按分值降序的场景匹配列表
        """
        self.ensure_loaded()

        if not self._images:
            return []

        # 分词 + 扩展
        keywords = self._tokenize(text)
        expanded = self._expand_keywords(keywords)

        results = []
        for filename, tags in self._images.items():
            score = self._score_scene(tags, expanded)
            if score >= min_score:
                results.append(SceneMatch(filename, score, tags))

        results.sort(key=lambda m: -m.score)
        return results[:top_k]

    def match_exact_scene_type(self, scene_type: str) -> list[SceneMatch]:
        """按场景类型精确匹配（忽略别名扩展）。"""
        self.ensure_loaded()

        results = []
        st_lower = scene_type.lower()
        for filename, tags in self._images.items():
            if st_lower in tags.get("scene_type", "").lower():
                results.append(SceneMatch(filename, 10.0, tags))

        results.sort(key=lambda m: -m.score)
        return results

    def get_scene(self, filename: str) -> dict | None:
        """获取单个场景的标签。"""
        self.ensure_loaded()
        return self._images.get(filename)

    def list_scene_types(self) -> list[str]:
        """列出所有不重复的场景类型。"""
        self.ensure_loaded()
        types = set()
        for tags in self._images.values():
            st = tags.get("scene_type", "")
            if st:
                types.add(st)
        return sorted(types)
