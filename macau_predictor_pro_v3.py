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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# =========================================================
# 配置
# =========================================================

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "new_macau.db"

API_SOURCES = [
    {
        "name": "marksix6",
        "url": "https://marksix6.net/index.php?api=1",
        "type": "marksix6",
    }
]

TARGET_LOTTERY_NAME = "新澳门彩"

ALL_NUMBERS = list(range(1, 50))

RED = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

USER_AGENTS = [
    "Mozilla/5.0",
    "Chrome/120.0",
    "Safari/537.36",
    "Edge/119.0",
]

AI_CONFIG = {
    "freq_weight": 0.28,
    "omit_weight": 0.20,
    "momentum_weight": 0.20,
    "cycle_weight": 0.18,
    "bayes_weight": 0.14,
    "learning_rate": 0.02,
    "mc_simulations": 30000,
    "recent_window": 120,
    "cycle_min": 5,
    "cycle_max": 15,
}

# =========================================================
# 数据模型
# =========================================================

class DrawRecord:
    def __init__(self, issue_no: str, draw_date: str, numbers: List[int], special: int):
        self.issue_no = issue_no
        self.draw_date = draw_date
        self.numbers = numbers
        self.special = special

# =========================================================
# 工具
# =========================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    return utc_now()[:10]

def normalize(score_map: Dict[int, float]) -> Dict[int, float]:
    vals = list(score_map.values())
    mn = min(vals)
    mx = max(vals)
    if mx == mn:
        return {k: 0.0 for k in score_map}
    return {k: (v - mn) / (mx - mn) for k, v in score_map.items()}

def safe_int_list(text: str) -> List[int]:
    nums: List[int] = []
    for x in text.replace("，", ",").split(","):
        x = x.strip()
        if x.isdigit():
            nums.append(int(x))
    return nums

def get_wave(n: int) -> str:
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_size(n: int) -> str:
    return "大" if n >= 25 else "小"

def get_odd_even(n: int) -> str:
    return "单" if n % 2 else "双"

def get_sum_size(n: int) -> str:
    s = sum(map(int, str(n)))
    return "合大" if s >= 7 else "合小"

def get_sum_odd_even(n: int) -> str:
    s = sum(map(int, str(n)))
    return "合单" if s % 2 else "合双"

def get_tail_size(n: int) -> str:
    return "尾大" if n % 10 >= 5 else "尾小"

def special_text(n: int) -> str:
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

def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS draws (
        issue_no TEXT PRIMARY KEY,
        draw_date TEXT NOT NULL,
        n1 INTEGER NOT NULL,
        n2 INTEGER NOT NULL,
        n3 INTEGER NOT NULL,
        n4 INTEGER NOT NULL,
        n5 INTEGER NOT NULL,
        n6 INTEGER NOT NULL,
        special INTEGER NOT NULL,
        source TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS prediction_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_no TEXT NOT NULL,
        predict_json TEXT NOT NULL,
        special INTEGER NOT NULL,
        hit_count INTEGER,
        special_hit INTEGER,
        score REAL,
        reviewed INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS analytics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        avg_hit REAL,
        special_rate REAL,
        montecarlo REAL
    );
    """)
    conn.commit()

# =========================================================
# 真实数据抓取
# =========================================================

def safe_request(url: str, timeout: int = 20, retry: int = 3) -> str:
    last_error = None
    for i in range(retry):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
                if not text:
                    raise RuntimeError("空响应")
                return text
        except Exception as e:
            last_error = e
            sleep_time = 2 ** i
            print(f"请求失败: {e} | {sleep_time}s后重试")
            time.sleep(sleep_time)
    raise RuntimeError(f"请求最终失败: {last_error}")

def validate_record(rec: Dict) -> bool:
    try:
        nums = rec["numbers"]
        special = rec["special"]
        if len(nums) != 6:
            return False
        all_nums = nums + [special]
        if any(not isinstance(n, int) for n in all_nums):
            return False
        if any(n < 1 or n > 49 for n in all_nums):
            return False
        if len(set(nums)) != 6:
            return False
        if special in nums:
            return False
        return True
    except Exception:
        return False

def parse_marksix6(payload: Dict) -> List[Dict]:
    result: List[Dict] = []
    lottery_list = payload.get("lottery_data", [])
    target = None

    for lottery in lottery_list:
        name = lottery.get("name", "")
        if TARGET_LOTTERY_NAME in name:
            target = lottery
            break

    if not target:
        return []

    latest_issue = str(target.get("expect", "")).strip()
    latest_code = str(target.get("openCode", "")).strip()
    latest_nums = safe_int_list(latest_code)

    if latest_issue and len(latest_nums) >= 7:
        result.append(
            {
                "issue": latest_issue,
                "numbers": latest_nums[:6],
                "special": latest_nums[6],
            }
        )

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
                result.append(
                    {
                        "issue": issue,
                        "numbers": nums[:6],
                        "special": nums[6],
                    }
                )
        except Exception:
            continue

    uniq = {}
    for r in result:
        if validate_record(r):
            uniq[r["issue"]] = r

    rows = list(uniq.values())
    rows.sort(key=lambda x: x["issue"])
    return rows

def fetch_real_data() -> List[Dict]:
    all_verified: List[List[Dict]] = []

    for api in API_SOURCES:
        try:
            print(f"正在获取: {api['name']}")
            raw = safe_request(api["url"])
            payload = json.loads(raw)

            if api["type"] == "marksix6":
                rows = parse_marksix6(payload)
            else:
                rows = []

            verified = [r for r in rows if validate_record(r)]
            print(f"{api['name']} 有效数据: {len(verified)}")

            if verified:
                all_verified.append(verified)
        except Exception as e:
            print(f"数据源失败: {api['name']} | {e}")

    if not all_verified:
        raise RuntimeError("所有数据源失败")

    best = max(all_verified, key=len)
    best.sort(key=lambda x: x["issue"])
    print(f"最终采用数据: {len(best)} 条")
    return best

# =========================================================
# 数据保存
# =========================================================

def save_records(conn: sqlite3.Connection, records: List[Dict]) -> None:
    new_count = 0
    changed_count = 0

    for r in records:
        issue = r["issue"]
        nums = r["numbers"]
        special = r["special"]

        old = conn.execute(
            "SELECT * FROM draws WHERE issue_no=?",
            (issue,),
        ).fetchone()

        if not old:
            conn.execute(
                """
                INSERT INTO draws (
                    issue_no, draw_date,
                    n1, n2, n3, n4, n5, n6,
                    special, source, created_at
                ) VALUES (
                    ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    issue,
                    today_str(),
                    nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
                    special,
                    "marksix6",
                    utc_now(),
                ),
            )
            new_count += 1
        else:
            old_nums = [old["n1"], old["n2"], old["n3"], old["n4"], old["n5"], old["n6"]]
            old_special = old["special"]
            if old_nums != nums or old_special != special:
                changed_count += 1
                print("⚠️ 检测到历史数据冲突，已跳过覆盖:")
                print(f"期号: {issue}")
                print(f"旧数据: {old_nums} + {old_special}")
                print(f"新数据: {nums} + {special}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) c FROM draws").fetchone()["c"]
    print(f"数据同步完成: total={total}, new={new_count}")
    if changed_count:
        print(f"历史冲突数量: {changed_count}")

def load_draws(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM draws
        ORDER BY issue_no ASC
        """
    ).fetchall()

    result = []
    for r in rows:
        result.append(
            {
                "issue": r["issue_no"],
                "numbers": [r["n1"], r["n2"], r["n3"], r["n4"], r["n5"], r["n6"]],
                "special": r["special"],
            }
        )
    return result

# =========================================================
# 特征工程
# =========================================================

def build_freq_score(draws: List[List[int]]) -> Dict[int, float]:
    freq = {n: 0.0 for n in ALL_NUMBERS}
    for draw in draws:
        for n in draw:
            freq[n] += 1
    return normalize(freq)

def build_omit_score(draws: List[List[int]]) -> Dict[int, float]:
    omission = {n: len(draws) + 1 for n in ALL_NUMBERS}
    for idx, draw in enumerate(draws):
        for n in draw:
            omission[n] = min(omission[n], idx + 1)
    return normalize(omission)

def build_momentum_score(draws: List[List[int]]) -> Dict[int, float]:
    m = {n: 0.0 for n in ALL_NUMBERS}
    for idx, draw in enumerate(draws):
        weight = 1.0 / (1 + idx)
        for n in draw:
            m[n] += weight
    return normalize(m)

def build_cycle_score(draws: List[List[int]]) -> Dict[int, float]:
    cycle_scores = {n: 0.0 for n in ALL_NUMBERS}
    history = {n: [] for n in ALL_NUMBERS}

    for idx, draw in enumerate(draws):
        for n in draw:
            history[n].append(idx)

    for n in ALL_NUMBERS:
        pos = history[n]
        if len(pos) < 3:
            continue

        gaps = [pos[i] - pos[i - 1] for i in range(1, len(pos))]
        avg_gap = sum(gaps) / len(gaps)

        if AI_CONFIG["cycle_min"] <= avg_gap <= AI_CONFIG["cycle_max"]:
            recent_gap = len(draws) - 1 - pos[-1]
            score = 1 - abs(recent_gap - avg_gap) / avg_gap
            cycle_scores[n] = max(0.0, score)

    return normalize(cycle_scores)

def build_bayes_score(draws: List[List[int]]) -> Dict[int, float]:
    scores = {n: 1.0 for n in ALL_NUMBERS}
    total = len(draws)
    freq = Counter()
    for draw in draws:
        for n in draw:
            freq[n] += 1

    for n in ALL_NUMBERS:
        prior = 6 / 49
        likelihood = freq[n] / max(1, total)
        posterior = prior * likelihood
        scores[n] = posterior

    return normalize(scores)

def montecarlo_simulation(scores: Dict[int, float]) -> Dict[int, float]:
    sims = AI_CONFIG["mc_simulations"]
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    weighted_pool = []
    for n, s in ranked:
        repeat = max(1, int(s * 100))
        weighted_pool.extend([n] * repeat)

    if len(weighted_pool) < 6:
        weighted_pool = ALL_NUMBERS[:]

    hit_counter = {n: 0 for n in ALL_NUMBERS}

    for _ in range(sims):
        pick = random.sample(weighted_pool, 6)
        for n in pick:
            hit_counter[n] += 1

    result = {n: hit_counter[n] / sims for n in ALL_NUMBERS}
    return normalize(result)

def build_ai_scores(draws: List[List[int]]) -> Dict[int, float]:
    freq = build_freq_score(draws)
    omit = build_omit_score(draws)
    momentum = build_momentum_score(draws)
    cycle = build_cycle_score(draws)
    bayes = build_bayes_score(draws)

    scores = {}
    for n in ALL_NUMBERS:
        scores[n] = (
            freq[n] * AI_CONFIG["freq_weight"]
            + omit[n] * AI_CONFIG["omit_weight"]
            + momentum[n] * AI_CONFIG["momentum_weight"]
            + cycle[n] * AI_CONFIG["cycle_weight"]
            + bayes[n] * AI_CONFIG["bayes_weight"]
        )

    scores = montecarlo_simulation(scores)
    return scores

def pick_numbers(scores: Dict[int, float]) -> Tuple[List[int], int]:
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    nums = [n for n, _ in ranked[:6]]
    special = ranked[6][0]
    return nums, special

# =========================================================
# 动态学习
# =========================================================

def auto_learn(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT hit_count
        FROM prediction_runs
        WHERE reviewed=1
        ORDER BY id DESC
        LIMIT 30
        """
    ).fetchall()

    if len(rows) < 10:
        return

    avg_hit = statistics.mean(r["hit_count"] for r in rows)
    lr = AI_CONFIG["learning_rate"]

    if avg_hit < 1.0:
        AI_CONFIG["momentum_weight"] += lr
        AI_CONFIG["freq_weight"] -= lr
    elif avg_hit > 2.0:
        AI_CONFIG["freq_weight"] += lr
        AI_CONFIG["momentum_weight"] -= lr

    total = (
        AI_CONFIG["freq_weight"]
        + AI_CONFIG["omit_weight"]
        + AI_CONFIG["momentum_weight"]
        + AI_CONFIG["cycle_weight"]
        + AI_CONFIG["bayes_weight"]
    )

    for k in [
        "freq_weight",
        "omit_weight",
        "momentum_weight",
        "cycle_weight",
        "bayes_weight",
    ]:
        AI_CONFIG[k] /= total

# =========================================================
# 生成预测
# =========================================================

def next_issue_no(issue_no: str) -> str:
    digits = "".join(ch for ch in issue_no if ch.isdigit())
    if not digits:
        return issue_no
    return str(int(digits) + 1)

def generate_prediction(conn: sqlite3.Connection) -> Tuple[str, List[int], int]:
    auto_learn(conn)

    draws = load_draws(conn)
    if len(draws) < 20:
        raise RuntimeError("历史数据不足，至少需要20期。")

    history = [x["numbers"] for x in draws[-AI_CONFIG["recent_window"] :]]
    scores = build_ai_scores(history)
    nums, special = pick_numbers(scores)

    latest_issue = draws[-1]["issue"]
    next_issue = next_issue_no(latest_issue)

    conn.execute(
        """
        INSERT INTO prediction_runs(
            issue, predict_json, special, created_at
        ) VALUES (?,?,?,?)
        """,
        (
            next_issue,
            json.dumps(nums, ensure_ascii=False),
            special,
            utc_now(),
        ),
    )
    conn.commit()

    return next_issue, nums, special

# =========================================================
# 波色 / 大小单双
# =========================================================

def predict_wave(draws: List[Dict]) -> Tuple[Tuple[str, int], Tuple[str, int]]:
    last = draws[-10:]
    score = {"红": 0, "蓝": 0, "绿": 0}
    weight = len(last)

    for r in reversed(last):
        wave = get_wave(r["special"])
        score[wave] += weight
        weight -= 1

    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)
    return ranked[0], ranked[1]

def predict_size_odd(draws: List[Dict]) -> Tuple[str, str]:
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

    return ("大" if big >= small else "小", "单" if odd >= even else "双")

# =========================================================
# 回测
# =========================================================

def walkforward_test(draws: List[Dict]) -> Tuple[float, float]:
    if len(draws) < 80:
        return 0.0, 0.0

    hits = []
    special_hit = 0
    total = 0

    for i in range(60, len(draws) - 1):
        train = draws[:i]
        target = draws[i]

        history = [x["numbers"] for x in train]
        scores = build_ai_scores(history)
        nums, special = pick_numbers(scores)

        hit = len(set(nums) & set(target["numbers"]))
        hits.append(hit)

        if special == target["special"]:
            special_hit += 1

        total += 1

    return statistics.mean(hits), special_hit / max(1, total)

def random_baseline(draws: List[Dict], loops: int = 300) -> float:
    total_hit = 0
    total = 0

    for _ in range(loops):
        for r in draws[-50:]:
            pick = random.sample(ALL_NUMBERS, 6)
            hit = len(set(pick) & set(r["numbers"]))
            total_hit += hit
            total += 1

    return total_hit / max(1, total)

# =========================================================
# 展示
# =========================================================

def dashboard(conn: sqlite3.Connection) -> None:
    draws = load_draws(conn)
    if not draws:
        print("暂无数据")
        return

    latest = draws[-1]
    issue, nums, special = generate_prediction(conn)

    print("=" * 70)
    print(f"最新开奖: {latest['issue']}")
    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in latest["numbers"]),
        "+",
        str(latest["special"]).zfill(2),
    )
    print("=" * 70)
    print()
    print(f"预测期号: {issue}")
    print()
    print("🎯 AI集成预测")
    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in nums),
        "+",
        str(special).zfill(2),
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
    avg_hit, special_rate = walkforward_test(draws)
    baseline = random_baseline(draws)
    print("📈 WalkForward回测")
    print(f"平均命中: {round(avg_hit, 4)}")
    print(f"特别号命中率: {round(special_rate * 100, 2)}%")
    print(f"MonteCarlo基准: {round(baseline, 4)}")
    print("=" * 70)

# =========================================================
# 命令
# =========================================================

def cmd_sync() -> None:
    conn = connect_db()
    try:
        init_db(conn)
        records = fetch_real_data()
        save_records(conn, records)
        dashboard(conn)
    finally:
        conn.close()

def cmd_show() -> None:
    conn = connect_db()
    try:
        init_db(conn)
        dashboard(conn)
    finally:
        conn.close()

# =========================================================
# 主函数
# =========================================================

def main() -> None:
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