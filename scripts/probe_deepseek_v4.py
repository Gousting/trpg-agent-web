"""deepseek-v4-flash (zen/go) 真实负载探针——验证推理模型对 KP 延迟的影响。"""
import json
import time
import urllib.request

ZEN_GO = "https://opencode.ai/zen/go/v1/chat/completions"
KEY = "sk-YM9rZy0FSPgC8MElvPmRjfkg3sztv0jHamErKBaE7wwLHHyyeJTYyo0fgdHIqBrV"
MODEL = "deepseek-v4-flash"

SYSTEM = (
    "你是《克苏鲁的呼唤》跑团主持人 KP。当前世界观：无限流主神空间。规则：玩家是轮回者，"
    "拥有力量/敏捷/精神三维属性与 HP/SAN，战斗用 2d6 判定。叙事要求：中文，画面感强，"
    "每段不超过 300 字，结尾提供 3 个行动选项（A/B/C），不要替玩家做决定。"
)
SCENE = (
    "【场景】咒怨·佐伯家凶宅。你们站在黄昏的日式老宅前，门牌写着「佐伯」，字迹被液体浸过模糊了半边。"
    "空气冷得不正常，院子里的枯树轻轻晃动。二楼的一扇窗户黑洞洞地注视着你们。"
    "【轮回者状态】HP 12/12 | 力量 10 | 敏捷 10 | 精神 10 | AP 15 | SAN 50"
    "【玩家行动】调查员 A 说：我检查信箱；调查员 B 说：我绕到房子后面看看有没有后门。"
)


def probe(max_tokens: int, extra: dict | None = None) -> dict:
    body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": SCENE + "\n\n请继续剧情。"}], "max_tokens": max_tokens}
    if extra:
        body.update(extra)
    req = urllib.request.Request(ZEN_GO, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
                                          "User-Agent": "curl/8.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    total = time.time() - t0
    msg = data.get("choices", [{}])[0].get("message", {})
    usage = data.get("usage", {})
    content = msg.get("content", "")
    reasoning = msg.get("reasoning_content", "")
    ct = usage.get("completion_tokens", 0)
    rt = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
    return {
        "total": round(total, 1),
        "content_chars": len(content),
        "completion_tokens": ct,
        "reasoning_tokens": rt,
        "visible_pct": round(100 * (ct - rt) / ct, 1) if ct else 0,
        "sample": content[:80].replace("\n", " "),
    }


def main():
    print(f"=== {MODEL} 真实 KP 参数 max_tokens=4000 × 2 轮 ===")
    for i in range(2):
        try:
            r = probe(4000)
            print(f"[{i+1}] {r}")
        except Exception as e:
            print(f"[{i+1}] ERR {str(e)[:200]}")

    print(f"\n=== {MODEL} reasoning_effort=low × 2 轮 (max_tokens=4000) ===")
    for i in range(2):
        try:
            r = probe(4000, {"reasoning_effort": "low"})
            print(f"[{i+1}] {r}")
        except Exception as e:
            print(f"[{i+1}] ERR {str(e)[:200]}")

    print(f"\n=== {MODEL} enable_thinking=False × 1 轮 (max_tokens=4000) ===")
    try:
        r = probe(4000, {"enable_thinking": False})
        print(f"[1] {r}")
    except Exception as e:
        print(f"[1] ERR {str(e)[:200]}")


if __name__ == "__main__":
    main()
