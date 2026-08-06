"""KP 生成延迟探针——模拟真实跑团场景的 LLM 调用，测各端点的单轮耗时。

用法: python scripts/latency_probe.py [--rounds N] [--tokens N]
默认: 每个端点测 2 轮，max_tokens 400（一段场景描述 + 3 个选项的量级）
"""
import argparse
import json
import sys
import time
import urllib.request

OLLAMA = "http://192.168.0.106:11434"
ZEN_KEY = "sk-ee06LCJ2weQcOOMlap1x0PMsEa7xCuPzj9Rrw7qGHb51JMEu64JC8GfgfjJ6vTAs"

# 模拟一局中 KP 的真实输入：系统提示（世界观规则）+ 当前场景 + 玩家行动 + 要求输出
SYSTEM = (
    "你是《克苏鲁的呼唤》跑团主持人 KP。当前世界观：无限流主神空间。规则：玩家是轮回者，"
    "拥有力量/敏捷/精神三维属性与 HP/SAN，战斗用 2d6 判定。叙事要求：中文，画面感强，"
    "每段不超过 300 字，结尾提供 3 个行动选项（A/B/C），不要替玩家做决定。"
    "严格输出格式：<!--GS scene --> ... <!--GS end -->"
)
SCENE = (
    "【场景】咒怨·佐伯家凶宅。你们站在黄昏的日式老宅前，门牌写着「佐伯」，字迹被液体浸过模糊了半边。"
    "空气冷得不正常，院子里的枯树轻轻晃动。二楼的一扇窗户黑洞洞地注视着你们。"
    "【轮回者状态】HP 12/12 | 力量 10 | 敏捷 10 | 精神 10 | AP 15 | SAN 50"
    "【玩家行动】调查员 A 说：我检查信箱；调查员 B 说：我绕到房子后面看看有没有后门。"
)


def ollama_generate(model: str, prompt: str, num_predict: int) -> tuple[float, float, int]:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0.8},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    total = time.time() - t0
    return total, data.get("eval_count", 0), data.get("eval_duration", 0) / 1e9


def zen_chat(model: str, system: str, user: str, max_tokens: int) -> tuple[float, float, int]:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.8,
    }).encode()
    req = urllib.request.Request(
        "https://opencode.ai/zen/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ZEN_KEY}"},
    )
    t0 = time.time()
    ttfb = None
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
        ttfb = time.time() - t0
        data = json.loads(raw)
    total = time.time() - t0
    out = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    return total, ttfb, len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--tokens", type=int, default=400)
    args = ap.parse_args()

    cases = [
        ("OLLAMA gemma4:12b (本地慢基线)", "ollama", "gemma4:12b"),
        ("OLLAMA ornith:9b (本地快)", "ollama", "ornith:9b"),
        ("ZEN nemotron-3-ultra-free", "zen", "nemotron-3-ultra-free"),
        ("ZEN deepseek-v4-flash-free", "zen", "deepseek-v4-flash-free"),
    ]

    results = []
    for name, kind, model in cases:
        row = {"name": name, "model": model, "rounds": []}
        for r in range(args.rounds):
            try:
                if kind == "ollama":
                    total, ntoks, evals = ollama_generate(model, f"{SYSTEM}\n\n{SCENE}\n\n请继续剧情。", args.tokens)
                    row["rounds"].append({"total": round(total, 1), "tokens": ntoks, "tps": round(ntoks / evals, 1) if evals else None})
                else:
                    total, ttfb, chars = zen_chat(model, SYSTEM, SCENE + "\n\n请继续剧情。", args.tokens)
                    row["rounds"].append({"total": round(total, 1), "ttfb": round(ttfb, 1), "chars": chars})
                print(f"[OK] {name} round{r+1}: {row['rounds'][-1]}")
            except Exception as e:
                row["rounds"].append({"error": str(e)[:200]})
                print(f"[ERR] {name} round{r+1}: {e}")
        results.append(row)

    print("\n===== 汇总 =====")
    for row in results:
        ok = [r for r in row["rounds"] if "error" not in r]
        if ok:
            avg = sum(r["total"] for r in ok) / len(ok)
            print(f"{row['name']}: avg {avg:.1f}s ({len(ok)}/{len(row['rounds'])} rounds ok)")
        else:
            print(f"{row['name']}: ALL FAILED - {row['rounds'][0]['error'][:100]}")

    with open("/tmp/latency_probe_results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("结果已存 /tmp/latency_probe_results.json")


if __name__ == "__main__":
    main()
