#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import ssl
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import numpy as np
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostClassifier

warnings.filterwarnings("ignore", category=UserWarning)

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "xinmacau.db")

OFFICIAL_URL_DEFAULT = (
    "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
)

THIRD_PARTY_URLS_DEFAULT: List[str] = [
    "https://marksix6.net/index.php?api=1"
]

MINED_CONFIG_KEY = "mined_strategy_config_v1"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "ensemble_v2": "集成投票",
    "pattern_mined_v1": "规律挖掘",
    "lgbm_v1": "LightGBM",
    "ml_window_v1": "ML窗口融合",
}

STRATEGY_IDS = [
    "balanced_v1",
    "hot_v1",
    "cold_rebound_v1",
    "momentum_v1",
    "ensemble_v2",
    "pattern_mined_v1",
    "lgbm_v1",
    "ml_window_v1",
]


# =========================================================
# 工具
# =========================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def safe_div(a, b):
    if b == 0:
        return 0.0
    return a / b


def fetch_json_url(url: str, timeout: int = 20):
    ctx = ssl.create_default_context()

    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        }
    )

    with urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="ignore")
        return json.loads(raw)


# =========================================================
# 波色
# =========================================================

def get_color(num: int) -> str:
    RED = {
        1, 2, 7, 8, 12, 13, 18, 19,
        23, 24, 29, 30, 34, 35, 40,
        45, 46
    }

    BLUE = {
        3, 4, 9, 10, 14, 15, 20,
        25, 26, 31, 36, 37, 41,
        42, 47, 48
    }

    GREEN = {
        5, 6, 11, 16, 17, 21, 22,
        27, 28, 32, 33, 38, 39,
        43, 44, 49
    }

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"


def special_attributes(num: int) -> Dict[str, str]:
    odd_even = "单" if num % 2 else "双"

    big_small = "大" if num >= 25 else "小"

    tens, ones = divmod(num, 10)

    total = tens + ones

    total_odd_even = "单" if total % 2 else "双"

    total_big_small = "大" if total >= 7 else "小"

    tail_big_small = "大" if ones >= 5 else "小"

    color = get_color(num)

    if ones in (1, 6):
        element = "水"
    elif ones in (2, 7):
        element = "火"
    elif ones in (3, 8):
        element = "木"
    elif ones in (4, 9):
        element = "金"
    else:
        element = "土"

    return {
        "单双": odd_even,
        "大小": big_small,
        "合单双": total_odd_even,
        "合大小": total_big_small,
        "尾大小": tail_big_small,
        "色波": color,
        "五行": element,
    }


# =========================================================
# 波色预测（三模型集成，连出奖励减半，指数平滑）
# =========================================================

def predict_color_weighted(
    specials: List[int],
    window: int = 12
) -> Tuple[str, str, float, float]:
    """
    三模型集成：
    - 模型A: 改进加权（衰减、连出奖励降低、极热压制）
    - 模型B: 马尔可夫转移矩阵
    - 模型C: 简单频率统计
    投票决定主强，平局时用模型A得分仲裁。
    """
    if not specials:
        return "绿", "红", 0.0, 0.0

    recent = specials[-window:]

    # ---- 模型A: 改进加权 ----
    scores_a = defaultdict(float)
    total_w = 0.0
    for i, num in enumerate(reversed(recent)):
        w = (window - i) ** 1.5          # 更平滑
        color = get_color(num)
        scores_a[color] += w
        total_w += w
        if i > 0:
            prev_idx = len(recent) - i
            if 0 <= prev_idx < len(recent):
                if color == get_color(recent[prev_idx]):
                    scores_a[color] += w * 0.3   # 连出奖励从0.68降到0.3

    # 长期缺失补偿
    miss_map = {"红":0,"蓝":0,"绿":0}
    for idx, n in enumerate(reversed(recent)):
        c = get_color(n)
        if miss_map[c] == 0: miss_map[c] = idx+1
    for c in miss_map:
        if miss_map[c] >= 5:
            scores_a[c] += miss_map[c] * 0.7

    # 极热压制（阈值提高到0.75）
    for c in ["红","蓝","绿"]:
        ratio = safe_div(scores_a[c], total_w)
        if ratio > 0.75:
            for other in ["红","蓝","绿"]:
                if other != c:
                    scores_a[other] += total_w * 0.1

    sorted_a = sorted(scores_a.items(), key=lambda x: x[1], reverse=True)
    main_a = sorted_a[0][0]

    # ---- 模型B: 马尔可夫 ----
    main_b, probs_b = predict_color_markov(specials)
    if not probs_b:
        main_b = main_a

    # ---- 模型C: 简单频率 ----
    freq = Counter(get_color(n) for n in recent)
    main_c = freq.most_common(1)[0][0]

    # ---- 投票集成 ----
    votes = Counter([main_a, main_b, main_c])
    top_voted = votes.most_common()
    if len(top_voted) == 1 or top_voted[0][1] > top_voted[1][1]:
        main_color = top_voted[0][0]
    else:
        # 平局，用模型A得分仲裁
        best_score = -1
        main_color = "绿"
        for color in [c for c,_ in top_voted]:
            if scores_a[color] > best_score:
                best_score = scores_a[color]
                main_color = color

    main_score = safe_div(scores_a[main_color], total_w)
    second_color = sorted_a[1][0] if len(sorted_a) > 1 else "红"
    second_score = safe_div(scores_a[second_color], total_w)

    return main_color, second_color, main_score, second_score


def predict_color(
    specials: List[int],
    window: int = 10,
    method: str = "weighted"
) -> Tuple[str, str, float, float]:

    if method == "simple":

        if not specials:
            return "蓝", "绿", 0.0, 0.0

        recent = specials[-window:]

        counter = Counter(get_color(n) for n in recent)

        sorted_colors = sorted(
            counter.items(),
            key=lambda x: (-x[1], x[0])
        )

        main_color = sorted_colors[0][0]
        main_freq = safe_div(sorted_colors[0][1], len(recent))

        if len(sorted_colors) > 1:
            second_color = sorted_colors[1][0]
            second_freq = safe_div(sorted_colors[1][1], len(recent))
        else:
            second_color = "绿"
            second_freq = 0.0

        return main_color, second_color, main_freq, second_freq

    return predict_color_weighted(specials, window)


# 状态转移矩阵
def build_color_transition_matrix(specials):

    matrix = {
        "红": defaultdict(int),
        "蓝": defaultdict(int),
        "绿": defaultdict(int)
    }

    if len(specials) < 2:
        return matrix

    colors = [get_color(n) for n in specials]

    for i in range(len(colors) - 1):
        current_c = colors[i]
        next_c = colors[i + 1]
        matrix[current_c][next_c] += 1

    return matrix


def predict_color_markov(specials):

    if len(specials) < 3:
        return "红", {}

    matrix = build_color_transition_matrix(specials)

    last_color = get_color(specials[-1])

    transitions = matrix[last_color]

    total = sum(transitions.values())

    if total == 0:
        return "红", {}

    probs = {
        c: transitions[c] / total
        for c in ["红", "蓝", "绿"]
    }

    best = max(probs.items(), key=lambda x: x[1])[0]

    return best, probs


# =========================================================
# 大小 & 单双预测
# =========================================================

def get_big_small(num: int) -> str:
    return "大" if num >= 25 else "小"

def get_odd_even(num: int) -> str:
    return "单" if num % 2 == 1 else "双"

def predict_big_small_weighted(
    specials: List[int],
    window: int = 12
) -> Tuple[str, str, float, float]:

    if not specials:
        return "大", "小", 0.0, 0.0

    recent = specials[-window:]

    scores = defaultdict(float)

    total_weight = 0.0

    for i, num in enumerate(reversed(recent)):

        weight = (window - i) ** 1.65

        bs = get_big_small(num)

        scores[bs] += weight

        total_weight += weight

        # 连挂增强
        if i > 0:

            prev_index = len(recent) - i

            if 0 <= prev_index < len(recent):

                prev_bs = get_big_small(recent[prev_index])

                if bs == prev_bs:
                    scores[bs] += weight * 0.52

    # 最近震荡识别
    seq = [get_big_small(x) for x in recent[-6:]]

    alt_count = 0

    for i in range(1, len(seq)):
        if seq[i] != seq[i - 1]:
            alt_count += 1

    if alt_count >= 4:

        last = seq[-1]

        reverse = "小" if last == "大" else "大"

        scores[reverse] += total_weight * 0.20

    # 极端热度压制
    big_ratio = safe_div(scores["大"], total_weight)
    small_ratio = safe_div(scores["小"], total_weight)

    if big_ratio > 0.80:
        scores["小"] += total_weight * 0.24

    if small_ratio > 0.80:
        scores["大"] += total_weight * 0.24

    sorted_bs = sorted(
        scores.items(),
        key=lambda x: (-x[1], x[0])
    )

    main = sorted_bs[0][0]

    second = sorted_bs[1][0]

    main_score = safe_div(sorted_bs[0][1], total_weight)

    second_score = safe_div(sorted_bs[1][1], total_weight)

    return main, second, main_score, second_score

def predict_odd_even_weighted(
    specials: List[int],
    window: int = 12
) -> Tuple[str, str, float, float]:

    if not specials:
        return "单", "双", 0.0, 0.0

    recent = specials[-window:]

    scores = defaultdict(float)

    total_weight = 0.0

    streak_bonus = 0.0

    reverse_bonus = 0.0

    for i, num in enumerate(reversed(recent)):

        weight = (window - i) ** 1.65

        oe = get_odd_even(num)

        scores[oe] += weight

        total_weight += weight

        # 连续趋势增强
        if i > 0:

            prev_index = len(recent) - i

            if 0 <= prev_index < len(recent):

                prev_oe = get_odd_even(recent[prev_index])

                if oe == prev_oe:
                    scores[oe] += weight * 0.55
                    streak_bonus += 1.0

                else:
                    reverse_bonus += 1.0

    # 周期震荡识别
    seq = [get_odd_even(x) for x in recent[-6:]]

    alt_count = 0

    for i in range(1, len(seq)):
        if seq[i] != seq[i - 1]:
            alt_count += 1

    if alt_count >= 4:

        last = seq[-1]

        reverse = "双" if last == "单" else "单"

        scores[reverse] += total_weight * 0.18

    # 极端压制
    single_ratio = safe_div(scores["单"], total_weight)
    double_ratio = safe_div(scores["双"], total_weight)

    if single_ratio > 0.78:
        scores["双"] += total_weight * 0.22

    if double_ratio > 0.78:
        scores["单"] += total_weight * 0.22

    sorted_oe = sorted(
        scores.items(),
        key=lambda x: (-x[1], x[0])
    )

    main = sorted_oe[0][0]

    second = sorted_oe[1][0]

    main_score = safe_div(sorted_oe[0][1], total_weight)

    second_score = safe_div(sorted_oe[1][1], total_weight)

    return main, second, main_score, second_score


def backtest_big_small(conn, recent_limit: int = 10, window: int = 10):
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + window: return 0, 0
    total = main_hit = 0
    start_idx = len(specials) - recent_limit
    for i in range(start_idx, len(specials)):
        train = specials[:i]
        actual = get_big_small(specials[i])
        main_bs, _, _, _ = predict_big_small_weighted(train, window)
        if main_bs == actual: main_hit += 1
        total += 1
    return total, main_hit

def backtest_odd_even(conn, recent_limit: int = 10, window: int = 10):
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + window: return 0, 0
    total = main_hit = 0
    start_idx = len(specials) - recent_limit
    for i in range(start_idx, len(specials)):
        train = specials[:i]
        actual = get_odd_even(specials[i])
        main_oe, _, _, _ = predict_odd_even_weighted(train, window)
        if main_oe == actual: main_hit += 1
        total += 1
    return total, main_hit


# =========================================================
# AI 融合终极预测
# =========================================================

def predict_final_attribute(
    specials: List[int],
    window: int = 12
):

    color_main, color_second, c1, c2 = predict_color_weighted(
        specials,
        window
    )

    bs_main, bs_second, b1, b2 = predict_big_small_weighted(
        specials,
        window
    )

    oe_main, oe_second, o1, o2 = predict_odd_even_weighted(
        specials,
        window
    )

    confidence = (
        c1 * 0.42 +
        b1 * 0.29 +
        o1 * 0.29
    )

    return {
        "波色": (color_main, color_second, c1, c2),
        "大小": (bs_main, bs_second, b1, b2),
        "单双": (oe_main, oe_second, o1, o2),
        "综合置信度": confidence
    }


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
# 数据库
# =========================================================

def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS draws (
            issue_no TEXT PRIMARY KEY,
            draw_date TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            special_number INTEGER NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prediction_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_no TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            hit_count INTEGER,
            hit_rate REAL,
            hit_count_10 INTEGER,
            hit_rate_10 REAL,
            hit_count_14 INTEGER,
            hit_rate_14 REAL,
            hit_count_20 INTEGER,
            hit_rate_20 REAL,
            special_hit INTEGER,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            UNIQUE(issue_no, strategy)
        );

        CREATE TABLE IF NOT EXISTS prediction_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            pick_type TEXT NOT NULL DEFAULT 'MAIN',
            number INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            score REAL NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE(run_id, pick_type, number),
            FOREIGN KEY(run_id)
                REFERENCES prediction_runs(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS prediction_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            pool_size INTEGER NOT NULL,
            numbers_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, pool_size),
            FOREIGN KEY(run_id)
                REFERENCES prediction_runs(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS model_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    _ensure_migrations(conn)

    conn.commit()


def _column_exists(conn, table, column):
    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(r["name"] == column for r in rows)


def _ensure_migrations(conn):
    migrations = [
        ("prediction_picks", "pick_type",
         "ALTER TABLE prediction_picks "
         "ADD COLUMN pick_type TEXT NOT NULL DEFAULT 'MAIN'"),

        ("prediction_runs", "special_hit",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN special_hit INTEGER"),

        ("prediction_runs", "hit_count_10",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_count_10 INTEGER"),

        ("prediction_runs", "hit_rate_10",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_rate_10 REAL"),

        ("prediction_runs", "hit_count_14",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_count_14 INTEGER"),

        ("prediction_runs", "hit_rate_14",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_rate_14 REAL"),

        ("prediction_runs", "hit_count_20",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_count_20 INTEGER"),

        ("prediction_runs", "hit_rate_20",
         "ALTER TABLE prediction_runs "
         "ADD COLUMN hit_rate_20 REAL"),
    ]

    for table, column, sql in migrations:
        try:
            if not _column_exists(conn, table, column):
                conn.execute(sql)
        except Exception:
            pass


# =========================================================
# model_state
# =========================================================

def get_model_state(conn, key):
    row = conn.execute(
        "SELECT value FROM model_state WHERE key=?",
        (key,)
    ).fetchone()

    return str(row["value"]) if row else None


def set_model_state(conn, key, value):
    now = utc_now()

    conn.execute("""
        INSERT INTO model_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key)
        DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
    """, (key, value, now))


# =========================================================
# 数据解析
# =========================================================

def _parse_marksix6_response(payload):
    records = []

    lottery_data = payload.get("lottery_data", [])

    hk_data = next(
        (
            l for l in lottery_data
            if l.get("name") in ["新澳门彩", "老澳门彩"]
        ),
        None
    )

    if not hk_data:
        return records

    try:
        latest_open_time = datetime.strptime(
            hk_data.get("openTime", ""),
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        latest_open_time = datetime.now()

    for idx, item in enumerate(hk_data.get("history", [])):
        try:
            parts = item.split("期：")

            if len(parts) != 2:
                continue

            issue_no = parts[0].strip()

            nums = [
                int(n.strip())
                for n in parts[1].split(",")
            ]

            if len(nums) != 7:
                continue

            draw_date = (
                latest_open_time - timedelta(days=idx)
            ).strftime("%Y-%m-%d")

            records.append(
                DrawRecord(
                    issue_no,
                    draw_date,
                    nums[:6],
                    nums[6]
                )
            )

        except Exception:
            continue

    return records


def _parse_official_json(payload):
    records = []

    for item in payload:
        try:
            issue_no = str(
                item.get("drawNo")
                or item.get("issueNo")
            )

            draw_date = str(
                item.get("drawDate", "")
            )[:10]

            numbers = [
                safe_int(item.get(f"no{i}"))
                for i in range(1, 7)
            ]

            special = safe_int(
                item.get("specialNumber")
                or item.get("no7")
            )

            if (
                issue_no
                and draw_date
                and len(numbers) == 6
            ):
                records.append(
                    DrawRecord(
                        issue_no,
                        draw_date,
                        numbers,
                        special
                    )
                )

        except Exception:
            continue

    return records


# =========================================================
# 在线同步
# =========================================================

def fetch_online_records_with_multi_fallback(
    official_url,
    third_party_urls
):

    if official_url.strip():
        try:
            payload = fetch_json_url(
                official_url,
                timeout=15
            )

            records = _parse_official_json(payload)

            if records:
                return records, "official_api", official_url

        except Exception as e:
            print(f"官方源失败: {e}")

    for url in third_party_urls:
        try:
            payload = fetch_json_url(url, timeout=20)

            if "marksix6.net" in url:
                records = _parse_marksix6_response(payload)
                source = "marksix6"
            else:
                records = _parse_official_json(payload)
                source = "third_party"

            if records:
                return records, source, url

        except (HTTPError, URLError, TimeoutError) as e:
            print(f"第三方源失败: {url} -> {e}")

        except Exception as e:
            print(f"第三方源异常: {url} -> {e}")

    raise RuntimeError("所有在线数据源均无法获取数据")


# =========================================================
# 数据写入
# =========================================================

def upsert_draw(conn, record, source):
    now = utc_now()

    exists = conn.execute(
        "SELECT 1 FROM draws WHERE issue_no=?",
        (record.issue_no,)
    ).fetchone()

    if exists:
        conn.execute("""
            UPDATE draws
            SET
                draw_date=?,
                numbers_json=?,
                special_number=?,
                source=?,
                updated_at=?
            WHERE issue_no=?
        """, (
            record.draw_date,
            safe_json_dumps(record.numbers),
            record.special_number,
            source,
            now,
            record.issue_no
        ))

        return "updated"

    conn.execute("""
        INSERT INTO draws
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        record.issue_no,
        record.draw_date,
        safe_json_dumps(record.numbers),
        record.special_number,
        source,
        now,
        now
    ))

    return "inserted"


def sync_from_records(conn, records, source):
    inserted = 0
    updated = 0

    for r in records:
        res = upsert_draw(conn, r, source)

        if res == "inserted":
            inserted += 1
        else:
            updated += 1

    conn.commit()

    return len(records), inserted, updated


# =========================================================
# 期号
# =========================================================

def next_issue(issue_no):
    digits = ''.join(
        ch for ch in issue_no
        if ch.isdigit()
    )

    if not digits:
        return issue_no

    num = int(digits) + 1

    if '/' in issue_no:
        parts = issue_no.rsplit('/', 1)

        return (
            f"{parts[0]}/"
            f"{num:0{len(digits)}d}"
        )

    return f"{num:0{len(digits)}d}"


# =========================================================
# 高级特性：稳定性、衰减、置信度、结构先验
# =========================================================

def stability_score(values):
    """值越稳定（方差小）且均值越大，分数越高"""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return mean / (1.0 + variance)


def cycle_confidence(gaps):
    """基于间隔序列的稳定性，返回0-1之间的置信度"""
    if len(gaps) < 3:
        return 0.0
    mean = sum(gaps) / len(gaps)
    variance = sum((x - mean) ** 2 for x in gaps) / len(gaps)
    return 1.0 / (1.0 + variance)


def structure_score(num: int, draws, specials) -> float:
    """
    结构先验：基于号码在历史中的奇偶、大小、尾数、波色出现概率
    返回0-1之间的先验合理性分数
    """
    # 历史统计（使用最近200期）
    recent_draws = draws[:200]
    recent_specials = specials[:200] if len(specials) >= 200 else specials

    flat_numbers = []
    for d in recent_draws:
        flat_numbers.extend(d)
    flat_numbers.extend(recent_specials)

    if not flat_numbers:
        return 0.5

    # 奇偶比例
    odd_count = sum(1 for x in flat_numbers if x % 2)
    odd_ratio = odd_count / len(flat_numbers)
    # 号码的奇偶匹配度
    odd_score = odd_ratio if num % 2 else (1 - odd_ratio)

    # 大小比例
    big_count = sum(1 for x in flat_numbers if x >= 25)
    big_ratio = big_count / len(flat_numbers)
    size_score = big_ratio if num >= 25 else (1 - big_ratio)

    # 尾数频率
    tail_counter = Counter(x % 10 for x in flat_numbers)
    total_tails = sum(tail_counter.values())
    tail_freq = tail_counter[num % 10] / total_tails if total_tails else 0.1

    # 波色频率
    color_counter = Counter(get_color(x) for x in flat_numbers)
    total_color = sum(color_counter.values())
    color_freq = color_counter[get_color(num)] / total_color if total_color else 0.33

    # 综合先验 (0-1)
    prior = (odd_score * 0.25 + size_score * 0.25 + tail_freq * 0.3 + color_freq * 0.2)
    # 归一化到合理范围
    return max(0.1, min(0.9, prior))


# =========================================================
# 核心评分
# =========================================================

def _normalize(score_map: Dict[int, float]):
    vals = list(score_map.values())

    if not vals:
        return {n: 0.0 for n in ALL_NUMBERS}

    mn = min(vals)
    mx = max(vals)

    if mx == mn:
        return {k: 0.0 for k in score_map}

    return {
        k: (v - mn) / (mx - mn)
        for k, v in score_map.items()
    }


def _freq_map(draws, decay_alpha=0.08):
    """带热度衰减的频率：近期越频繁出现，权重越低"""
    freq = {n: 0.0 for n in ALL_NUMBERS}
    counts = {n: 0 for n in ALL_NUMBERS}

    for draw in draws:
        for n in draw:
            counts[n] += 1

    # 衰减：频率越高，分数越小
    for n in ALL_NUMBERS:
        raw = counts[n]
        decay = math.exp(-raw * decay_alpha)
        freq[n] = raw * decay
    return freq


def _omission_map(draws):
    omission = {
        n: float(len(draws) + 1)
        for n in ALL_NUMBERS
    }

    for i, draw in enumerate(draws):
        for n in draw:
            omission[n] = min(
                omission[n],
                float(i + 1)
            )

    return omission


def build_cycle_stats(draws):
    """周期模型，使用 cycle_confidence 调整可靠性"""
    positions = defaultdict(list)

    for idx, draw in enumerate(draws):
        for n in draw:
            positions[n].append(idx)

    cycle_map = {}

    for n in ALL_NUMBERS:
        pos = positions[n]
        if len(pos) < 2:
            cycle_map[n] = 999
            continue
        gaps = [pos[i] - pos[i - 1] for i in range(1, len(pos))]
        conf = cycle_confidence(gaps)
        avg_gap = sum(gaps) / len(gaps)
        # 置信度低则视为不可靠，赋予较大gap
        if conf < 0.3:
            cycle_map[n] = 999
        else:
            cycle_map[n] = avg_gap

    return cycle_map


def _momentum_map(draws):
    m = {n: 0.0 for n in ALL_NUMBERS}

    for i, draw in enumerate(draws):
        w = 1.0 / (1.0 + i)

        for n in draw:
            m[n] += w

    return m


def _pair_affinity_map(draws, window=200):
    pair_count = {}

    for draw in draws[:window]:
        s = sorted(draw)

        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                key = (s[i], s[j])

                pair_count[key] = (
                    pair_count.get(key, 0) + 1
                )

    social = {n: 0.0 for n in ALL_NUMBERS}

    for (a, b), c in pair_count.items():
        social[a] += c
        social[b] += c

    return social


def _zone_heat_map(draws, window=80):
    zone_counts = [0.0] * 5

    w = draws[:window]

    if not w:
        return {n: 0.0 for n in ALL_NUMBERS}

    for draw in w:
        for n in draw:
            zone_counts[min(4, (n - 1) // 10)] += 1

    expected = 6.0 * len(w) / 5.0

    zone_score = [
        expected - c
        for c in zone_counts
    ]

    return {
        n: zone_score[min(4, (n - 1) // 10)]
        for n in ALL_NUMBERS
    }


def build_ml_features(draws):
    features = {}

    recent5 = draws[:5]
    recent20 = draws[:20]

    freq5 = _freq_map(recent5)
    freq20 = _freq_map(recent20)

    omit = _omission_map(draws)

    momentum = _momentum_map(recent20)

    for n in ALL_NUMBERS:
        features[n] = {
            "freq5": freq5[n],
            "freq20": freq20[n],
            "omit": omit[n],
            "momentum": momentum[n],
            "zone": (n - 1) // 10,
            "odd": n % 2,
            "big": 1 if n >= 25 else 0,
        }

    return features


def build_multi_window_features(draws):
    windows = [20, 50, 120, 240]
    result = {
        n: {"freq": 0.0, "omit": 0.0, "momentum": 0.0}
        for n in ALL_NUMBERS
    }

    for w in windows:
        sub = draws[:w] if len(draws) >= w else draws
        freq = _normalize(_freq_map(sub))
        omit = _normalize(_omission_map(sub))
        mom = _normalize(_momentum_map(sub))

        weight = {20: 0.35, 50: 0.30, 120: 0.20, 240: 0.15}[w]

        for n in ALL_NUMBERS:
            result[n]["freq"] += freq[n] * weight
            result[n]["omit"] += omit[n] * weight
            result[n]["momentum"] += mom[n] * weight

    return result    
# =========================================================
# 结构评估（高级规则系统，用于排除组合）
# =========================================================

def evaluate_structure(nums: List[int]) -> float:
    """返回结构惩罚分，越小越合理"""
    penalty = 0.0
    n = len(nums)

    if n < 2:
        return 0.0

    # 奇偶极端
    odd_count = sum(1 for x in nums if x % 2)
    even_count = n - odd_count
    if odd_count >= 5:
        penalty += 0.3
    if even_count >= 5:
        penalty += 0.3

    # 大小极端
    big_count = sum(1 for x in nums if x >= 25)
    small_count = n - big_count
    if big_count >= 5 or small_count >= 5:
        penalty += 0.2

    # 连号
    sorted_nums = sorted(nums)
    consec = 1
    max_consec = 1
    for i in range(1, len(sorted_nums)):
        if sorted_nums[i] - sorted_nums[i-1] == 1:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 1
    if max_consec >= 3:
        penalty += 0.25

    # 尾数冲突
    tail_counter = Counter(x % 10 for x in nums)
    for cnt in tail_counter.values():
        if cnt >= 3:
            penalty += 0.15 * (cnt - 2)

    # 波色平衡
    color_counter = Counter(get_color(x) for x in nums)
    for cnt in color_counter.values():
        if cnt >= 4:
            penalty += 0.2
        if cnt == 0 and n >= 5:
            penalty += 0.1

    # 和值约束
    total_sum = sum(nums)
    dev = abs(total_sum - 150)
    if dev > 40:
        penalty += 0.3
    elif dev > 30:
        penalty += 0.15

    return penalty


# =========================================================
# 选号（加入结构评估+结构先验融合）
# =========================================================

def _pick_top_six(scores, reason):
    ranked = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    picked = []

    for n, s in ranked:
        if len(picked) >= 6:
            break

        proposal = [pn for pn, _ in picked] + [n]

        odd_count = sum(1 for x in proposal if x % 2 == 1)
        if len(proposal) >= 4:
            if odd_count == 0 or odd_count == len(proposal):
                continue

        zone_counts = {}
        for x in proposal:
            z = min(4, (x - 1) // 10)
            zone_counts[z] = zone_counts.get(z, 0) + 1
        if any(c >= 4 for c in zone_counts.values()):
            continue

        penalty = evaluate_structure(proposal)
        if penalty > 0.7:  # 放宽到0.7
            continue

        picked.append((n, s))

    while len(picked) < 6:
        for n, s in ranked:
            if n not in [pn for pn, _ in picked]:
                picked.append((n, s))
                break

    return [
        (n, idx + 1, s, f"{reason} score={s:.4f}")
        for idx, (n, s) in enumerate(picked)
    ]


# =========================================================
# 特别号独立模型（已优化：随机扰动 + 并列随机）
# =========================================================

def _specials_omission_map(specials, window=None):
    sp = specials[-window:] if window else specials
    omission = {n: float(len(sp) + 1) for n in ALL_NUMBERS}
    for i, s in enumerate(reversed(sp)):
        if 1 <= s <= 49:
            omission[s] = min(omission[s], float(i + 1))
    return omission


def _specials_momentum_map(specials, window=None):
    sp = specials[-window:] if window else specials
    m = {n: 0.0 for n in ALL_NUMBERS}
    for i, s in enumerate(reversed(sp)):
        w = 1.0 / (1.0 + i)
        if 1 <= s <= 49:
            m[s] += w
    return m


def tail_stats(specials):
    tails = [s % 10 for s in specials]
    freq = {t: 0 for t in range(10)}
    for t in tails:
        freq[t] += 1
    total = len(tails) or 1
    tail_freq = {t: freq[t] / total for t in range(10)}

    tail_omit = {}
    for t in range(10):
        found = False
        for i, s in enumerate(reversed(specials)):
            if s % 10 == t:
                tail_omit[t] = i + 1
                found = True
                break
        if not found:
            tail_omit[t] = len(specials) + 1

    positions = defaultdict(list)
    for idx, s in enumerate(specials):
        positions[s % 10].append(idx)
    tail_cycle = {}
    for t in range(10):
        pos = positions[t]
        if len(pos) < 2:
            tail_cycle[t] = 999
        else:
            gaps = [pos[i] - pos[i-1] for i in range(1, len(pos))]
            tail_cycle[t] = sum(gaps) / len(gaps)

    return {
        "freq": tail_freq,
        "omit": tail_omit,
        "cycle": tail_cycle
    }


def color_stats(specials):
    colors = [get_color(s) for s in specials]
    freq = {"红": 0, "蓝": 0, "绿": 0}
    for c in colors:
        freq[c] += 1
    total = len(colors) or 1
    color_freq = {c: freq[c] / total for c in freq}

    color_omit = {}
    for c in ["红", "蓝", "绿"]:
        found = False
        for i, s in enumerate(reversed(specials)):
            if get_color(s) == c:
                color_omit[c] = i + 1
                found = True
                break
        if not found:
            color_omit[c] = len(specials) + 1

    return {
        "freq": color_freq,
        "omit": color_omit
    }


def generate_special_model(draws, main_numbers, specials):
    if len(specials) < 10:
        candidates = [n for n in ALL_NUMBERS if n not in set(main_numbers)]
        if not candidates:
            candidates = ALL_NUMBERS.copy()
        return random.choice(candidates), 0.0

    try:
        color_main, color_second, c1, c2 = predict_color_weighted(specials, window=12)
    except:
        color_main, color_second, c1, c2 = "红", "蓝", 0.5, 0.3

    try:
        oe_main, oe_second, o1, o2 = predict_odd_even_weighted(specials, window=12)
    except:
        oe_main, oe_second, o1, o2 = "单", "双", 0.5, 0.3

    omit_raw = _specials_omission_map(specials)
    momentum_raw = _specials_momentum_map(specials)

    max_omit = max(omit_raw.values()) or 1.0
    max_mom = max(momentum_raw.values()) or 1.0

    tstats = tail_stats(specials)
    max_tail_omit = max(tstats["omit"].values()) or 1.0

    best_num = None
    best_score = -1.0
    top_candidates = []

    # ★ 关键：将候选号码随机打乱
    candidates_pool = [n for n in ALL_NUMBERS if n not in main_numbers]
    random.shuffle(candidates_pool)

    for n in candidates_pool:
        c = get_color(n)
        if c == color_main:
            color_score = c1
        elif c == color_second:
            color_score = c2
        else:
            color_score = max(0.0, 1.0 - c1 - c2)

        oe = get_odd_even(n)
        if oe == oe_main:
            oe_score = o1
        elif oe == oe_second:
            oe_score = o2
        else:
            oe_score = max(0.0, 1.0 - o1 - o2)

        omit_score = omit_raw[n] / max_omit
        mom_score = momentum_raw[n] / max_mom

        tail = n % 10
        tail_freq = tstats["freq"].get(tail, 0.0)
        tail_omit = tstats["omit"].get(tail, 1)
        tail_cycle = tstats["cycle"].get(tail, 999)
        if tail_cycle < 1:
            tail_cycle = 1
        tail_score = (tail_freq * 0.4 + (1.0 - tail_omit / max_tail_omit) * 0.4 + (1.0 / tail_cycle) * 0.2)

        score = (color_score * 0.25 +
                 tail_score * 0.25 +
                 oe_score * 0.20 +
                 omit_score * 0.15 +
                 mom_score * 0.15)

        score += random.uniform(-0.005, 0.005)   # 稍微加大随机扰动

        if score > best_score + 1e-6:
            best_score = score
            top_candidates = [n]
        elif abs(score - best_score) < 1e-6:
            top_candidates.append(n)

    if top_candidates:
        best_num = random.choice(top_candidates)
    else:
        best_num = random.choice(candidates_pool)

    return best_num, best_score


# =========================================================
# 动态贝叶斯权重（加入稳定性）
# =========================================================

def get_dynamic_strategy_weights(conn):
    rows = conn.execute("""
        SELECT
            strategy,
            AVG(hit_count) AS avg_hit
        FROM prediction_runs
        WHERE status='REVIEWED'
        GROUP BY strategy
    """).fetchall()

    raw = {}
    for r in rows:
        raw[r["strategy"]] = safe_float(r["avg_hit"])

    total = sum(raw.values())
    if total <= 0:
        return {s: 1.0 / len(STRATEGY_IDS) for s in STRATEGY_IDS}

    return {k: v / total for k, v in raw.items()}


def get_dynamic_strategy_weights_before_id(conn, max_run_id):
    rows = conn.execute("""
        SELECT
            strategy,
            AVG(hit_count) AS avg_hit
        FROM prediction_runs
        WHERE status='REVIEWED'
        AND id < ?
        GROUP BY strategy
    """, (max_run_id,)).fetchall()

    raw = {}
    for r in rows:
        raw[r["strategy"]] = safe_float(r["avg_hit"])

    total = sum(raw.values())
    if total <= 0:
        return {s: 1.0 / len(STRATEGY_IDS) for s in STRATEGY_IDS}

    return {k: v / total for k, v in raw.items()}


def get_dynamic_weights_with_stability(conn):
    """基于平均命中率和稳定性综合调整"""
    rows = conn.execute("""
        SELECT strategy, hit_count
        FROM prediction_runs
        WHERE status='REVIEWED'
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    strat_hits = defaultdict(list)
    for r in rows:
        strat_hits[r["strategy"]].append(r["hit_count"])

    weights = {}
    for s in STRATEGY_IDS:
        hits = strat_hits.get(s, [])
        if not hits:
            weights[s] = 1.0 / len(STRATEGY_IDS)
            continue
        mean = sum(hits) / len(hits)
        stab = stability_score(hits)  # 稳定性分数
        # 综合评分 = 平均命中率 * 0.6 + 稳定性分数 * 0.4 (稳定性分数已是均值/方差)
        # 但 stability_score 已经融合了均值和方差，可直接用作权重
        weights[s] = stab

    total = sum(weights.values())
    if total > 0:
        return {k: v / total for k, v in weights.items()}
    return {s: 1.0 / len(STRATEGY_IDS) for s in STRATEGY_IDS}


# =========================================================
# 权重策略（加入周期置信度、冷号限制、结构先验）
# =========================================================

def _apply_weight_config(draws, config, reason, specials=None):
    window_size = int(config.get("window", 80))
    window = draws[:max(20, window_size)]

    # 多窗口频率（带衰减已在 _freq_map 实现）
    multi_features = build_multi_window_features(window)
    freq = _normalize({n: multi_features[n]["freq"] for n in ALL_NUMBERS})

    # 遗漏周期调整
    omission_raw = _omission_map(window)
    cycle_stats = build_cycle_stats(window)  # 已含置信度
    cycle_score_map = {}
    for n in ALL_NUMBERS:
        avg_gap = cycle_stats.get(n, 999)
        if avg_gap < 1:
            avg_gap = 1
        cycle_score_map[n] = omission_raw[n] / avg_gap
    omission = _normalize(cycle_score_map)

    momentum = _normalize(_momentum_map(window))

    pair = _normalize(
        _pair_affinity_map(
            window,
            window=min(200, len(window))
        )
    )

    zone = _normalize(
        _zone_heat_map(
            window,
            window=min(80, len(window))
        )
    )

    w_freq = safe_float(config.get("w_freq", 0.45))
    w_omit = safe_float(config.get("w_omit", 0.35))
    # 回补幻觉：限制冷号权重
    w_omit = min(w_omit, 0.25)
    w_mom = safe_float(config.get("w_mom", 0.20))
    w_pair = safe_float(config.get("w_pair", 0.00))
    w_zone = safe_float(config.get("w_zone", 0.00))

    # 归一化保证总和为1（忽略pair/zone如果为0）
    total_w = w_freq + w_omit + w_mom + w_pair + w_zone
    if total_w > 0:
        w_freq /= total_w
        w_omit /= total_w
        w_mom /= total_w
        w_pair /= total_w
        w_zone /= total_w

    scores = {}
    for n in ALL_NUMBERS:
        scores[n] = (
            freq[n] * w_freq
            + omission[n] * w_omit
            + momentum[n] * w_mom
            + pair[n] * w_pair
            + zone[n] * w_zone
        )

    # 结构先验融合 (0.7模型 + 0.3结构先验)
    model_norm = _normalize(scores)
    struct_map = {n: structure_score(n, draws, specials or []) for n in ALL_NUMBERS}
    struct_norm = _normalize(struct_map)

    final_scores = {}
    for n in ALL_NUMBERS:
        final_scores[n] = model_norm[n] * 0.7 + struct_norm[n] * 0.3

    main_picks = _pick_top_six(final_scores, reason)
    main_numbers = [n for n, _, _, _ in main_picks]

    if specials is not None and len(specials) >= 10:
        special_number, special_score = generate_special_model(draws, main_numbers, specials)
    else:
        candidates = [(n, s) for n, s in sorted(final_scores.items(), key=lambda x: x[1], reverse=True) if n not in set(main_numbers)]
        if not candidates:
            candidates = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        special_number, special_score = candidates[0]

    return (
        main_picks,
        special_number,
        special_score,
        final_scores
    )


# =========================================================
# 策略相似度与去相关
# =========================================================

def cosine_similarity(vec1, vec2):
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def compute_strategy_similarities(score_maps):
    """基于得分向量的余弦相似度，返回每个策略的平均相似度"""
    vectors = []
    for m in score_maps:
        vec = [m[n] for n in ALL_NUMBERS]
        vectors.append(vec)

    n_strat = len(vectors)
    similarities = [0.0] * n_strat
    for i in range(n_strat):
        sim_sum = 0.0
        for j in range(n_strat):
            if i != j:
                sim_sum += cosine_similarity(vectors[i], vectors[j])
        similarities[i] = sim_sum / max(1, n_strat - 1)
    return similarities


# =========================================================
# 策略（原有传统策略）
# =========================================================

def _ensemble_strategy(draws, mined_cfg=None, conn=None, specials=None):
    if conn is not None:
        dynamic_weights = get_dynamic_weights_with_stability(conn)
    else:
        dynamic_weights = {s: 1.0 / len(STRATEGY_IDS) for s in STRATEGY_IDS}

    m_hot = _apply_weight_config(draws, {"window": 80, "w_freq": 0.8, "w_omit": 0.0, "w_mom": 0.2, "w_pair": 0.0, "w_zone": 0.0}, "热号", specials)
    m_cold = _apply_weight_config(draws, {"window": 80, "w_freq": 0.0, "w_omit": 0.7, "w_mom": 0.3, "w_pair": 0.0, "w_zone": 0.0}, "冷号", specials)
    m_mom = _apply_weight_config(draws, {"window": 80, "w_freq": 0.1, "w_omit": 0.0, "w_mom": 0.9, "w_pair": 0.0, "w_zone": 0.0}, "动量", specials)
    m_bal = _apply_weight_config(draws, {"window": 80, "w_freq": 0.4, "w_omit": 0.3, "w_mom": 0.2, "w_pair": 0.05, "w_zone": 0.05}, "平衡", specials)
    m_mined = _apply_weight_config(draws, mined_cfg or _default_mined_config(), "规律挖掘", specials)

    score_maps = [
        m_hot[3],
        m_cold[3],
        m_mom[3],
        m_bal[3],
        m_mined[3],
    ]

    strategy_ids_order = ["hot_v1", "cold_rebound_v1", "momentum_v1", "balanced_v1", "pattern_mined_v1"]
    similarities = compute_strategy_similarities(score_maps)
    adj_weights = {}
    for i, s_id in enumerate(strategy_ids_order):
        raw_w = dynamic_weights.get(s_id, 0.2)
        sim = similarities[i]
        effective_w = raw_w * (1.0 - sim * 0.5)
        adj_weights[s_id] = effective_w

    total_adj = sum(adj_weights.values())
    if total_adj > 0:
        for s_id in adj_weights:
            adj_weights[s_id] /= total_adj
    else:
        adj_weights = {s: 1.0 / len(strategy_ids_order) for s in strategy_ids_order}

    votes = {n: 0.0 for n in ALL_NUMBERS}
    for i, m in enumerate(score_maps):
        w = adj_weights[strategy_ids_order[i]]
        ranked = sorted(m.items(), key=lambda x: x[1], reverse=True)
        for rank, (n, _) in enumerate(ranked):
            votes[n] += float(49 - rank) * w

    voted = _normalize(votes)
    main_picks = _pick_top_six(voted, "集成投票")
    main_numbers = [n for n, _, _, _ in main_picks]

    if specials is not None and len(specials) >= 10:
        special_number, special_score = generate_special_model(draws, main_numbers, specials)
    else:
        candidates = [(n, s) for n, s in sorted(voted.items(), key=lambda x: x[1], reverse=True) if n not in set(main_numbers)]
        if not candidates:
            candidates = sorted(voted.items(), key=lambda x: x[1], reverse=True)
        special_number, special_score = candidates[0]

    return main_picks, special_number, special_score, voted


# =========================================================
# 新增：LightGBM 单模型策略
# =========================================================

def extract_lgbm_features(past_draws: List[List[int]], past_specials: List[int], num: int) -> List[float]:
    draws = [list(d) for d in past_draws]
    specials = list(past_specials)
    total = len(draws)

    omission = 0
    for i in range(total - 1, -1, -1):
        if num in draws[i]:
            omission = total - i
            break
    if omission == 0:
        omission = total + 1

    def recent_count(arr, window, condition=None, value=None):
        recent = arr[-window:] if window > 0 else arr
        if value is not None:
            return sum(1 for x in recent if x == value)
        if condition:
            return sum(1 for x in recent if condition(x))
        return 0

    freq10 = recent_count(draws, 10, condition=lambda d: num in d)
    freq20 = recent_count(draws, 20, condition=lambda d: num in d)
    freq50 = recent_count(draws, 50, condition=lambda d: num in d)

    zone_idx = (num - 1) // 10
    zone_total_10 = sum(sum(1 for x in d if (x - 1) // 10 == zone_idx) for d in draws[-10:])
    zone_ratio_10 = zone_total_10 / (len(draws[-10:]) * 6) if total >= 10 else 0.0

    odd_10 = sum(1 for d in draws[-10:] for x in d if x % 2)
    odd_ratio_10 = odd_10 / (len(draws[-10:]) * 6) if total >= 10 else 0.5

    big_10 = sum(1 for d in draws[-10:] for x in d if x >= 25)
    big_ratio_10 = big_10 / (len(draws[-10:]) * 6) if total >= 10 else 0.5

    sums_10 = [sum(d) for d in draws[-10:]]
    avg_sum_10 = np.mean(sums_10) if sums_10 else 0.0
    std_sum_10 = np.std(sums_10) if len(sums_10) > 1 else 0.0

    spec_omission = 0
    for i in range(total - 1, -1, -1):
        if specials[i] == num:
            spec_omission = total - i
            break
    if spec_omission == 0:
        spec_omission = total + 1
    spec_freq10 = recent_count(specials, 10, value=num)

    return [
        omission, freq10, freq20, freq50,
        zone_ratio_10,
        odd_ratio_10 if num % 2 else 1 - odd_ratio_10,
        big_ratio_10 if num >= 25 else 1 - big_ratio_10,
        avg_sum_10, std_sum_10,
        spec_omission, spec_freq10,
        num / 49.0, num % 2, 1 if num >= 25 else 0
    ]


def build_lgbm_dataset(draws, specials, stop_issue):
    X, y = [], []
    for i in range(10, stop_issue):
        past_d = draws[:i]
        past_s = specials[:i]
        cur = draws[i]
        for num in ALL_NUMBERS:
            X.append(extract_lgbm_features(past_d, past_s, num))
            y.append(1 if num in cur else 0)
    return np.array(X), np.array(y)


def train_lgbm_model(draws, specials, stop_issue) -> lgb.LGBMClassifier:
    X, y = build_lgbm_dataset(draws, specials, stop_issue)
    model = lgb.LGBMClassifier(
        n_estimators=200, learning_rate=0.05, num_leaves=31,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1, force_col_wise=True,
    )
    model.fit(X, y)
    return model


def lgbm_predict_probs(model, past_draws, past_specials) -> Dict[int, float]:
    feats = [extract_lgbm_features(past_draws, past_specials, n) for n in ALL_NUMBERS]
    X = np.array(feats)
    probs = model.predict_proba(X)[:, 1]
    return dict(zip(ALL_NUMBERS, probs))


def lgbm_strategy(draws, specials):
    if len(draws) < 20:
        return _apply_weight_config(draws, {"window":80,"w_freq":0.4,"w_omit":0.3,"w_mom":0.2,"w_pair":0.05,"w_zone":0.05}, "LGBM回退", specials)
    model = train_lgbm_model(draws, specials, len(draws))
    probs = lgbm_predict_probs(model, draws, specials)
    ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    picked = []
    for n, prob in ranked:
        if len(picked) >= 6: break
        proposal = [pn for pn, _ in picked] + [n]
        if evaluate_structure(proposal) > 0.7: continue  # 放宽到0.7
        picked.append((n, prob))
    while len(picked) < 6:
        for n, prob in ranked:
            if n not in [pn for pn, _ in picked]:
                picked.append((n, prob))
                break
    main_picks = [(n, i + 1, prob, f"LGBM prob={prob:.4f}") for i, (n, prob) in enumerate(picked)]
    main_nums = [n for n, _, _, _ in main_picks]
    special_num, special_score = generate_special_model(draws, main_nums, specials)
    return main_picks, special_num, special_score, probs


def walk_forward_backtest_lgbm(draws, specials, start_train_size=100) -> Dict:
    total = hits = top10_hits = spec_hits = 0
    for test_idx in range(start_train_size, len(draws)):
        train_d = draws[:test_idx]
        train_s = specials[:test_idx]
        if len(train_d) < 20: continue
        model = train_lgbm_model(train_d, train_s, len(train_d))
        probs = lgbm_predict_probs(model, train_d, train_s)
        ranked = sorted(probs.items(), key=lambda x: x[1], reverse=True)
        main6 = []
        for n, _ in ranked:
            if len(main6) >= 6: break
            proposal = main6 + [n]
            if evaluate_structure(proposal) > 0.7: continue
            main6.append(n)
        actual = draws[test_idx]
        hit = len(set(main6) & set(actual))
        hits += hit
        top10 = [n for n, _ in ranked[:10]]
        top10_hits += len(set(top10) & set(actual))
        spec_pred, _ = generate_special_model(train_d, main6, train_s)
        spec_hits += 1 if spec_pred == specials[test_idx] else 0
        total += 1
    return {
        "total": total,
        "avg_hit": safe_div(hits, total),
        "top10_avg_hit": safe_div(top10_hits, total),
        "special_hit_rate": safe_div(spec_hits, total),
    }


# =========================================================
# 新增：时序窗口多模型融合策略
# =========================================================

WINDOW_SIZE = 5

def build_window_features(draws: List[List[int]], stop_index: int) -> Optional[np.ndarray]:
    if stop_index < WINDOW_SIZE:
        return None
    window = draws[stop_index - WINDOW_SIZE: stop_index]
    flat = []
    for draw in window:
        flat.extend(draw)
    features = [n / 49.0 for n in flat]
    odd_count = sum(1 for n in flat if n % 2)
    big_count = sum(1 for n in flat if n >= 25)
    total_sum = sum(flat)
    features.append(odd_count / len(flat))
    features.append(big_count / len(flat))
    features.append(total_sum / (49 * len(flat)))
    return np.array(features)


def create_window_dataset(draws, specials, start, end):
    X_list, y_list = [], []
    for idx in range(start, end):
        feats = build_window_features(draws, idx)
        if feats is None:
            continue
        cur = draws[idx]
        for num in ALL_NUMBERS:
            X_list.append(feats)
            y_list.append(1 if num in cur else 0)
    return np.array(X_list), np.array(y_list)


def train_ensemble_models(draws, specials, train_end_idx):
    X, y = create_window_dataset(draws, specials, WINDOW_SIZE, train_end_idx)
    models = {}
    models["lgb"] = lgb.LGBMClassifier(
        n_estimators=150, learning_rate=0.05, num_leaves=31,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1, force_col_wise=True
    ).fit(X, y)
    models["xgb"] = xgb.XGBClassifier(
        n_estimators=150, learning_rate=0.05, max_depth=6,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0, use_label_encoder=False, eval_metric='logloss'
    ).fit(X, y)
    models["cat"] = CatBoostClassifier(
        iterations=150, learning_rate=0.05, depth=6,
        subsample=0.8, random_seed=42, verbose=0
    ).fit(X, y)
    return models


def ensemble_predict_proba(models, draws, predict_idx):
    feats = build_window_features(draws, predict_idx)
    if feats is None:
        return {n: 0.0 for n in ALL_NUMBERS}
    probas = {}
    for name in ["lgb", "xgb", "cat"]:
        X_all = np.tile(feats, (49, 1))
        probs = models[name].predict_proba(X_all)[:, 1]
        for i, num in enumerate(ALL_NUMBERS):
            probas[num] = probas.get(num, 0.0) + probs[i] / 3.0
    return probas


def ml_window_strategy(draws, specials):
    if len(draws) < WINDOW_SIZE + 5:
        return _apply_weight_config(draws, {"window":80,"w_freq":0.4,"w_omit":0.3,"w_mom":0.2,"w_pair":0.05,"w_zone":0.05}, "ML窗口回退", specials)
    models = train_ensemble_models(draws, specials, len(draws))
    probas = ensemble_predict_proba(models, draws, len(draws))
    ranked = sorted(probas.items(), key=lambda x: x[1], reverse=True)
    picked = []
    for n, prob in ranked:
        if len(picked) >= 6: break
        proposal = [pn for pn, _ in picked] + [n]
        if evaluate_structure(proposal) > 0.7: continue
        picked.append((n, prob))
    while len(picked) < 6:
        for n, prob in ranked:
            if n not in [pn for pn, _ in picked]:
                picked.append((n, prob))
                break
    main_picks = [(n, i + 1, prob, f"ML融合 prob={prob:.4f}") for i, (n, prob) in enumerate(picked)]
    main_nums = [n for n, _, _, _ in main_picks]
    special_num, special_score = generate_special_model(draws, main_nums, specials)
    return main_picks, special_num, special_score, probas


# =========================================================
# 默认策略配置
# =========================================================

def _default_mined_config():
    return {"window": 80.0, "w_freq": 0.40, "w_omit": 0.30, "w_mom": 0.20, "w_pair": 0.05, "w_zone": 0.05, "special_bonus": 0.10}    
# =========================================================
# 策略调度（包含新增策略）
# =========================================================

def generate_strategy(draws, strategy, mined_config=None, conn=None, specials=None):
    if strategy == "lgbm_v1":
        return lgbm_strategy(draws, specials)
    if strategy == "ml_window_v1":
        return ml_window_strategy(draws, specials)
    if strategy == "hot_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.8, "w_omit": 0.0, "w_mom": 0.2, "w_pair": 0.0, "w_zone": 0.0}, "热号", specials)
    if strategy == "cold_rebound_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.0, "w_omit": 0.7, "w_mom": 0.3, "w_pair": 0.0, "w_zone": 0.0}, "冷号", specials)
    if strategy == "momentum_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.1, "w_omit": 0.0, "w_mom": 0.9, "w_pair": 0.0, "w_zone": 0.0}, "动量", specials)
    if strategy == "ensemble_v2":
        return _ensemble_strategy(draws, mined_config, conn, specials)
    if strategy == "pattern_mined_v1":
        cfg = mined_config or _default_mined_config()
        return _apply_weight_config(draws, cfg, "规律挖掘", specials)
    return _apply_weight_config(draws, {"window": 80, "w_freq": 0.4, "w_omit": 0.3, "w_mom": 0.2, "w_pair": 0.05, "w_zone": 0.05}, "平衡", specials)


# =========================================================
# Walk-forward 回测（原有集成策略回测，保留）
# =========================================================

def walk_forward_backtest(draws, specials, train_size=100, test_size=50):
    if len(draws) < train_size + test_size:
        raise ValueError("Not enough data for walk-forward backtest")

    results = []
    for start in range(train_size, len(draws) - test_size + 1):
        train_draws = draws[:start]
        train_specials = specials[:start]
        picks, special_num, special_score, _ = _ensemble_strategy(
            train_draws,
            mined_cfg=None,
            conn=None,
            specials=train_specials
        )
        actual_draw = draws[start]
        actual_special = specials[start]
        main_nums = [n for n, _, _, _ in picks]
        hit = len(set(main_nums) & set(actual_draw))
        special_hit = 1 if special_num == actual_special else 0
        results.append({
            "issue_index": start,
            "hit": hit,
            "special_hit": special_hit
        })
    return results


# =========================================================
# 候选池
# =========================================================

def _build_candidate_pools(scores, main6):
    ranked = [n for n, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
    rest = [n for n in ranked if n not in main6]
    pool10 = main6 + rest[:max(0, 10 - len(main6))]
    pool14 = main6 + rest[:max(0, 14 - len(main6))]
    pool20 = main6 + rest[:max(0, 20 - len(main6))]
    return {6: main6, 10: pool10, 14: pool14, 20: pool20}


def _pool_hit_count(pool, winning):
    return len([n for n in pool if n in winning])


def _save_prediction_pools(conn, run_id, pools):
    conn.execute("DELETE FROM prediction_pools WHERE run_id=?", (run_id,))
    now = utc_now()
    for size, nums in pools.items():
        conn.execute("INSERT INTO prediction_pools(run_id, pool_size, numbers_json, created_at) VALUES (?, ?, ?, ?)",
                     (run_id, size, safe_json_dumps(nums), now))


# =========================================================
# 预测生成
# =========================================================

def generate_predictions(conn, issue_no=None):
    row = conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("No draws in database")

    target_issue = issue_no or next_issue(row["issue_no"])

    draws = [json.loads(r["numbers_json"]) for r in conn.execute("SELECT numbers_json FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200").fetchall()]
    specials = [r["special_number"] for r in conn.execute("SELECT special_number FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200").fetchall()]

    if len(draws) < 20:
        raise RuntimeError("Need at least 20 draws")

    config_json = get_model_state(conn, MINED_CONFIG_KEY)
    mined_cfg = json.loads(config_json) if config_json else _default_mined_config()

    for strategy in STRATEGY_IDS:
        now = utc_now()
        conn.execute("INSERT OR REPLACE INTO prediction_runs(issue_no, strategy, status, created_at) VALUES (?, ?, 'PENDING', ?)",
                     (target_issue, strategy, now))
        run_id = conn.execute("SELECT id FROM prediction_runs WHERE issue_no=? AND strategy=?", (target_issue, strategy)).fetchone()["id"]

        picks, special_number, special_score, scores = generate_strategy(draws, strategy, mined_cfg, conn, specials)

        conn.execute("DELETE FROM prediction_picks WHERE run_id=?", (run_id,))
        conn.executemany("INSERT OR REPLACE INTO prediction_picks(run_id, pick_type, number, rank, score, reason) VALUES (?, ?, ?, ?, ?, ?)",
                         [(run_id, "MAIN", n, rank, score, reason) for n, rank, score, reason in picks] +
                         [(run_id, "SPECIAL", special_number, 1, special_score, "特别号")])

        main_numbers = [n for n, _, _, _ in picks]
        pools = _build_candidate_pools(scores, main_numbers)
        _save_prediction_pools(conn, run_id, pools)

    conn.commit()
    return target_issue


# =========================================================
# 复盘
# =========================================================

def review_issue(conn, issue_no):
    draw = conn.execute("SELECT numbers_json, special_number FROM draws WHERE issue_no=?", (issue_no,)).fetchone()
    if not draw:
        return 0

    winning = set(json.loads(draw["numbers_json"]))
    winning_special = safe_int(draw["special_number"])

    runs = conn.execute("SELECT id FROM prediction_runs WHERE issue_no=? AND status='PENDING'", (issue_no,)).fetchall()
    count = 0

    def get_pool(conn, run_id, size):
        row = conn.execute("SELECT numbers_json FROM prediction_pools WHERE run_id=? AND pool_size=?", (run_id, size)).fetchone()
        return json.loads(row["numbers_json"]) if row else []

    for run in runs:
        run_id = run["id"]
        mains = [r["number"] for r in conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='MAIN' ORDER BY rank", (run_id,)).fetchall()]
        special_row = conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='SPECIAL' LIMIT 1", (run_id,)).fetchone()
        special = special_row["number"] if special_row else None

        pool10 = get_pool(conn, run_id, 10) or mains
        pool14 = get_pool(conn, run_id, 14) or mains
        pool20 = get_pool(conn, run_id, 20) or mains

        hit_count = _pool_hit_count(mains, winning)
        hit_count_10 = _pool_hit_count(pool10, winning)
        hit_count_14 = _pool_hit_count(pool14, winning)
        hit_count_20 = _pool_hit_count(pool20, winning)
        special_hit = 1 if special == winning_special else 0

        conn.execute("""UPDATE prediction_runs SET status='REVIEWED', hit_count=?, hit_rate=?,
                        hit_count_10=?, hit_rate_10=?, hit_count_14=?, hit_rate_14=?,
                        hit_count_20=?, hit_rate_20=?, special_hit=?, reviewed_at=?
                        WHERE id=?""",
                     (hit_count, safe_div(hit_count, 6.0),
                      hit_count_10, safe_div(hit_count_10, 6.0),
                      hit_count_14, safe_div(hit_count_14, 6.0),
                      hit_count_20, safe_div(hit_count_20, 6.0),
                      special_hit, utc_now(), run_id))
        count += 1

    conn.commit()
    return count


# =========================================================
# 补齐特别号
# =========================================================

def backfill_missing_special_picks(conn):
    runs = conn.execute("SELECT id, strategy FROM prediction_runs WHERE status='PENDING'").fetchall()
    patched = 0
    for run in runs:
        run_id = run["id"]
        exists = conn.execute("SELECT 1 FROM prediction_picks WHERE run_id=? AND pick_type='SPECIAL'", (run_id,)).fetchone()
        if exists:
            continue
        mains = [r["number"] for r in conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='MAIN'", (run_id,)).fetchall()]
        draws = [json.loads(r["numbers_json"]) for r in conn.execute("SELECT numbers_json FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200").fetchall()]
        specials = [r["special_number"] for r in conn.execute("SELECT special_number FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200").fetchall()]
        config_json = get_model_state(conn, MINED_CONFIG_KEY)
        mined_cfg = json.loads(config_json) if config_json else _default_mined_config()
        _, special_number, special_score, _ = generate_strategy(draws, run["strategy"], mined_cfg, conn, specials)
        if special_number in mains:
            for n in ALL_NUMBERS:
                if n not in mains:
                    special_number = n
                    break
        conn.execute("INSERT OR REPLACE INTO prediction_picks(run_id, pick_type, number, rank, score, reason) VALUES (?, 'SPECIAL', ?, 1, ?, '补齐')",
                     (run_id, special_number, special_score))
        patched += 1
    if patched:
        conn.commit()
    return patched


# =========================================================
# 自动调优
# =========================================================

def auto_tune_mined_config(conn, recent_runs=20):
    config_json = get_model_state(conn, MINED_CONFIG_KEY)
    cfg = json.loads(config_json) if config_json else _default_mined_config()

    rows = conn.execute("SELECT hit_count FROM prediction_runs WHERE strategy='pattern_mined_v1' AND status='REVIEWED' ORDER BY id DESC LIMIT ?", (recent_runs,)).fetchall()
    if len(rows) < 5:
        print("复盘数据不足，跳过调优")
        return cfg

    avg_hits = sum(safe_int(r["hit_count"]) for r in rows) / len(rows)
    print(f"近期规律挖掘平均命中: {avg_hits:.2f}")

    w_freq = safe_float(cfg.get("w_freq", 0.40))
    w_mom = safe_float(cfg.get("w_mom", 0.20))
    delta = 0.03

    if avg_hits < 1.8:
        w_freq = max(0.2, w_freq - delta)
        w_mom = min(0.5, w_mom + delta)
    elif avg_hits > 2.5:
        w_freq = min(0.5, w_freq + delta)
        w_mom = max(0.1, w_mom - delta)
    else:
        print("当前表现合理，不调整")
        return cfg

    w_omit = 1.0 - w_freq - w_mom
    if w_omit < 0:
        w_omit = 0.0
        total = w_freq + w_mom
        if total > 0:
            w_freq /= total
            w_mom /= total

    cfg["w_freq"] = round(w_freq, 4)
    cfg["w_omit"] = round(w_omit, 4)
    cfg["w_mom"] = round(w_mom, 4)

    set_model_state(conn, MINED_CONFIG_KEY, safe_json_dumps(cfg))
    print(f"已更新规律挖掘权重: freq={w_freq:.3f}, omit={w_omit:.3f}, mom={w_mom:.3f}")
    return cfg


# =========================================================
# 波色回测（原有）
# =========================================================

def backtest_colors(conn, recent_limit=12, window=10, method="weighted"):
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + window:
        return 0, 0, 0, 0, 0

    total = main_hit = second_hit = any_hit = max_miss = miss = 0
    start_idx = len(specials) - recent_limit

    for i in range(start_idx, len(specials)):
        train = specials[:i]
        actual = get_color(specials[i])
        main_color, second_color, _, _ = predict_color(train, window=window, method=method)
        if main_color == actual:
            main_hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)
        if second_color == actual:
            second_hit += 1
        if main_color == actual or second_color == actual:
            any_hit += 1
        total += 1

    return total, main_hit, second_hit, any_hit, max_miss


# =========================================================
# 动态波色窗口选择（新增优化）
# =========================================================

def predict_color_weighted(
    specials: List[int],
    window: int = 12
) -> Tuple[str, str, float, float]:
    """
    三模型概率加权融合：
    - 模型A: 改进加权得分 -> 归一化为概率
    - 模型B: 马尔可夫转移概率
    - 模型C: 简单频率比例
    最终概率 = 0.5*A + 0.25*B + 0.25*C
    """
    if not specials:
        return "绿", "红", 0.0, 0.0

    recent = specials[-window:]

    # ---- 模型A: 改进加权得分 ----
    scores_a = defaultdict(float)
    total_w = 0.0
    for i, num in enumerate(reversed(recent)):
        w = (window - i) ** 1.5
        color = get_color(num)
        scores_a[color] += w
        total_w += w
        if i > 0:
            prev_idx = len(recent) - i
            if 0 <= prev_idx < len(recent):
                if color == get_color(recent[prev_idx]):
                    scores_a[color] += w * 0.3

    # 长期缺失补偿
    miss_map = {"红":0,"蓝":0,"绿":0}
    for idx, n in enumerate(reversed(recent)):
        c = get_color(n)
        if miss_map[c] == 0: miss_map[c] = idx+1
    for c in miss_map:
        if miss_map[c] >= 5:
            scores_a[c] += miss_map[c] * 0.7

    # 极热压制
    for c in ["红","蓝","绿"]:
        ratio = safe_div(scores_a[c], total_w)
        if ratio > 0.75:
            for other in ["红","蓝","绿"]:
                if other != c:
                    scores_a[other] += total_w * 0.1

    # 将模型A得分转化为概率（softmax）
    max_score = max(scores_a.values())
    exp_scores = {c: math.exp(scores_a[c] - max_score) for c in scores_a}
    sum_exp = sum(exp_scores.values())
    prob_a = {c: exp_scores[c]/sum_exp for c in exp_scores}

    # ---- 模型B: 马尔可夫转移概率 ----
    _, prob_b_dict = predict_color_markov(specials)
    if not prob_b_dict:
        prob_b_dict = {"红":1/3, "蓝":1/3, "绿":1/3}

    # ---- 模型C: 简单频率比例 ----
    freq = Counter(get_color(n) for n in recent)
    total_freq = len(recent)
    prob_c = {
        "红": freq["红"]/total_freq,
        "蓝": freq["蓝"]/total_freq,
        "绿": freq["绿"]/total_freq
    }

    # ---- 加权融合概率 ----
    final_prob = {}
    for c in ["红","蓝","绿"]:
        final_prob[c] = (prob_a[c] * 0.5 +
                         prob_b_dict.get(c, 1/3) * 0.25 +
                         prob_c[c] * 0.25)

    # 按融合概率排序
    sorted_final = sorted(final_prob.items(), key=lambda x: x[1], reverse=True)
    main_color = sorted_final[0][0]
    second_color = sorted_final[1][0]

    # 主强得分用融合概率（0~1之间）
    main_score = final_prob[main_color]
    second_score = final_prob[second_color]

    return main_color, second_color, main_score, second_score


# =========================================================
# 展示（加入 LightGBM 回测开关 + 动态波色窗口 + 集成纠错）
# =========================================================

def print_dashboard(conn, color_window=10, color_method="weighted", show_lgbm_backtest=False):
    backfill_missing_special_picks(conn)

    latest = conn.execute("SELECT * FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if latest:
        nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
        print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")

    pending = conn.execute("SELECT id, issue_no, strategy FROM prediction_runs WHERE status='PENDING' ORDER BY strategy").fetchall()
    if pending:
        print(f"\n预测期号: {pending[0]['issue_no']}")
        for r in pending:
            mains = [str(x["number"]).zfill(2) for x in conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='MAIN' ORDER BY rank", (r["id"],)).fetchall()]
            special_row = conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='SPECIAL' LIMIT 1", (r["id"],)).fetchone()
            special = str(special_row["number"]).zfill(2) if special_row else "--"
            label = STRATEGY_LABELS.get(r["strategy"], r["strategy"])
            print(f"  {label:<8s}: {' '.join(mains)} + {special}")
            if special_row:
                attrs = special_attributes(special_row["number"])
                print(f"         特码属性: {attrs['单双']}/{attrs['大小']} 合{attrs['合单双']}/{attrs['合大小']} 尾{attrs['尾大小']} {attrs['色波']} {attrs['五行']}")

    all_specials = [r["special_number"] for r in conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()]
    if len(all_specials) >= max(color_window, 10):
        # 动态选择最佳窗口
        dyn_window = best_color_window(conn, all_specials, color_method)
        print(f"\n🎨 特码波色预测（自适应窗口：{dyn_window} 期，{ '改进加权' if color_method=='weighted' else '简单频率' }）:")

        # 检查近期命中率，若过低则自动切换为集成投票模式（内部已包含集成逻辑）
        _, mh, _, ah, _ = backtest_colors(conn, recent_limit=8, window=dyn_window, method=color_method)
        if mh/8 < 0.25 and color_method == "weighted":
            print("⚠️ 近期加权模型主强命中率低于25%，自动切换为三模型集成投票")
            main_color, second_color, main_score, second_score = predict_color_weighted(all_specials, dyn_window)
        else:
            main_color, second_color, main_score, second_score = predict_color(all_specials, dyn_window, color_method)

        print(f"   主强: {main_color} (得分 {main_score:.3f})   次强: {second_color} (得分 {second_score:.3f})")

        total, main_hit, second_hit, any_hit, max_miss = backtest_colors(conn, recent_limit=10, window=dyn_window, method=color_method)
        if total > 0:
            print(f"\n📊 历史回测（最近 {total} 期，窗口{dyn_window}）：")
            print(f"   主强命中率: {main_hit}/{total} ({main_hit / total * 100:.1f}%)")
            print(f"   二中一命中率: {any_hit}/{total} ({any_hit / total * 100:.1f}%)")
            print(f"   最大连错: {max_miss}期")

        main_bs, _, bs_score, _ = predict_big_small_weighted(all_specials, color_window)
        total_bs, hit_bs = backtest_big_small(conn, 10, color_window)
        print(f"\n📏 特码大小预测（基于最近 {color_window} 期）： 主强 {main_bs} ({bs_score:.3f})")
        if total_bs > 0:
            print(f"   回测（最近10期）：主强命中率 {hit_bs}/{total_bs} ({hit_bs/total_bs*100:.1f}%)")

        main_oe, _, oe_score, _ = predict_odd_even_weighted(all_specials, color_window)
        total_oe, hit_oe = backtest_odd_even(conn, 10, color_window)
        print(f"\n🔢 特码单双预测（基于最近 {color_window} 期）： 主强 {main_oe} ({oe_score:.3f})")
        if total_oe > 0:
            print(f"   回测（最近10期）：主强命中率 {hit_oe}/{total_oe} ({hit_oe/total_oe*100:.1f}%)")

        final_attr = predict_final_attribute(all_specials, color_window)
        print("\n🔥 AI融合终极预测：")
        print(f"   波色: {final_attr['波色'][0]}")
        print(f"   大小: {final_attr['大小'][0]}")
        print(f"   单双: {final_attr['单双'][0]}")
        print(f"   综合置信度: {final_attr['综合置信度']:.3f}")

    else:
        print("\n特码数据不足，无法预测波色、大小和单双")

    # LightGBM 滑动回测（可选）
    if show_lgbm_backtest:
        draws_asc = [json.loads(r["numbers_json"]) for r in conn.execute("SELECT numbers_json FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()]
        specials_asc = [r["special_number"] for r in conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()]
        if len(draws_asc) >= 150:
            print("\n🤖 LightGBM 滑动回测 (最近100期测试):")
            res = walk_forward_backtest_lgbm(draws_asc, specials_asc, start_train_size=max(50, len(draws_asc)-100))
            print(f"   总测试期数: {res['total']}")
            print(f"   平均命中个数: {res['avg_hit']:.2f}")
            print(f"   Top10命中个数: {res['top10_avg_hit']:.2f}")
            print(f"   特别号命中率: {res['special_hit_rate']*100:.1f}%")
        else:
            print("\n数据不足，跳过LightGBM回测")

    stats = conn.execute("""
        SELECT strategy, COUNT(*) AS cnt, ROUND(AVG(hit_count), 2) AS avg_hit,
               ROUND(AVG(hit_rate) * 100, 1) AS hit_rate_pct,
               ROUND(AVG(COALESCE(special_hit, 0)) * 100, 1) AS special_rate_pct
        FROM prediction_runs WHERE status='REVIEWED'
        GROUP BY strategy ORDER BY avg_hit DESC
    """).fetchall()
    if stats:
        print("\n历史命中统计:")
        for s in stats:
            label = STRATEGY_LABELS.get(s["strategy"], s["strategy"])
            print(f"  {label:<8s}: 期数={s['cnt']}, 平均命中={s['avg_hit']}个, 命中率={s['hit_rate_pct']}%, 特别号命中率={s['special_rate_pct']}%")
    else:
        print("\n暂无复盘数据")


# =========================================================
# 命令
# =========================================================

def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source_label, used_url = fetch_online_records_with_multi_fallback(args.official_url, THIRD_PARTY_URLS_DEFAULT)
        total, ins, upd = sync_from_records(conn, records, source_label)
        print(f"数据同步完成: total={total}, new={ins}, updated={upd}, source={source_label} ({used_url})")

        latest_row = conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
        if latest_row:
            review_issue(conn, latest_row["issue_no"])

        if args.with_backtest:
            recent = [r["issue_no"] for r in conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC LIMIT 60").fetchall()]  # 扩大为60期
            for issue in recent:
                review_issue(conn, issue)

        if args.auto_tune:
            auto_tune_mined_config(conn)

        issue = generate_predictions(conn)
        print(f"已生成 {issue} 期预测")
        print_dashboard(conn, color_window=args.color_window, color_method=args.color_method, show_lgbm_backtest=args.lgbm_backtest)

    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()


def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn, color_window=args.color_window, color_method=args.color_method, show_lgbm_backtest=args.lgbm_backtest)
    finally:
        conn.close()


# =========================================================
# main
# =========================================================

def main():
    p = argparse.ArgumentParser(description="新澳门六合彩预测工具")
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--official-url", default=OFFICIAL_URL_DEFAULT)
    p.add_argument("--color-window", type=int, default=10, help="波色预测窗口大小")
    p.add_argument("--color-method", choices=["simple", "weighted"], default="weighted", help="波色预测方法")
    p.add_argument("--lgbm-backtest", action="store_true", help="显示LightGBM滑动回测结果")

    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--with-backtest", action="store_true")
    sp.add_argument("--auto-tune", action="store_true")
    sp.set_defaults(func=cmd_sync)

    show_parser = sub.add_parser("show")
    show_parser.set_defaults(func=cmd_show)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
