# -*- coding: utf-8 -*-

import json
import sqlite3
import urllib.request
import sys
from collections import Counter, defaultdict

DB_FILE = "new_macau.db"

RED = {
    1, 2, 7, 8, 12, 13, 18, 19, 23, 24,
    29, 30, 34, 35, 40, 45, 46
}
BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25, 26,
    31, 36, 37, 41, 42, 47, 48
}
GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

ELEMENTS = {
    "金": [5, 6, 13, 14, 21, 22, 35, 36, 43, 44],
    "木": [3, 4, 17, 18, 25, 26, 39, 40, 47, 48],
    "水": [1, 2, 15, 16, 23, 24, 37, 38, 45, 46],
    "火": [7, 8, 19, 20, 27, 28, 41, 42, 49],
    "土": [9, 10, 11, 12, 29, 30, 31, 32, 33, 34]
}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        issue TEXT PRIMARY KEY,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER
    )
    """)
    conn.commit()
    conn.close()


def fetch_api_data():
    """从 marksix6.net 获取新澳门彩数据，保留最近 30 期"""
    url = "https://marksix6.net/index.php?api=1"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Cache-Control": "no-cache"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        print(f"API获取成功: {url}")
        result = []
        lottery_list = data.get("lottery_data", [])

        target = None
        for lottery in lottery_list:
            if "新澳门" in lottery.get("name", ""):
                target = lottery
                break
        if not target and lottery_list:
            target = lottery_list[0]

        if not target:
            print("⚠️ 未找到新澳门彩数据")
            return []

        def parse_open_code(code_str):
            try:
                nums = [int(x.strip()) for x in code_str.split(",") if x.strip().isdigit()]
                if len(nums) >= 7:
                    return nums[:6], nums[6]
            except:
                pass
            return [], None

        latest_code = target.get("openCode", "")
        main_nums, special_num = parse_open_code(latest_code)
        if main_nums and special_num is not None:
            result.append({
                "issue": str(target.get("expect", "")).strip(),
                "numbers": main_nums,
                "special": special_num
            })

        histories = target.get("history", [])
        for item in histories:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if "期：" in item:
                parts = item.split("期：", 1)
                issue = parts[0].strip()
                code_part = parts[1]
                nums = [int(x.strip()) for x in code_part.split(",") if x.strip().isdigit()]
                if len(nums) >= 7:
                    result.append({"issue": issue, "numbers": nums[:6], "special": nums[6]})
            else:
                parts = item.split()
                if len(parts) >= 8:
                    issue = parts[0]
                    nums = []
                    for x in parts[1:7]:
                        try:
                            nums.append(int(x))
                        except:
                            break
                    else:
                        special_str = parts[-1].replace("+", "")
                        try:
                            special = int(special_str)
                            result.append({"issue": issue, "numbers": nums, "special": special})
                        except:
                            pass

        uniq = {}
        for r in result:
            if r["issue"] not in uniq:
                uniq[r["issue"]] = r
        result = list(uniq.values())
        result.sort(key=lambda x: x["issue"])

        # 保留最近 30 期
        if len(result) > 30:
            result = result[-30:]

        print(f"抓取到历史数据: {len(result)} 条（保留最近30期）")
        return result

    except Exception as e:
        print(f"API失败: {e}")
        return []


def save_records(records):
    conn = sqlite3.connect(DB_FILE)
    new_count = 0
    for r in records:
        issue = r["issue"]
        nums = r["numbers"]
        special = r["special"]
        if len(nums) < 6:
            continue
        cur = conn.execute("SELECT issue FROM lottery WHERE issue=?", (issue,)).fetchone()
        if not cur:
            new_count += 1
        conn.execute("INSERT OR REPLACE INTO lottery VALUES (?,?,?,?,?,?,?,?)",
                     (issue, nums[0], nums[1], nums[2], nums[3], nums[4], nums[5], special))
    conn.commit()
    conn.close()
    return new_count


def load_records():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT * FROM lottery ORDER BY issue").fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "issue": r[0],
            "numbers": [r[1], r[2], r[3], r[4], r[5], r[6]],
            "special": r[7]
        })
    return result


# ----------------- 属性工具 -----------------
def get_wave(n):
    if n in RED: return "红"
    if n in BLUE: return "蓝"
    return "绿"

def get_element(n):
    for k, arr in ELEMENTS.items():
        if n in arr: return k
    return "?"

def get_size(n): return "大" if n >= 25 else "小"
def get_odd_even(n): return "单" if n % 2 else "双"
def get_tail_size(n): return "尾大" if n % 10 >= 5 else "尾小"
def get_sum_odd_even(n):
    s = sum(map(int, str(n)))
    return "合单" if s % 2 else "合双"
def get_sum_size(n):
    s = sum(map(int, str(n)))
    return "合大" if s >= 7 else "合小"

def special_text(n):
    return (f"{get_odd_even(n)}/{get_size(n)} "
            f"{get_sum_odd_even(n)}/{get_sum_size(n)} "
            f"{get_tail_size(n)} {get_wave(n)} {get_element(n)}")


# ----------------- 基础策略生成器 -----------------
def generate_strategy(records, strategy_name):
    """返回 (正码列表, 特码)，若数据不足返回 (None, None)"""
    if len(records) < 7:
        return None, None

    if strategy_name == "热号策略":
        freq = Counter()
        for r in records[-20:]:
            freq.update(r["numbers"])
        ranked = freq.most_common(7)
        if len(ranked) < 7:
            return None, None
        return [n for n, _ in ranked[:6]], ranked[6][0]

    elif strategy_name == "冷号回补":
        freq = Counter()
        for r in records[-30:]:
            freq.update(r["numbers"])
        missing = sorted([n for n in range(1, 50) if n not in freq])
        if len(missing) < 7:
            return None, None
        return missing[:6], missing[6]

    elif strategy_name == "近期动量":
        scores = Counter()
        for i, r in enumerate(records[-30:]):
            w = 1.0 / (1 + i)
            for n in r["numbers"]:
                scores[n] += w
        ranked = scores.most_common(7)
        if len(ranked) < 7:
            return None, None
        return [n for n, _ in ranked[:6]], ranked[6][0]

    elif strategy_name == "规律挖掘":
        recent = records[-5:]
        seen = []
        for r in recent:
            for n in r["numbers"]:
                if n not in seen:
                    seen.append(n)
        if len(seen) < 6:
            return None, None
        return seen[:6], seen[0]

    else:
        return None, None


# ----------------- 自动调优：基于最近 5 期滚动评估 -----------------
def evaluate_strategies(records, window=5):
    """
    对每种策略在最近 window 期进行留一法滚动回测，
    返回字典 {策略名: 平均命中数}
    """
    if len(records) < window + 10:
        return {}

    strategies = ["热号策略", "冷号回补", "近期动量", "规律挖掘"]
    performance = {s: [] for s in strategies}

    start_idx = len(records) - window - 1
    for i in range(start_idx, len(records) - 1):
        train = records[:i]
        actual = set(records[i]["numbers"])
        for s in strategies:
            main, _ = generate_strategy(train, s)
            if main is None:
                continue
            hit = sum(1 for n in main if n in actual)
            performance[s].append(hit)

    avg_perf = {}
    for s, hits in performance.items():
        if hits:
            avg_perf[s] = sum(hits) / len(hits)
    return avg_perf


# ----------------- 集成投票（动态权重） -----------------
def ensemble_with_weights(records, strategy_weights):
    """
    根据各策略的权重，对号码进行加权投票，返回 (正码列表, 特码)
    """
    if not strategy_weights or len(records) < 10:
        return None, None

    total_score = Counter()
    for s, w in strategy_weights.items():
        main, special = generate_strategy(records, s)
        if main is None:
            continue
        for num in main:
            total_score[num] += w

    if len(total_score) < 6:
        return None, None
    ranked = total_score.most_common(6)
    return [n for n, _ in ranked], ranked[0][0]


# ----------------- 主逻辑 -----------------
def sync():
    init_db()
    records = fetch_api_data()
    if not records:
        print("未抓到真实开奖数据")
        return

    new_count = save_records(records)
    all_records = load_records()
    print(f"数据同步完成: total={len(all_records)}, new={new_count}")

    if len(all_records) < 10:
        print("⚠️ 历史数据不足10期，无法进行预测")
        return

    latest = all_records[-1]
    print(f"最新开奖: {latest['issue']} | "
          f"{' '.join([str(x).zfill(2) for x in latest['numbers']])} "
          f"+ {str(latest['special']).zfill(2)}")
    next_issue = str(int(latest["issue"]) + 1)
    print(f"\n预测期号: {next_issue}")

    # 1. 评估各策略最近5期表现
    perf = evaluate_strategies(all_records, window=5)
    print("\n📈 最近5期各策略平均命中数（用于自动调权）:")
    strategy_weights = {}
    if perf:
        for s, avg in sorted(perf.items(), key=lambda x: x[1], reverse=True):
            print(f"   {s}: {avg:.2f} 个/期")
        max_hit = max(perf.values())
        for s, avg in perf.items():
            strategy_weights[s] = 0.1 + (avg / max_hit) * 0.9 if max_hit > 0 else 1.0
    else:
        print("   数据不足，使用默认等权重")
        strategy_weights = {s: 1.0 for s in ["热号策略", "冷号回补", "近期动量", "规律挖掘"]}

    # 2. 固定策略展示
    print("\n固定策略预测:")
    strategies = ["热号策略", "冷号回补", "近期动量", "规律挖掘"]
    for s in strategies:
        main, special = generate_strategy(all_records, s)
        if main is None:
            print(f"  {s}: 数据不足")
            continue
        nums_str = " ".join([str(x).zfill(2) for x in main])
        sp_str = str(special).zfill(2) if special else "--"
        print(f"  {s}: {nums_str} + {sp_str}")
        if special:
            print(f"         特码属性: {special_text(special)}")

    # 3. 动态集成策略
    print("\n🧠 动态集成策略（基于5期表现加权）:")
    ens_main, ens_special = ensemble_with_weights(all_records, strategy_weights)
    if ens_main is None:
        print("   无法生成")
    else:
        ens_nums = " ".join([str(x).zfill(2) for x in ens_main])
        print(f"  集成投票: {ens_nums} + {str(ens_special).zfill(2)}")
        if ens_special:
            print(f"         特码属性: {special_text(ens_special)}")

    # 4. 波色预测
    if len(all_records) >= 10:
        specials = [r["special"] for r in all_records[-10:]]
        scores = defaultdict(int)
        for i, sp in enumerate(reversed(specials)):
            scores[get_wave(sp)] += 10 - i
        sorted_wave = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print("\n🎨 特码波色预测（加权）:")
        print(f"   主强: {sorted_wave[0][0]} (得分 {sorted_wave[0][1]})   "
              f"次强: {sorted_wave[1][0]} (得分 {sorted_wave[1][1]})")

        # 大小单双预测
        recent_specials = [r["special"] for r in all_records[-10:]]
        big = small = odd = even = 0
        for sp in recent_specials:
            if sp >= 25: big += 1
            else: small += 1
            if sp % 2: odd += 1
            else: even += 1
        print("📊 大小单双预测:")
        print(f"   大小: {'大' if big >= small else '小'}   单双: {'单' if odd >= even else '双'}")

    else:
        print("\n波色预测数据不足")


if __name__ == "__main__":
    cmd = "sync" if len(sys.argv) <= 1 else sys.argv[1]
    if cmd == "sync":
        sync()