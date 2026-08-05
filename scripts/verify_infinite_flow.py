"""验证无限流模块池：组合连通性 + 是否可达 BOSS + 时长估算"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.adventure.module_composer import ModuleComposer

composer = ModuleComposer(Path("data/modules_infinite_flow"))
n = composer.load_all()
print(f"加载模块: {n}")
issues = composer.validate()
print("静态校验:", issues if issues else "无问题")


def _report(adv, seed: int) -> None:
    """统计组合结果：场景数、模块数、BOSS 可达性、链路"""
    ids = list(adv._scenes.keys())
    mods = sorted({i.split("::")[0] for i in ids})
    has_boss = any("boss" in i for i in ids)
    # BFS 从 start_scene 出发，走 leads_to
    start = adv.start_scene
    reachable = set()
    queue = [start]
    while queue:
        cur = queue.pop(0)
        if cur in reachable:
            continue
        reachable.add(cur)
        sc = adv.get_scene(cur)
        if sc:
            queue.extend(t for t in sc.leads_to if t not in reachable)
    boss_reachable = any("boss" in r for r in reachable)
    print(f"seed={seed}: 场景数={len(ids)} 模块数={len(mods)} 含BOSS={has_boss} BOSS可达={boss_reachable} start={start}")
    print("   模块:", mods)
    if has_boss:
        # 找一条到 boss 的路径
        path = _find_path(adv, start, next(r for r in reachable if "boss" in r))
        print("   到BOSS路径:", " -> ".join(path[:20]))


def _find_path(adv, start: str, target: str) -> list[str]:
    from collections import deque

    q = deque([[start]])
    seen = {start}
    while q:
        path = q.popleft()
        cur = path[-1]
        if cur == target:
            return path
        sc = adv.get_scene(cur)
        if not sc:
            continue
        for nxt in sc.leads_to:
            if nxt not in seen:
                seen.add(nxt)
                q.append(path + [nxt])
    return []

print("\n=== 组合测试 (start=hub_plaza, authored_only=True) ===")
for seed in [42, 7, 123, 777, 2024, 999, 555]:
    try:
        adv, run_seed = composer.compose(
            seed=seed, start_module="hub_plaza", authored_only=True, max_depth=8
        )
        _report(adv, seed)
    except Exception as e:
        print(f"seed={seed}: ERR {type(e).__name__}: {e}")

print("\n=== 组合测试 (authored_only=False) ===")
for seed in [42, 777]:
    try:
        adv, run_seed = composer.compose(
            seed=seed, start_module="hub_plaza", authored_only=False, max_depth=8
        )
        _report(adv, seed)
    except Exception as e:
        print(f"seed={seed}: ERR {type(e).__name__}: {e}")
