# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Any

# =========================================================
# V12 - 新澳门六合彩 AI 严格时序版
# =========================================================

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "macau_v12.db"
HTML_FILE = ROOT / "dashboard.html"
TREND_FILE = ROOT / "trend.txt"

TARGET_LOTTERY_NAME = "新澳门彩"
API_URLS = [
    "https://marksix6.net/index.php?api=1",
]

ALL_NUMBERS = list(range(1, 50))

RED = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

DEFAULT_WEIGHTS = {
    "frequency": 0.24,
    "omission": 0.18,
    "momentum": 0.18,
    "cycle": 0.16,
    "bayes": 0.12,
    "pair": 0.08,
    "wave": 0.04,
}

DEFAULT_LEARNING_RATE = 0.03

# =========================================================
# 工具
# =========================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    return utc_now()[:10]

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

def normalize(score_map: Dict[int, float]) -> Dict[int, float]:
    vals = list(score_map.values())
    mn = min(vals)
    mx = max(vals)
    if mx == mn:
        return {k: 0.0 for k in score_map}
    return {k: (v - mn) / (mx - mn) for k, v in score_map.items()}

def safe_ints(text: str) -> List[int]:
    nums: List[int] = []
    for x in text.replace("，", ",").replace(" ", ",").split(","):
        x = x.strip()
        if x.isdigit():
            nums.append(int(x))
    return nums

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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ai_state (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS prediction_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_no TEXT NOT NULL,
        created_at TEXT NOT NULL,
        main_json TEXT NOT NULL,
        special INTEGER NOT NULL,
        confidence REAL NOT NULL,
        main_hit INTEGER,
        special_hit INTEGER,
        reviewed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS backtest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        window_size INTEGER NOT NULL,
        total INTEGER NOT NULL,
        main_hit_total INTEGER NOT NULL,
        any_hit_total INTEGER NOT NULL,
        special_hit_total INTEGER NOT NULL,
        max_consecutive_miss INTEGER NOT NULL,
        avg_main_hit REAL NOT NULL,
        details_json TEXT NOT NULL
    );
    """)
    conn.commit()

def get_state_json(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    row = conn.execute("SELECT v FROM ai_state WHERE k=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except Exception:
        return default

def set_state_json(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO ai_state(k, v, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(k) DO UPDATE SET
            v=excluded.v,
            updated_at=excluded.updated_at
        """,
        (key, json.dumps(value, ensure_ascii=False), utc_now()),
    )
    conn.commit()

def load_weights(conn: sqlite3.Connection) -> Dict[str, float]:
    weights = get_state_json(conn, "weights_v12", DEFAULT_WEIGHTS.copy())
    if not isinstance(weights, dict):
        weights = DEFAULT_WEIGHTS.copy()

    merged = DEFAULT_WEIGHTS.copy()
    for k, v in weights.items():
        if k in merged:
            try:
                merged[k] = float(v)
            except Exception:
                pass

    total = sum(merged.values())
    if total <= 0:
        merged = DEFAULT_WEIGHTS.copy()
        total = sum(merged.values())

    for k in merged:
        merged[k] /= total
    return merged

def save_weights(conn: sqlite3.Connection, weights: Dict[str, float]) -> None:
    total = sum(weights.values())
    if total <= 0:
        weights = DEFAULT_WEIGHTS.copy()
        total = sum(weights.values())
    normalized = {k: float(v) / total for k, v in weights.items()}
    set_state_json(conn, "weights_v12", normalized)

def get_learning_rate(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT v FROM ai_state WHERE k='learning_rate_v12'").fetchone()
    if not row:
        set_state_json(conn, "learning_rate_v12", DEFAULT_LEARNING_RATE)
        return DEFAULT_LEARNING_RATE
    try:
        return float(json.loads(row["v"]))
    except Exception:
        return DEFAULT_LEARNING_RATE

def set_learning_rate(conn: sqlite3.Connection, lr: float) -> None:
    lr = max(0.005, min(0.2, lr))
    set_state_json(conn, "learning_rate_v12", lr)

# =========================================================
# 真实数据获取
# =========================================================

def safe_request(url: str, timeout: int = 20, retry: int = 3) -> str:
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
                        "Edge/119.0",
                    ]),
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
            time.sleep(2 ** i)
    raise RuntimeError(f"请求失败: {last_error}")

def validate_record(rec: Dict[str, Any]) -> bool:
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

def parse_marksix6(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    lottery_list = payload.get("lottery_data", [])
    target = None

    for item in lottery_list:
        name = item.get("name", "")
        if TARGET_LOTTERY_NAME in name:
            target = item
            break

    if not target:
        return []

    latest_issue = str(target.get("expect", "")).strip()
    latest_code = str(target.get("openCode", "")).strip()
    latest_nums = safe_ints(latest_code)

    if latest_issue and len(latest_nums) >= 7:
        result.append({
            "issue": latest_issue,
            "numbers": latest_nums[:6],
            "special": latest_nums[6],
        })

    for item in target.get("history", []):
        if not isinstance(item, str):
            continue
        if "期" not in item:
            continue
        try:
            left, right = item.split("期", 1)
            issue = left.strip()
            nums = safe_ints(right)
            if len(nums) >= 7:
                result.append({
                    "issue": issue,
                    "numbers": nums[:6],
                    "special": nums[6],
                })
        except Exception:
            continue

    uniq = {}
    for r in result:
        if validate_record(r):
            uniq[r["issue"]] = r

    rows = list(uniq.values())
    rows.sort(key=lambda x: x["issue"])
    return rows

def fetch_real_data() -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []

    for url in API_URLS:
        try:
            raw = safe_request(url)
            payload = json.loads(raw)
            rows = parse_marksix6(payload)
            if rows:
                all_rows.extend(rows)
        except Exception as e:
            print(f"数据源失败: {url} | {e}")

    uniq = {}
    for r in all_rows:
        if validate_record(r):
            uniq[r["issue"]] = r

    final = list(uniq.values())
    final.sort(key=lambda x: x["issue"])

    if not final:
        raise RuntimeError("未获取到真实新澳门六合彩数据")

    print(f"真实数据获取成功: {len(final)} 条")
    return final

# =========================================================
# 保存 / 加载
# =========================================================

def save_records(conn: sqlite3.Connection, rows: List[Dict[str, Any]]) -> int:
    new_count = 0

    for r in rows:
        issue = r["issue"]
        nums = r["numbers"]
        special = r["special"]

        old = conn.execute(
            "SELECT n1,n2,n3,n4,n5,n6,special FROM draws WHERE issue_no=?",
            (issue,),
        ).fetchone()

        if old:
            old_nums = [old["n1"], old["n2"], old["n3"], old["n4"], old["n5"], old["n6"]]
            old_special = old["special"]
            if old_nums != nums or old_special != special:
                print(f"⚠️ 期号 {issue} 历史数据冲突，已跳过覆盖")
            continue

        conn.execute(
            """
            INSERT INTO draws (
                issue_no, draw_date,
                n1, n2, n3, n4, n5, n6,
                special, source, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                issue,
                today_str(),
                nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
                special,
                "marksix6",
                utc_now(),
                utc_now(),
            ),
        )
        new_count += 1

    conn.commit()
    return new_count

def load_draws(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT issue_no, n1,n2,n3,n4,n5,n6, special
        FROM draws
        ORDER BY issue_no ASC
        """
    ).fetchall()

    result = []
    for r in rows:
        result.append({
            "issue": r["issue_no"],
            "numbers": [r["n1"], r["n2"], r["n3"], r["n4"], r["n5"], r["n6"]],
            "special": r["special"],
        })
    return result

# =========================================================
# 特征工程
# =========================================================

def frequency_score(draws: List[Dict[str, Any]], window: int = 80) -> Dict[int, float]:
    freq = {n: 0.0 for n in ALL_NUMBERS}
    recent = draws[-window:]
    for d in recent:
        for n in d["numbers"]:
            freq[n] += 1.0
        freq[d["special"]] += 0.5
    return normalize(freq)

def omission_score(draws: List[Dict[str, Any]]) -> Dict[int, float]:
    omission = {n: float(len(draws) + 1) for n in ALL_NUMBERS}
    for i, d in enumerate(reversed(draws)):
        for n in d["numbers"] + [d["special"]]:
            omission[n] = min(omission[n], float(i + 1))
    return normalize(omission)

def momentum_score(draws: List[Dict[str, Any]], recent_window: int = 20, older_window: int = 60) -> Dict[int, float]:
    recent = draws[-recent_window:]
    older = draws[-(recent_window + older_window):-recent_window] if len(draws) > recent_window else []

    r = Counter()
    o = Counter()
    for d in recent:
        for n in d["numbers"]:
            r[n] += 1
        r[d["special"]] += 0.5

    for d in older:
        for n in d["numbers"]:
            o[n] += 1
        o[d["special"]] += 0.5

    score = {}
    for n in ALL_NUMBERS:
        score[n] = r[n] - (o[n] / 2.0)
    return normalize(score)

def cycle_score(draws: List[Dict[str, Any]]) -> Dict[int, float]:
    score = {n: 0.0 for n in ALL_NUMBERS}
    history = {n: [] for n in ALL_NUMBERS}

    for idx, d in enumerate(draws):
        for n in d["numbers"] + [d["special"]]:
            history[n].append(idx)

    for n in ALL_NUMBERS:
        pos = history[n]
        if len(pos) < 3:
            continue
        gaps = [pos[i] - pos[i - 1] for i in range(1, len(pos))]
        avg_gap = sum(gaps) / len(gaps)
        recent_gap = len(draws) - 1 - pos[-1]
        score[n] = max(0.0, 1.0 - abs(recent_gap - avg_gap) / max(avg_gap, 1.0))
    return normalize(score)

def bayes_score(draws: List[Dict[str, Any]]) -> Dict[int, float]:
    freq = Counter()
    for d in draws:
        for n in d["numbers"]:
            freq[n] += 1.0
        freq[d["special"]] += 0.5

    total = sum(freq.values())
    score = {}
    for n in ALL_NUMBERS:
        prior = 6.0 / 49.0
        likelihood = (freq[n] + 1.0) / (total + 49.0)
        score[n] = prior * likelihood
    return normalize(score)

def pair_affinity_score(draws: List[Dict[str, Any]], window: int = 120) -> Dict[int, float]:
    pair_count = defaultdict(int)
    recent = draws[-window:]

    for d in recent:
        nums = sorted(d["numbers"])
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                pair_count[(nums[i], nums[j])] += 1

    score = defaultdict(float)
    for (a, b), c in pair_count.items():
        score[a] += c
        score[b] += c

    for n in ALL_NUMBERS:
        score.setdefault(n, 0.0)

    return normalize(score)

def wave_balance_score(draws: List[Dict[str, Any]], window: int = 20) -> Dict[int, float]:
    recent = draws[-window:]
    wave_count = {"红": 0.0, "蓝": 0.0, "绿": 0.0}
    for d in recent:
        wave_count[get_wave(d["special"])] += 1.0

    expected = max(1.0, len(recent) / 3.0)
    wave_pressure = {k: max(0.0, expected - v) for k, v in wave_count.items()}

    score = {}
    for n in ALL_NUMBERS:
        score[n] = wave_pressure[get_wave(n)]
    return normalize(score)

# =========================================================
# AI 预测
# =========================================================

def ai_predict(train_draws: List[Dict[str, Any]], weights: Dict[str, float]) -> Dict[str, Any]:
    freq = frequency_score(train_draws)
    omit = omission_score(train_draws)
    mom = momentum_score(train_draws)
    cyc = cycle_score(train_draws)
    bay = bayes_score(train_draws)
    pair = pair_affinity_score(train_draws)
    wave = wave_balance_score(train_draws)

    combined = {}
    for n in ALL_NUMBERS:
        combined[n] = (
            freq[n] * weights["frequency"] +
            omit[n] * weights["omission"] +
            mom[n] * weights["momentum"] +
            cyc[n] * weights["cycle"] +
            bay[n] * weights["bayes"] +
            pair[n] * weights["pair"] +
            wave[n] * weights["wave"]
        )

    ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)
    main = [n for n, _ in ranked[:6]]
    special = next((n for n, _ in ranked if n not in main), ranked[6][0])
    confidence = round(sum(score for _, score in ranked[:6]) / 6.0, 4)

    return {
        "main": main,
        "special": special,
        "confidence": confidence,
        "scores": combined,
        "ranked": ranked,
    }

# =========================================================
# 严格回测最近最新10期（不偷看未来）
# =========================================================

def backtest_latest_10_strict(draws: List[Dict[str, Any]], weights: Dict[str, float]) -> Dict[str, Any]:
    if len(draws) < 15:
        return {
            "total": 0,
            "main_hit_total": 0,
            "any_hit_total": 0,
            "special_hit_total": 0,
            "max_consecutive_miss": 0,
            "avg_main_hit": 0.0,
            "details": [],
        }

    start = max(1, len(draws) - 10)
    end = len(draws)

    details = []
    main_hit_total = 0
    any_hit_total = 0
    special_hit_total = 0
    consecutive_miss = 0
    max_consecutive_miss = 0

    for i in range(start, end):
        train = draws[:i]
        actual = draws[i]

        pred = ai_predict(train, weights)
        pred_main = pred["main"]
        pred_special = pred["special"]

        actual_main = set(actual["numbers"])
        main_hit = len(set(pred_main) & actual_main)
        special_hit = 1 if pred_special == actual["special"] else 0
        any_hit = 1 if main_hit > 0 else 0

        main_hit_total += main_hit
        any_hit_total += any_hit
        special_hit_total += special_hit

        if main_hit == 0:
            consecutive_miss += 1
        else:
            consecutive_miss = 0
        max_consecutive_miss = max(max_consecutive_miss, consecutive_miss)

        details.append({
            "issue": actual["issue"],
            "pred_main": pred_main,
            "pred_special": pred_special,
            "actual_main": actual["numbers"],
            "actual_special": actual["special"],
            "main_hit": main_hit,
            "special_hit": special_hit,
            "any_hit": any_hit,
        })

    total = len(details)
    avg_main_hit = round(main_hit_total / total if total else 0.0, 4)

    return {
        "total": total,
        "main_hit_total": main_hit_total,
        "any_hit_total": any_hit_total,
        "special_hit_total": special_hit_total,
        "max_consecutive_miss": max_consecutive_miss,
        "avg_main_hit": avg_main_hit,
        "details": details,
    }

def update_weights_from_backtest(weights: Dict[str, float], bt: Dict[str, Any], lr: float) -> Dict[str, float]:
    new_weights = dict(weights)
    avg_main_hit = bt["avg_main_hit"]

    if avg_main_hit < 1.2:
        new_weights["frequency"] = max(0.05, new_weights["frequency"] - lr)
        new_weights["omission"] = min(0.30, new_weights["omission"] + lr / 2)
        new_weights["cycle"] = min(0.28, new_weights["cycle"] + lr / 2)
    elif avg_main_hit > 2.2:
        new_weights["frequency"] = min(0.35, new_weights["frequency"] + lr)
        new_weights["omission"] = max(0.05, new_weights["omission"] - lr / 2)
        new_weights["bayes"] = min(0.25, new_weights["bayes"] + lr / 2)

    total = sum(new_weights.values())
    if total <= 0:
        return DEFAULT_WEIGHTS.copy()

    for k in new_weights:
        new_weights[k] /= total

    return new_weights

# =========================================================
# 其他分析
# =========================================================

def banker_cycle(draws: List[Dict[str, Any]]) -> str:
    recent = draws[-30:]
    waves = [get_wave(d["special"]) for d in recent]
    c = Counter(waves)
    return c.most_common(1)[0][0] if c else "绿"

def build_trend_text(draws: List[Dict[str, Any]], limit: int = 30) -> str:
    lines = []
    for d in draws[-limit:]:
        lines.append(f"{d['issue']} -> {d['special']:02d} ({get_wave(d['special'])})")
    return "\n".join(lines)

def next_issue_no(issue: str) -> str:
    digits = "".join(ch for ch in issue if ch.isdigit())
    if not digits:
        return issue
    return str(int(digits) + 1)

# =========================================================
# HTML
# =========================================================

def generate_html(latest: Dict[str, Any], prediction: Dict[str, Any], bt: Dict[str, Any]) -> None:
    details_rows = ""
    for d in bt["details"]:
        details_rows += f"""
        <tr>
            <td>{d["issue"]}</td>
            <td>{" ".join(str(x).zfill(2) for x in d["pred_main"])}</td>
            <td>{str(d["pred_special"]).zfill(2)}</td>
            <td>{" ".join(str(x).zfill(2) for x in d["actual_main"])}</td>
            <td>{str(d["actual_special"]).zfill(2)}</td>
            <td>{d["main_hit"]}</td>
            <td>{d["special_hit"]}</td>
        </tr>
        """

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>新澳门六合彩 V12</title>
        <style>
            body {{
                background: #111;
                color: #00ff88;
                font-family: Consolas, monospace;
                padding: 24px;
            }}
            .box {{
                border: 1px solid #00ff88;
                padding: 16px;
                margin-bottom: 18px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                color: #00ff88;
            }}
            th, td {{
                border: 1px solid #00ff88;
                padding: 6px 8px;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <h1>新澳门六合彩 AI V12</h1>

        <div class="box">
            <h2>最新开奖</h2>
            <p>期号：{latest["issue"]}</p>
            <p>号码：{" ".join(str(x).zfill(2) for x in latest["numbers"])} + {str(latest["special"]).zfill(2)}</p>
        </div>

        <div class="box">
            <h2>AI预测</h2>
            <p>主码：{" ".join(str(x).zfill(2) for x in prediction["main"])}</p>
            <p>特别号：{str(prediction["special"]).zfill(2)}</p>
            <p>置信度：{prediction["confidence"]}</p>
        </div>

        <div class="box">
            <h2>最近10期严格回测</h2>
            <p>主码总命中：{bt["main_hit_total"]}</p>
            <p>至少中1个主码的期数：{bt["any_hit_total"]} / {bt["total"]}</p>
            <p>特别号命中：{bt["special_hit_total"]} / {bt["total"]}</p>
            <p>主码最大连空：{bt["max_consecutive_miss"]} 期</p>
            <p>主码平均命中：{bt["avg_main_hit"]}</p>
        </div>

        <div class="box">
            <h2>回测明细</h2>
            <table>
                <tr>
                    <th>期号</th>
                    <th>预测主码</th>
                    <th>预测特号</th>
                    <th>实际主码</th>
                    <th>实际特号</th>
                    <th>主码命中</th>
                    <th>特号命中</th>
                </tr>
                {details_rows}
            </table>
        </div>

        <div class="box">
            <h2>走势图</h2>
            <pre>{build_trend_text([latest], 1)}</pre>
        </div>

        <div class="box">
            <p>生成时间：{utc_now()}</p>
        </div>
    </body>
    </html>
    """

    HTML_FILE.write_text(html, encoding="utf-8")

# =========================================================
# 控制台输出
# =========================================================

def print_dashboard(draws: List[Dict[str, Any]], conn: sqlite3.Connection) -> None:
    if not draws:
        print("暂无数据")
        return

    weights = load_weights(conn)
    latest = draws[-1]
    prediction = ai_predict(draws, weights)
    bt = backtest_latest_10_strict(draws, weights)

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

    next_issue = next_issue_no(latest["issue"])
    print(f"预测期号: {next_issue}")
    print()
    print("🎯 V12 AI预测")
    print(
        "号码:",
        " ".join(str(x).zfill(2) for x in prediction["main"]),
        "+",
        str(prediction["special"]).zfill(2),
    )
    print(f"置信度: {prediction['confidence']}")
    print(f"特码属性: {special_text(prediction['special'])}")
    print()

    print("🏦 庄家周期:", banker_cycle(draws))
    print()

    print("📈 最近10期严格回测（不偷看未来）")
    print(f"主码总命中: {bt['main_hit_total']}")
    print(f"至少中1个主码的期数: {bt['any_hit_total']} / {bt['total']}")
    print(f"特别号命中: {bt['special_hit_total']} / {bt['total']}")
    print(f"主码最大连空: {bt['max_consecutive_miss']} 期")
    print(f"主码平均命中: {bt['avg_main_hit']}")
    print()

    print("🧠 当前权重")
    for k, v in weights.items():
        print(f"{k}: {round(v, 4)}")

    print()
    print("📄 HTML面板已生成:", HTML_FILE.name)
    print("📄 趋势文本已生成:", TREND_FILE.name)
    print("=" * 70)

    generate_html(latest, prediction, bt)
    TREND_FILE.write_text(build_trend_text(draws), encoding="utf-8")

    conn.execute(
        """
        INSERT INTO prediction_runs (
            issue_no, created_at, main_json, special, confidence
        ) VALUES (?,?,?,?,?)
        """,
        (
            next_issue,
            utc_now(),
            json.dumps(prediction["main"], ensure_ascii=False),
            prediction["special"],
            prediction["confidence"],
        ),
    )
    conn.execute(
        """
        INSERT INTO backtest_runs (
            created_at, window_size, total, main_hit_total,
            any_hit_total, special_hit_total, max_consecutive_miss,
            avg_main_hit, details_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            utc_now(),
            10,
            bt["total"],
            bt["main_hit_total"],
            bt["any_hit_total"],
            bt["special_hit_total"],
            bt["max_consecutive_miss"],
            bt["avg_main_hit"],
            json.dumps(bt["details"], ensure_ascii=False),
        ),
    )
    conn.commit()

    # 轻量自学习：只用这次严格回测结果调整权重
    lr = get_learning_rate(conn)
    new_weights = update_weights_from_backtest(weights, bt, lr)
    save_weights(conn, new_weights)

    # 学习率也做微调，但限制在合理范围
    if bt["avg_main_hit"] < 1.2:
        lr = max(0.01, lr * 0.98)
    elif bt["avg_main_hit"] > 2.2:
        lr = min(0.2, lr * 1.02)
    set_learning_rate(conn, lr)

# =========================================================
# 主流程
# =========================================================

def sync_and_run() -> None:
    conn = connect_db()
    try:
        init_db(conn)
        print("正在获取真实新澳门六合彩数据...")
        rows = fetch_real_data()
        new_count = save_records(conn, rows)
        print(f"数据同步完成: total={len(rows)}, new={new_count}")
        draws = load_draws(conn)
        print_dashboard(draws, conn)
    finally:
        conn.close()

def show_only() -> None:
    conn = connect_db()
    try:
        init_db(conn)
        draws = load_draws(conn)
        print_dashboard(draws, conn)
    finally:
        conn.close()

def main() -> None:
    parser = argparse.ArgumentParser(description="新澳门六合彩 AI V12 严格时序版")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync")
    sub.add_parser("show")

    args = parser.parse_args()

    if args.cmd == "show":
        show_only()
    else:
        sync_and_run()

if __name__ == "__main__":
    main()