#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# =========================================================
# 新澳门六合彩 AI 预测系统 PRO V4 FINAL
# GitHub Actions 可直接运行
# Python 3.11+
# =========================================================

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "new_macau.db"

ALL_NUMBERS = list(range(1, 50))

API_URL = "https://marksix6.net/index.php?api=1"

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

AI_CONFIG = {
    "freq_weight": 0.28,
    "omit_weight": 0.20,
    "momentum_weight": 0.20,
    "cycle_weight": 0.18,
    "bayes_weight": 0.14,
    "recent_window": 120,
    "mc_simulations": 15000,
}

# =========================================================
# 工具
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return utc_now()[:10]

def normalize(score_map):
    vals = list(score_map.values())
    mn = min(vals)
    mx = max(vals)

    if mx == mn:
        return {k: 0.0 for k in score_map}

    return {
        k: (v - mn) / (mx - mn)
        for k, v in score_map.items()
    }

def get_wave(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_size(n):
    return "大" if n >= 25 else "小"

def get_odd_even(n):
    return "单" if n % 2 else "双"

def get_sum_size(n):
    s = sum(map(int, str(n)))
    return "合大" if s >= 7 else "合小"

def get_sum_odd_even(n):
    s = sum(map(int, str(n)))
    return "合单" if s % 2 else "合双"

def get_tail_size(n):
    return "尾大" if n % 10 >= 5 else "尾小"

def special_text(n):
    return (
        f"{get_odd_even(n)}/"
        f"{get_size(n)} "
        f"{get_sum_odd_even(n)}/"
        f"{get_sum_size(n)} "
        f"{get_tail_size(n)} "
        f"{get_wave(n)}"
    )

# =========================================================
# 数据库
# =========================================================

def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER,
        source TEXT,
        created_at TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS prediction_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_no TEXT,
        predict_json TEXT,
        special INTEGER,
        hit_count INTEGER,
        special_hit INTEGER,
        reviewed INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    conn.commit()

# =========================================================
# 请求
# =========================================================

def safe_request(url, retry=3):

    last_error = None

    for i in range(retry):

        try:

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": random.choice([
                        "Mozilla/5.0",
                        "Chrome/120.0",
                        "Safari/537.36",
                    ]),
                    "Cache-Control": "no-cache",
                }
            )

            with urllib.request.urlopen(req, timeout=20) as resp:

                return resp.read().decode("utf-8", errors="ignore")

        except Exception as e:

            last_error = e

            wait = 2 ** i

            print(f"请求失败: {e} | {wait}s后重试")

            time.sleep(wait)

    raise RuntimeError(last_error)

# =========================================================
# 真实数据
# =========================================================

def validate_record(r):

    try:

        nums = r["numbers"]
        special = r["special"]

        if len(nums) != 6:
            return False

        all_nums = nums + [special]

        if any(n < 1 or n > 49 for n in all_nums):
            return False

        if len(set(nums)) != 6:
            return False

        if special in nums:
            return False

        return True

    except:
        return False

def fetch_real_data():

    print("正在获取真实新澳门六合彩数据...")

    raw = safe_request(API_URL)

    payload = json.loads(raw)

    target = None

    for item in payload.get("lottery_data", []):

        name = item.get("name", "")

        if "新澳门" in name:

            target = item

            break

    if not target:
        raise RuntimeError("未找到新澳门彩数据")

    rows = []

    latest_issue = str(target.get("expect", "")).strip()

    latest_code = target.get("openCode", "")

    nums = []

    for x in latest_code.replace(",", " ").split():

        if x.strip().isdigit():

            nums.append(int(x.strip()))

    if len(nums) >= 7:

        rows.append({
            "issue": latest_issue,
            "numbers": nums[:6],
            "special": nums[6]
        })

    for item in target.get("history", []):

        if not isinstance(item, str):
            continue

        if "期：" not in item:
            continue

        try:

            left, right = item.split("期：", 1)

            issue = left.strip()

            nums = []

            for x in right.replace(",", " ").split():

                x = x.strip()

                if x.isdigit():
                    nums.append(int(x))

            if len(nums) >= 7:

                rows.append({
                    "issue": issue,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

        except:
            pass

    uniq = {}

    for r in rows:

        if validate_record(r):

            uniq[r["issue"]] = r

    result = list(uniq.values())

    result.sort(key=lambda x: x["issue"])

    print(f"获取成功: {len(result)} 条真实数据")

    return result

# =========================================================
# 保存数据
# =========================================================

def save_records(conn, rows):

    new_count = 0

    for r in rows:

        issue = r["issue"]

        nums = r["numbers"]

        special = r["special"]

        old = conn.execute(
            "SELECT issue_no FROM draws WHERE issue_no=?",
            (issue,)
        ).fetchone()

        if old:
            continue

        conn.execute("""
        INSERT INTO draws VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            issue,
            today(),
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            special,
            "marksix6",
            utc_now(),
        ))

        new_count += 1

    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) c FROM draws"
    ).fetchone()["c"]

    print(f"数据同步完成: total={total}, new={new_count}")

# =========================================================
# 加载数据
# =========================================================

def load_draws(conn):

    rows = conn.execute("""
    SELECT *
    FROM draws
    ORDER BY issue_no ASC
    """).fetchall()

    result = []

    for r in rows:

        result.append({
            "issue": r["issue_no"],
            "numbers": [
                r["n1"],
                r["n2"],
                r["n3"],
                r["n4"],
                r["n5"],
                r["n6"],
            ],
            "special": r["special"],
        })

    return result

# =========================================================
# AI 特征
# =========================================================

def build_freq_score(draws):

    freq = {n: 0.0 for n in ALL_NUMBERS}

    for draw in draws:

        for n in draw:

            freq[n] += 1

    return normalize(freq)

def build_omit_score(draws):

    omit = {n: len(draws)+1 for n in ALL_NUMBERS}

    for idx, draw in enumerate(draws):

        for n in draw:

            omit[n] = min(omit[n], idx+1)

    return normalize(omit)

def build_momentum_score(draws):

    score = {n: 0.0 for n in ALL_NUMBERS}

    for idx, draw in enumerate(draws):

        weight = 1 / (1 + idx)

        for n in draw:

            score[n] += weight

    return normalize(score)

def build_cycle_score(draws):

    score = {n: 0.0 for n in ALL_NUMBERS}

    history = {n: [] for n in ALL_NUMBERS}

    for idx, draw in enumerate(draws):

        for n in draw:

            history[n].append(idx)

    for n in ALL_NUMBERS:

        pos = history[n]

        if len(pos) < 3:
            continue

        gaps = []

        for i in range(1, len(pos)):

            gaps.append(pos[i] - pos[i-1])

        avg_gap = sum(gaps) / len(gaps)

        recent_gap = len(draws) - pos[-1]

        diff = abs(recent_gap - avg_gap)

        score[n] = max(0, 1 - diff / max(avg_gap, 1))

    return normalize(score)

def build_bayes_score(draws):

    total = len(draws)

    freq = Counter()

    for draw in draws:

        for n in draw:

            freq[n] += 1

    score = {}

    for n in ALL_NUMBERS:

        prior = 6 / 49

        likelihood = freq[n] / max(total, 1)

        score[n] = prior * likelihood

    return normalize(score)

def montecarlo(scores):

    weighted = []

    for n, s in scores.items():

        repeat = max(1, int(s * 100))

        weighted.extend([n] * repeat)

    result = {n: 0 for n in ALL_NUMBERS}

    for _ in range(AI_CONFIG["mc_simulations"]):

        pick = random.sample(weighted, 6)

        for n in pick:

            result[n] += 1

    return normalize(result)

def build_ai_scores(draws):

    freq = build_freq_score(draws)
    omit = build_omit_score(draws)
    momentum = build_momentum_score(draws)
    cycle = build_cycle_score(draws)
    bayes = build_bayes_score(draws)

    score = {}

    for n in ALL_NUMBERS:

        score[n] = (
            freq[n] * AI_CONFIG["freq_weight"] +
            omit[n] * AI_CONFIG["omit_weight"] +
            momentum[n] * AI_CONFIG["momentum_weight"] +
            cycle[n] * AI_CONFIG["cycle_weight"] +
            bayes[n] * AI_CONFIG["bayes_weight"]
        )

    return montecarlo(score)

# =========================================================
# 选号
# =========================================================

def pick_numbers(scores):

    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    nums = [n for n, _ in ranked[:6]]

    special = ranked[6][0]

    return nums, special

# =========================================================
# 回测
# =========================================================

def walkforward(draws):

    if len(draws) < 80:
        return 0.0, 0.0

    hits = []

    special_hit = 0

    total = 0

    for i in range(60, len(draws)-1):

        train = draws[:i]

        target = draws[i]

        history = [x["numbers"] for x in train]

        scores = build_ai_scores(history)

        nums, special = pick_numbers(scores)

        hit = len(
            set(nums) &
            set(target["numbers"])
        )

        hits.append(hit)

        if special == target["special"]:

            special_hit += 1

        total += 1

    return (
        statistics.mean(hits),
        special_hit / max(total, 1)
    )

# =========================================================
# 波色
# =========================================================

def predict_wave(draws):

    last = draws[-10:]

    score = {
        "红": 0,
        "蓝": 0,
        "绿": 0,
    }

    weight = len(last)

    for r in reversed(last):

        score[get_wave(r["special"])] += weight

        weight -= 1

    ranked = sorted(
        score.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return ranked[0], ranked[1]

# =========================================================
# 大小单双
# =========================================================

def predict_size_odd(draws):

    last = draws[-10:]

    big = small = odd = even = 0

    for r in last:

        s = r["special"]

        if s >= 25:
            big += 1
        else:
            small += 1

        if s % 2:
            odd += 1
        else:
            even += 1

    size_pred = "大" if big >= small else "小"

    odd_pred = "单" if odd >= even else "双"

    return size_pred, odd_pred

# =========================================================
# 下一期
# =========================================================

def next_issue(issue):

    digits = "".join(
        ch for ch in issue
        if ch.isdigit()
    )

    return str(int(digits)+1)

# =========================================================
# 生成预测
# =========================================================

def generate_prediction(conn):

    draws = load_draws(conn)

    if len(draws) < 20:
        raise RuntimeError("历史数据不足")

    history = [
        x["numbers"]
        for x in draws[-AI_CONFIG["recent_window"]:]
    ]

    scores = build_ai_scores(history)

    nums, special = pick_numbers(scores)

    latest_issue = draws[-1]["issue"]

    issue = next_issue(latest_issue)

    conn.execute("""
    INSERT INTO prediction_runs(
        issue_no,
        predict_json,
        special,
        created_at
    ) VALUES (?,?,?,?)
    """, (
        issue,
        json.dumps(nums, ensure_ascii=False),
        special,
        utc_now(),
    ))

    conn.commit()

    return issue, nums, special

# =========================================================
# 展示
# =========================================================

def dashboard(conn):

    draws = load_draws(conn)

    if not draws:
        print("暂无数据")
        return

    latest = draws[-1]

    issue, nums, special = generate_prediction(conn)

    print("="*70)

    print(f"最新开奖: {latest['issue']}")

    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in latest["numbers"]),
        "+",
        str(latest["special"]).zfill(2)
    )

    print("="*70)

    print()

    print(f"预测期号: {issue}")

    print()

    print("🎯 AI集成预测")

    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in nums),
        "+",
        str(special).zfill(2)
    )

    print(f"特码属性: {special_text(special)}")

    print()

    main_wave, second_wave = predict_wave(draws)

    print("🎨 波色预测")

    print(f"主强: {main_wave[0]} ({main_wave[1]})")

    print(f"次强: {second_wave[0]} ({second_wave[1]})")

    print()

    size_pred, odd_pred = predict_size_odd(draws)

    print("📊 大小单双")

    print(f"大小预测: {size_pred}")

    print(f"单双预测: {odd_pred}")

    print()

    avg_hit, special_rate = walkforward(draws)

    print("📈 WalkForward回测")

    print(f"平均命中: {round(avg_hit, 4)}")

    print(f"特别号命中率: {round(special_rate*100, 2)}%")

    print("="*70)

# =========================================================
# 命令
# =========================================================

def cmd_sync():

    conn = connect_db()

    try:

        init_db(conn)

        rows = fetch_real_data()

        save_records(conn, rows)

        dashboard(conn)

    finally:

        conn.close()

def cmd_show():

    conn = connect_db()

    try:

        init_db(conn)

        dashboard(conn)

    finally:

        conn.close()

# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync")

    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "sync":

        cmd_sync()

    elif args.cmd == "show":

        cmd_show()

    else:

        parser.print_help()

if __name__ == "__main__":

    main()