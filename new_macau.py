# -*- coding: utf-8 -*-

import json
import sqlite3
import urllib.request
import sys
from collections import Counter

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
    """从 marksix6.net 获取新澳门彩数据，只保留最近 10 期"""
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

        # 只保留最近 10 期
        if len(result) > 10:
            result = result[-10:]

        print(f"抓取到历史数据: {len(result)} 条（仅保留最近10期）")
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
        conn.execute("""
        INSERT OR REPLACE INTO lottery
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            issue,
            nums[0], nums[1], nums[2],
            nums[3], nums[4], nums[5],
            special
        ))
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


def predict_numbers(records):
    recent = records[-20:]
    freq = Counter()
    for r in recent:
        for n in r["numbers"]:
            freq[n] += 1
    hot = [x[0] for x in freq.most_common(6)]
    cold = [n for n in range(1, 50) if n not in hot][:6]
    momentum = hot[::-1]
    vote = hot[:]
    pattern = []
    for r in recent[-5:]:
        pattern.extend(r["numbers"])
    pattern = list(dict.fromkeys(pattern))[:6]
    combo = list(set(hot[:3] + cold[:3]))[:6]
    strategies = {
        "组合策略": combo,
        "冷号回补": cold,
        "集成投票": vote,
        "热号策略": hot,
        "近期动量": momentum,
        "规律挖掘": pattern
    }
    result = {}
    for k, nums in strategies.items():
        result[k] = {"nums": nums, "special": nums[0]}
    return result


def calc_wave_prediction(records):
    last10 = records[-10:]
    score = {"红": 0, "蓝": 0, "绿": 0}
    weight = min(10, len(last10))
    for i, r in enumerate(reversed(last10)):
        wave = get_wave(r["special"])
        score[wave] += weight - i
    sorted_wave = sorted(score.items(), key=lambda x: x[1], reverse=True)
    return sorted_wave[0], sorted_wave[1]


def backtest_wave(records):
    """波色回测，返回 (命中, 总次数, 最大连空)"""
    if len(records) < 2:
        return 0, 0, 0
    recent = records[-10:]  # 最多取最近10期
    hit = total = 0
    max_miss = miss = 0
    for i in range(2, len(recent)):   # 至少需要2期历史才能预测
        hist = recent[:i]
        main, second = calc_wave_prediction(hist)
        real = get_wave(recent[i]["special"])
        total += 1
        if real in [main[0], second[0]]:
            hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)
    return hit, total, max_miss


def predict_big_small(records):
    if len(records) < 1:
        return "数据不足", "数据不足"
    recent = records[-10:]
    big = small = odd = even = 0
    for r in recent:
        s = r["special"]
        if s >= 25: big += 1
        else: small += 1
        if s % 2: odd += 1
        else: even += 1
    return ("大" if big >= small else "小"), ("单" if odd >= even else "双")


def backtest_size_odd(records):
    if len(records) < 2:
        return 0, 0, 0, 0, 0
    recent = records[-10:]
    size_hit = odd_hit = 0
    size_miss = odd_miss = 0
    size_max = odd_max = 0
    total = 0
    for i in range(2, len(recent)):
        hist = recent[:i]
        size_pred, odd_pred = predict_big_small(hist)
        real = recent[i]["special"]
        total += 1
        real_size = "大" if real >= 25 else "小"
        real_odd = "单" if real % 2 else "双"
        if size_pred == real_size:
            size_hit += 1
            size_miss = 0
        else:
            size_miss += 1
            size_max = max(size_max, size_miss)
        if odd_pred == real_odd:
            odd_hit += 1
            odd_miss = 0
        else:
            odd_miss += 1
            odd_max = max(odd_max, odd_miss)
    return size_hit, odd_hit, total, size_max, odd_max


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
        print(f"⚠️ 历史数据不足10期，当前仅有 {len(all_records)} 期，部分预测可能不准")

    if len(all_records) == 0:
        print("无数据，退出")
        return

    latest = all_records[-1]
    print(
        f"最新开奖: {latest['issue']} | "
        f"{' '.join([str(x).zfill(2) for x in latest['numbers']])} "
        f"+ {str(latest['special']).zfill(2)}"
    )
    print()
    next_issue = str(int(latest["issue"]) + 1)
    print(f"预测期号: {next_issue}")

    # 策略预测
    predicts = predict_numbers(all_records)
    for k, v in predicts.items():
        nums = " ".join([str(x).zfill(2) for x in v["nums"]])
        sp = str(v["special"]).zfill(2)
        print(f"  {k}　　　　: {nums} + {sp}")
        print(f"         特码属性: {special_text(v['special'])}")

    print()

    # 波色预测与回测（只要有2期就做）
    if len(all_records) >= 2:
        # 波色预测
        main, second = calc_wave_prediction(all_records)
        print("🎨 特码波色预测（加权）：")
        print(f"   主强: {main[0]} (得分 {main[1]})   次强: {second[0]} (得分 {second[1]})")

        # 波色回测
        wave_hit, wave_total, wave_max_miss = backtest_wave(all_records)
        if wave_total > 0:
            print(f"\n📊 波色回测（最近{wave_total}期）：")
            print(f"   二中一命中: {wave_hit}/{wave_total}")
            print(f"   命中率: {round(wave_hit/wave_total*100,1)}%")
            print(f"   最大连空: {wave_max_miss}期")
    else:
        print("🎨 特码波色预测：数据不足")

    # 大小单双预测与回测
    if len(all_records) >= 2:
        size_pred, odd_pred = predict_big_small(all_records)
        print("\n📊 大小单双预测：")
        print(f"   大小: {size_pred}   单双: {odd_pred}")

        size_hit, odd_hit, total, size_max, odd_max = backtest_size_odd(all_records)
        if total > 0:
            print(f"\n📊 大小单双回测（最近{total}期）：")
            print(f"   大小命中: {size_hit}/{total} ({round(size_hit/total*100,1)}%)")
            print(f"   大小最大连空: {size_max}期")
            print(f"   单双命中: {odd_hit}/{total} ({round(odd_hit/total*100,1)}%)")
            print(f"   单双最大连空: {odd_max}期")
    else:
        print("\n📊 大小单双预测：数据不足")


if __name__ == "__main__":
    cmd = "sync" if len(sys.argv) <= 1 else sys.argv[1]
    if cmd == "sync":
        sync()