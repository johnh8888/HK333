#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sqlite3
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# =========================================================
# 基础配置
# =========================================================

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = str(SCRIPT_DIR / "new_macau.db")

# 新澳门彩真实数据源
DATA_URL = "https://api3.marksix6.net/lottery_api.php?type=newMacau"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "ensemble_v2": "集成投票",
}

STRATEGY_IDS = [
    "balanced_v1",
    "hot_v1",
    "cold_rebound_v1",
    "momentum_v1",
    "ensemble_v2",
]

# =========================================================
# 数据结构
# =========================================================

@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


# =========================================================
# 波色映射（真实六合彩）
# =========================================================

RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE_WAVE = {
    3, 4, 9, 10, 14, 15, 20,
    25, 26, 31, 36, 37, 41,
    42, 47, 48
}

GREEN_WAVE = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}


def get_color(num: int) -> str:
    if num in RED_WAVE:
        return "红"
    if num in BLUE_WAVE:
        return "蓝"
    return "绿"


# =========================================================
# 特码属性
# =========================================================

def special_attributes(num: int):
    odd_even = "单" if num % 2 else "双"
    big_small = "大" if num >= 25 else "小"

    if num % 10 in [1, 6]:
        element = "水"
    elif num % 10 in [2, 7]:
        element = "火"
    elif num % 10 in [3, 8]:
        element = "木"
    elif num % 10 in [4, 9]:
        element = "金"
    else:
        element = "土"

    return {
        "单双": odd_even,
        "大小": big_small,
        "色波": get_color(num),
        "五行": element
    }


# =========================================================
# 数据库
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS draws (
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        numbers_json TEXT,
        special_number INTEGER,
        created_at TEXT
    );
    """)
    conn.commit()


# =========================================================
# 真实数据获取（新澳门彩）-- 增强错误处理
# =========================================================

def fetch_real_data():
    """拉取真实开奖数据，增加详细错误日志"""
    req = Request(
        DATA_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        resp = urlopen(req, timeout=20)
    except HTTPError as e:
        print(f"❌ HTTP错误: {e.code} {e.reason}")
        return []
    except URLError as e:
        print(f"❌ 网络错误: {e.reason}")
        return []
    except Exception as e:
        print(f"❌ 未知网络错误: {e}")
        return []

    if resp.status != 200:
        print(f"⚠️ 状态码异常: {resp.status}")
        return []

    raw = resp.read().decode("utf-8")
    try:
        payload = json.loads(raw)
    except Exception as e:
        print(f"❌ JSON解析失败: {e}")
        print(f"原始响应前200字符: {raw[:200]}")
        return []

    data = payload.get("data", [])
    if not data:
        print("⚠️ API返回的data为空，完整响应:", json.dumps(payload, ensure_ascii=False)[:500])
        return []

    records = []
    for item in data:
        try:
            issue = str(item.get("expect", "")).strip()
            opencode = item.get("opencode", "")
            nums = [int(x) for x in opencode.split(",")]
            if len(nums) != 7:
                continue
            draw_date = str(item.get("opentime", ""))[:10]
            records.append(
                DrawRecord(
                    issue_no=issue,
                    draw_date=draw_date,
                    numbers=nums[:6],
                    special_number=nums[6]
                )
            )
        except Exception:
            print(f"⚠️ 解析单条数据失败: {item}")
            traceback.print_exc()
            continue

    return records


# =========================================================
# 保存数据
# =========================================================

def save_records(conn, records):
    now = utc_now()
    count = 0
    for r in records:
        conn.execute("""
        INSERT OR REPLACE INTO draws
        VALUES (?,?,?,?,?)
        """, (
            r.issue_no,
            r.draw_date,
            json.dumps(r.numbers),
            r.special_number,
            now
        ))
        count += 1
    conn.commit()
    return count


# =========================================================
# 预测
# =========================================================

def predict_color_weighted(specials, window=10):
    history = specials[-window:]
    scores = defaultdict(float)
    total_weight = 0
    for i, n in enumerate(reversed(history)):
        weight = window - i
        scores[get_color(n)] += weight
        total_weight += weight
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    main = ranked[0][0]
    second = ranked[1][0]
    return (
        main,
        second,
        ranked[0][1] / total_weight,
        ranked[1][1] / total_weight
    )


def predict_big_small(specials):
    recent = specials[-10:]
    big = sum(1 for n in recent if n >= 25)
    small = 10 - big
    return "大" if big >= small else "小"


def predict_odd_even(specials):
    recent = specials[-10:]
    odd = sum(1 for n in recent if n % 2)
    even = 10 - odd
    return "单" if odd >= even else "双"


# =========================================================
# 策略
# =========================================================

def get_draws(conn):
    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    """).fetchall()
    result = []
    for r in rows:
        nums = json.loads(r["numbers_json"])
        result.append(nums)
    return result


def freq_map(draws):
    m = Counter()
    for d in draws:
        m.update(d)
    return m


def omission_map(draws):
    result = {}
    for n in ALL_NUMBERS:
        miss = 0
        found = False
        for d in draws:
            if n in d:
                found = True
                break
            miss += 1
        result[n] = miss if found else 999
    return result


def hot_strategy(draws):
    freq = freq_map(draws[:50])
    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    nums = [n for n, _ in ranked[:6]]
    special = ranked[6][0]
    return nums, special


def cold_strategy(draws):
    omit = omission_map(draws[:50])
    ranked = sorted(omit.items(), key=lambda x: x[1], reverse=True)
    nums = [n for n, _ in ranked[:6]]
    special = ranked[6][0]
    return nums, special


def momentum_strategy(draws):
    score = defaultdict(float)
    for i, d in enumerate(draws[:30]):
        weight = 30 - i
        for n in d:
            score[n] += weight
    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)
    nums = [n for n, _ in ranked[:6]]
    special = ranked[6][0]
    return nums, special


def balanced_strategy(draws):
    hot, _ = hot_strategy(draws)
    cold, _ = cold_strategy(draws)
    result = []
    for n in hot + cold:
        if n not in result:
            result.append(n)
    nums = result[:6]
    special = result[6]
    return nums, special


def ensemble_strategy(draws):
    score = defaultdict(int)
    for func in [hot_strategy, cold_strategy, momentum_strategy, balanced_strategy]:
        nums, _ = func(draws)
        for i, n in enumerate(nums):
            score[n] += 10 - i
    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)
    nums = [n for n, _ in ranked[:6]]
    special = ranked[6][0]
    return nums, special


# =========================================================
# 真实波色回测（二中一）
# =========================================================

def color_backtest_real(conn, recent=10, window=10):
    rows = conn.execute("""
    SELECT special_number
    FROM draws
    ORDER BY issue_no ASC
    """).fetchall()
    specials = [int(r["special_number"]) for r in rows]
    if len(specials) < recent + window:
        return 0, 0
    hit = 0
    total = 0
    for i in range(len(specials) - recent, len(specials)):
        history = specials[:i]
        if len(history) < window:
            continue
        main_color, second_color, _, _ = predict_color_weighted(history, window)
        actual = get_color(specials[i])
        if actual in [main_color, second_color]:
            hit += 1
        total += 1
    return hit, total


def color_max_miss_real(conn, recent=10, window=10):
    rows = conn.execute("""
    SELECT special_number
    FROM draws
    ORDER BY issue_no ASC
    """).fetchall()
    specials = [int(r["special_number"]) for r in rows]
    miss = 0
    max_miss = 0
    for i in range(len(specials) - recent, len(specials)):
        history = specials[:i]
        if len(history) < window:
            continue
        main_color, second_color, _, _ = predict_color_weighted(history, window)
        actual = get_color(specials[i])
        if actual in [main_color, second_color]:
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)
    return max_miss


# =========================================================
# 仪表盘展示
# =========================================================

def show_dashboard(conn):
    latest = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no DESC
    LIMIT 1
    """).fetchone()

    if not latest:
        print("⚠️ 数据库无任何开奖记录，无法预测")
        return

    nums = json.loads(latest["numbers_json"])
    nums_str = " ".join(f"{x:02d}" for x in nums)

    print("最新开奖:")
    print(f"{latest['issue_no']} | {nums_str} + {latest['special_number']:02d}")
    print()

    next_issue = str(int(latest["issue_no"]) + 1)
    print(f"预测期号: {next_issue}")

    draws = get_draws(conn)
    strategy_funcs = {
        "balanced_v1": balanced_strategy,
        "hot_v1": hot_strategy,
        "cold_rebound_v1": cold_strategy,
        "momentum_v1": momentum_strategy,
        "ensemble_v2": ensemble_strategy
    }

    for sid in STRATEGY_IDS:
        nums, special = strategy_funcs[sid](draws)
        label = STRATEGY_LABELS[sid]
        nums_str = " ".join(f"{x:02d}" for x in nums)
        print(f"{label:<12}: {nums_str} + {special:02d}")
        attrs = special_attributes(special)
        print(f"特码属性: {attrs['单双']}/{attrs['大小']} {attrs['色波']} {attrs['五行']}")

    specials = [
        int(r["special_number"])
        for r in conn.execute("""
        SELECT special_number
        FROM draws
        ORDER BY issue_no ASC
        """).fetchall()
    ]

    print("\n特码波色预测（最近10期真实数据）:")
    main_color, second_color, _, _ = predict_color_weighted(specials, 10)
    print(f"主强: {main_color} 次强: {second_color}")

    print("\n大小单双预测（最近10期真实数据）:")
    print(f"大小: {predict_big_small(specials)}")
    print(f"单双: {predict_odd_even(specials)}")

    # 真实回测
    hit, total = color_backtest_real(conn, 10, 10)
    rate = (hit / total * 100) if total else 0
    print(f"\n最近10期真实波色回测（二中一）:")
    print(f"命中: {hit}/{total}")
    print(f"命中率: {rate:.1f}%")

    miss = color_max_miss_real(conn, 10, 10)
    print(f"\n真实最大连空（二中一）: {miss}期")

    print("\n推荐投注方案:")
    print(f"{main_color}: 300 元")
    print(f"{predict_big_small(specials)}: 200 元")
    print(f"{predict_odd_even(specials)}: 200 元")

    print("\n赔率参考:")
    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小单双: 1.95")


# =========================================================
# 主流程
# =========================================================

def sync():
    conn = connect_db()
    init_db(conn)

    # 尝试拉取最新数据
    records = fetch_real_data()
    if records:
        count = save_records(conn, records)
        print(f"✅ 成功保存 {count} 条新记录")
    else:
        print("⚠️ 未获取到新开奖数据，将基于现有数据库进行分析")

    # 无论是否拉取到新数据，只要数据库有历史记录就输出预测
    row = conn.execute("SELECT COUNT(*) FROM draws").fetchone()
    if row[0] == 0:
        print("❌ 数据库无任何开奖记录，无法生成预测")
    else:
        show_dashboard(conn)

    conn.close()


if __name__ == "__main__":
    sync()