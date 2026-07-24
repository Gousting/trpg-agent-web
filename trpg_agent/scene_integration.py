"""场景匹配集成——将 DMBrain 的 turn 完成事件连接到 overlay server 的场景切换。

用法（在创建 DMBrain 时传入）:

    from trpg_agent.scene_integration import make_scene_callback
    brain = DMBrain(client, on_turn_complete=make_scene_callback())
"""

from __future__ import annotations

import json
import logging
from typing import Callable

import requests

log = logging.getLogger(__name__)

# Overlay server 默认地址（同机部署）
DEFAULT_OVERLAY_URL = "http://localhost:8766/api/scene/match"


def make_scene_callback(
    overlay_url: str = DEFAULT_OVERLAY_URL,
    timeout: float = 5.0,
) -> Callable[[str], None]:
    """返回一个同步回调函数，接受 KP 旁白文本并触发场景匹配。

    Args:
        overlay_url: overlay server 的 /api/scene/match 地址
        timeout: HTTP 请求超时（秒）
    """

    def _on_turn_complete(answer_text: str) -> None:
        """每次 KP turn 完成后调用，自动匹配场景图。"""
        if not answer_text or len(answer_text.strip()) < 10:
            return  # 太短的文本不匹配

        try:
            resp = requests.post(
                overlay_url,
                json={"text": answer_text},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    matched = data.get("matched", {})
                    log.info(
                        "场景自动切换: %s (score=%.1f) → %s",
                        matched.get("image", "?"),
                        matched.get("score", 0),
                        matched.get("location", "?"),
                    )
                else:
                    log.debug("场景匹配无结果: %s", data.get("error", "unknown"))
            else:
                log.warning("场景匹配请求失败: HTTP %d", resp.status_code)
        except requests.exceptions.ConnectionError:
            log.debug("Overlay server 未运行（%s），跳过场景匹配", overlay_url)
        except requests.exceptions.Timeout:
            log.debug("场景匹配请求超时")
        except Exception:
            log.debug("场景匹配异常", exc_info=True)

    return _on_turn_complete
