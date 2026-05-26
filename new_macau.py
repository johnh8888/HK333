#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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
}

STRATEGY_IDS = [
    "balanced_v1",
    "hot_v1",
    "cold_rebound_v1",
    "momentum_v1",
    "ensemble_v2",
    "pattern_mined_v1",
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
# 波色预测
# =========================================================

def predict_color_weighted(
    specials: List[int],
    window: int = 10
) -> Tuple[str, str, float, float]:

    if not specials:
        return "绿", "红", 0.0, 0.0

    recent = specials[-window:]

    scores = defaultdict(float)

    total_weight = 0.0

    for i, num in enumerate(reversed(recent)):
        weight = (window - i) ** 1.4

        color = get_color(num)

        scores[color] += weight

        if i > 0:
            prev_index = len(recent) - i

            if 0 <= prev_index < len(recent):
                if color == get_color(recent[prev_index]):
                    scores[color] += weight * 0.35

        total_weight += weight

    if total_weight <= 0:
        return "绿", "红", 0.0, 0.0

    sorted_colors = sorted(
        scores.items(),
        key=lambda x: (-x[1], x[0])
    )

    main_color = sorted_colors[0][0]
    main_score = safe_div(sorted_colors[0][1], total_weight)

    if len(sorted_colors) > 1:
        second_color = sorted_colors[1][0]
        second_score = safe_div(sorted_colors[1][1], total_weight)
    else:
        second_color = "绿"
        second_score = 0.0

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


def _freq_map(draws):
    freq = {n: 0.0 for n in ALL_NUMBERS}

    for draw in draws:
        for n in draw:
            freq[n] += 1.0

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


# =========================================================
# 选号
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

        odd_count = sum(
            1 for x in proposal
            if x % 2 == 1
        )

        if len(proposal) >= 4:
            if odd_count == 0:
                continue

            if odd_count == len(proposal):
                continue

        zone_counts = {}

        for x in proposal:
            z = min(4, (x - 1) // 10)

            zone_counts[z] = (
                zone_counts.get(z, 0) + 1
            )

        if any(c >= 4 for c in zone_counts.values()):
            continue

        picked.append((n, s))

    while len(picked) < 6:
        for n, s in ranked:
            if n not in [pn for pn, _ in picked]:
                picked.append((n, s))
                break

    return [
        (
            n,
            idx + 1,
            s,
            f"{reason} score={s:.4f}"
        )
        for idx, (n, s) in enumerate(picked)
    ]


# =========================================================
# 默认参数
# =========================================================

def _default_mined_config():
    return {
        "window": 80.0,
        "w_freq": 0.40,
        "w_omit": 0.30,
        "w_mom": 0.20,
        "w_pair": 0.05,
        "w_zone": 0.05,
        "special_bonus": 0.10,
    }


# =========================================================
# 权重策略
# =========================================================

def _apply_weight_config(draws, config, reason):

    window_size = int(config.get("window", 80))

    window = draws[:max(20, window_size)]

    freq = _normalize(_freq_map(window))

    omission = _normalize(_omission_map(window))

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
    w_mom = safe_float(config.get("w_mom", 0.20))
    w_pair = safe_float(config.get("w_pair", 0.00))
    w_zone = safe_float(config.get("w_zone", 0.00))

    scores = {}

    for n in ALL_NUMBERS:
        scores[n] = (
            freq[n] * w_freq
            + omission[n] * w_omit
            + momentum[n] * w_mom
            + pair[n] * w_pair
            + zone[n] * w_zone
        )

    main_picks = _pick_top_six(scores, reason)

    main_set = {n for n, _, _, _ in main_picks}

    candidates = [
        (n, s)
        for n, s in sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        if n not in main_set
    ]

    if not candidates:
        candidates = sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

    special_number, special_score = candidates[0]

    return (
        main_picks,
        special_number,
        special_score,
        scores
    )


# =========================================================
# 策略
# =========================================================

def _ensemble_strategy(draws, mined_cfg=None):

    m_hot = _apply_weight_config(
        draws,
        {
            "window": 80,
            "w_freq": 0.8,
            "w_omit": 0.0,
            "w_mom": 0.2,
            "w_pair": 0.0,
            "w_zone": 0.0
        },
        "热号"
    )

    m_cold = _apply_weight_config(
        draws,
        {
            "window": 80,
            "w_freq": 0.0,
            "w_omit": 0.7,
            "w_mom": 0.3,
            "w_pair": 0.0,
            "w_zone": 0.0
        },
        "冷号"
    )

    m_mom = _apply_weight_config(
        draws,
        {
            "window": 80,
            "w_freq": 0.1,
            "w_omit": 0.0,
            "w_mom": 0.9,
            "w_pair": 0.0,
            "w_zone": 0.0
        },
        "动量"
    )

    m_bal = _apply_weight_config(
        draws,
        {
            "window": 80,
            "w_freq": 0.4,
            "w_omit": 0.3,
            "w_mom": 0.2,
            "w_pair": 0.05,
            "w_zone": 0.05
        },
        "平衡"
    )

    m_mined = _apply_weight_config(
        draws,
        mined_cfg or _default_mined_config(),
        "规律挖掘"
    )

    score_maps = [
        m_hot[3],
        m_cold[3],
        m_mom[3],
        m_bal[3],
        m_mined[3],
    ]

    votes = {
        n: 0.0
        for n in ALL_NUMBERS
    }

    for m in score_maps:
        ranked = sorted(
            m.items(),
            key=lambda x: x[1],
            reverse=True
        )

        for rank, (n, _) in enumerate(ranked):
            votes[n] += float(49 - rank)

    voted = _normalize(votes)

    picked = _pick_top_six(voted, "集成投票")

    main_set = {n for n, _, _, _ in picked}

    candidates = [
        (n, s)
        for n, s in sorted(
            voted.items(),
            key=lambda x: x[1],
            reverse=True
        )
        if n not in main_set
    ]

    if not candidates:
        candidates = sorted(
            voted.items(),
            key=lambda x: x[1],
            reverse=True
        )

    special_number, special_score = candidates[0]

    return picked, special_number, special_score, voted


def generate_strategy(draws, strategy, mined_config=None):

    if strategy == "hot_v1":
        return _apply_weight_config(
            draws,
            {
                "window": 80,
                "w_freq": 0.8,
                "w_omit": 0.0,
                "w_mom": 0.2,
                "w_pair": 0.0,
                "w_zone": 0.0
            },
            "热号"
        )

    if strategy == "cold_rebound_v1":
        return _apply_weight_config(
            draws,
            {
                "window": 80,
                "w_freq": 0.0,
                "w_omit": 0.7,
                "w_mom": 0.3,
                "w_pair": 0.0,
                "w_zone": 0.0
            },
            "冷号"
        )

    if strategy == "momentum_v1":
        return _apply_weight_config(
            draws,
            {
                "window": 80,
                "w_freq": 0.1,
                "w_omit": 0.0,
                "w_mom": 0.9,
                "w_pair": 0.0,
                "w_zone": 0.0
            },
            "动量"
        )

    if strategy == "ensemble_v2":
        return _ensemble_strategy(draws, mined_config)

    if strategy == "pattern_mined_v1":
        cfg = mined_config or _default_mined_config()

        return _apply_weight_config(
            draws,
            cfg,
            "规律挖掘"
        )

    return _apply_weight_config(
        draws,
        {
            "window": 80,
            "w_freq": 0.4,
            "w_omit": 0.3,
            "w_mom": 0.2,
            "w_pair": 0.05,
            "w_zone": 0.05
        },
        "平衡"
    )


# =========================================================
# 候选池
# =========================================================

def _build_candidate_pools(scores, main6):

    ranked = [
        n for n, _ in sorted(
            scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
    ]

    rest = [
        n for n in ranked
        if n not in main6
    ]

    pool10 = main6 + rest[:max(0, 10 - len(main6))]
    pool14 = main6 + rest[:max(0, 14 - len(main6))]
    pool20 = main6 + rest[:max(0, 20 - len(main6))]

    return {
        6: main6,
        10: pool10,
        14: pool14,
        20: pool20
    }


def _pool_hit_count(pool, winning):
    return len([
        n for n in pool
        if n in winning
    ])


def _save_prediction_pools(conn, run_id, pools):

    conn.execute(
        "DELETE FROM prediction_pools WHERE run_id=?",
        (run_id,)
    )

    now = utc_now()

    for size, nums in pools.items():
        conn.execute("""
            INSERT INTO prediction_pools(
                run_id,
                pool_size,
                numbers_json,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            run_id,
            size,
            safe_json_dumps(nums),
            now
        ))


# =========================================================
# 预测生成
# =========================================================

def generate_predictions(conn, issue_no=None):

    row = conn.execute("""
        SELECT issue_no
        FROM draws
        ORDER BY draw_date DESC, issue_no DESC
        LIMIT 1
    """).fetchone()

    if not row:
        raise RuntimeError("No draws in database")

    target_issue = issue_no or next_issue(row["issue_no"])

    draws = [
        json.loads(r["numbers_json"])
        for r in conn.execute("""
            SELECT numbers_json
            FROM draws
            ORDER BY draw_date DESC, issue_no DESC
            LIMIT 200
        """).fetchall()
    ]

    if len(draws) < 20:
        raise RuntimeError("Need at least 20 draws")

    config_json = get_model_state(
        conn,
        MINED_CONFIG_KEY
    )

    mined_cfg = (
        json.loads(config_json)
        if config_json
        else _default_mined_config()
    )

    for strategy in STRATEGY_IDS:

        now = utc_now()

        conn.execute("""
            INSERT OR REPLACE INTO prediction_runs(
                issue_no,
                strategy,
                status,
                created_at
            )
            VALUES (?, ?, 'PENDING', ?)
        """, (
            target_issue,
            strategy,
            now
        ))

        run_id = conn.execute("""
            SELECT id
            FROM prediction_runs
            WHERE issue_no=? AND strategy=?
        """, (
            target_issue,
            strategy
        )).fetchone()["id"]

        picks, special_number, special_score, scores = (
            generate_strategy(
                draws,
                strategy,
                mined_cfg
            )
        )

        conn.execute(
            "DELETE FROM prediction_picks WHERE run_id=?",
            (run_id,)
        )

        conn.executemany("""
            INSERT OR REPLACE INTO prediction_picks(
                run_id,
                pick_type,
                number,
                rank,
                score,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (
                run_id,
                "MAIN",
                n,
                rank,
                score,
                reason
            )
            for n, rank, score, reason in picks
        ] + [
            (
                run_id,
                "SPECIAL",
                special_number,
                1,
                special_score,
                "特别号"
            )
        ])

        main_numbers = [
            n for n, _, _, _ in picks
        ]

        pools = _build_candidate_pools(
            scores,
            main_numbers
        )

        _save_prediction_pools(
            conn,
            run_id,
            pools
        )

    conn.commit()

    return target_issue


# =========================================================
# 复盘
# =========================================================

def review_issue(conn, issue_no):

    draw = conn.execute("""
        SELECT numbers_json, special_number
        FROM draws
        WHERE issue_no=?
    """, (issue_no,)).fetchone()

    if not draw:
        return 0

    winning = set(json.loads(draw["numbers_json"]))

    winning_special = safe_int(draw["special_number"])

    runs = conn.execute("""
        SELECT id
        FROM prediction_runs
        WHERE issue_no=? AND status='PENDING'
    """, (issue_no,)).fetchall()

    count = 0

    def get_pool(conn, run_id, size):
        row = conn.execute("""
            SELECT numbers_json
            FROM prediction_pools
            WHERE run_id=? AND pool_size=?
        """, (run_id, size)).fetchone()

        return (
            json.loads(row["numbers_json"])
            if row else []
        )

    for run in runs:

        run_id = run["id"]

        mains = [
            r["number"]
            for r in conn.execute("""
                SELECT number
                FROM prediction_picks
                WHERE run_id=?
                AND pick_type='MAIN'
                ORDER BY rank
            """, (run_id,)).fetchall()
        ]

        special_row = conn.execute("""
            SELECT number
            FROM prediction_picks
            WHERE run_id=?
            AND pick_type='SPECIAL'
            LIMIT 1
        """, (run_id,)).fetchone()

        special = (
            special_row["number"]
            if special_row else None
        )

        pool10 = get_pool(conn, run_id, 10) or mains
        pool14 = get_pool(conn, run_id, 14) or mains
        pool20 = get_pool(conn, run_id, 20) or mains

        hit_count = _pool_hit_count(mains, winning)
        hit_count_10 = _pool_hit_count(pool10, winning)
        hit_count_14 = _pool_hit_count(pool14, winning)
        hit_count_20 = _pool_hit_count(pool20, winning)

        special_hit = (
            1 if special == winning_special else 0
        )

        conn.execute("""
            UPDATE prediction_runs
            SET
                status='REVIEWED',
                hit_count=?,
                hit_rate=?,
                hit_count_10=?,
                hit_rate_10=?,
                hit_count_14=?,
                hit_rate_14=?,
                hit_count_20=?,
                hit_rate_20=?,
                special_hit=?,
                reviewed_at=?
            WHERE id=?
        """, (
            hit_count,
            safe_div(hit_count, 6.0),

            hit_count_10,
            safe_div(hit_count_10, 6.0),

            hit_count_14,
            safe_div(hit_count_14, 6.0),

            hit_count_20,
            safe_div(hit_count_20, 6.0),

            special_hit,
            utc_now(),
            run_id
        ))

        count += 1

    conn.commit()

    return count


# =========================================================
# 补齐特别号
# =========================================================

def backfill_missing_special_picks(conn):

    runs = conn.execute("""
        SELECT id, strategy
        FROM prediction_runs
        WHERE status='PENDING'
    """).fetchall()

    patched = 0

    for run in runs:

        run_id = run["id"]

        exists = conn.execute("""
            SELECT 1
            FROM prediction_picks
            WHERE run_id=?
            AND pick_type='SPECIAL'
        """, (run_id,)).fetchone()

        if exists:
            continue

        mains = [
            r["number"]
            for r in conn.execute("""
                SELECT number
                FROM prediction_picks
                WHERE run_id=?
                AND pick_type='MAIN'
            """, (run_id,)).fetchall()
        ]

        draws = [
            json.loads(r["numbers_json"])
            for r in conn.execute("""
                SELECT numbers_json
                FROM draws
                ORDER BY draw_date DESC, issue_no DESC
                LIMIT 200
            """).fetchall()
        ]

        config_json = get_model_state(
            conn,
            MINED_CONFIG_KEY
        )

        mined_cfg = (
            json.loads(config_json)
            if config_json
            else _default_mined_config()
        )

        _, special_number, special_score, _ = (
            generate_strategy(
                draws,
                run["strategy"],
                mined_cfg
            )
        )

        if special_number in mains:
            for n in ALL_NUMBERS:
                if n not in mains:
                    special_number = n
                    break

        conn.execute("""
            INSERT OR REPLACE INTO prediction_picks(
                run_id,
                pick_type,
                number,
                rank,
                score,
                reason
            )
            VALUES (?, 'SPECIAL', ?, 1, ?, '补齐')
        """, (
            run_id,
            special_number,
            special_score
        ))

        patched += 1

    if patched:
        conn.commit()

    return patched


# =========================================================
# 自动调优
# =========================================================

def auto_tune_mined_config(conn, recent_runs=20):

    config_json = get_model_state(
        conn,
        MINED_CONFIG_KEY
    )

    cfg = (
        json.loads(config_json)
        if config_json
        else _default_mined_config()
    )

    rows = conn.execute("""
        SELECT hit_count
        FROM prediction_runs
        WHERE strategy='pattern_mined_v1'
        AND status='REVIEWED'
        ORDER BY id DESC
        LIMIT ?
    """, (recent_runs,)).fetchall()

    if len(rows) < 5:
        print("复盘数据不足，跳过调优")
        return cfg

    avg_hits = sum(
        safe_int(r["hit_count"])
        for r in rows
    ) / len(rows)

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

    set_model_state(
        conn,
        MINED_CONFIG_KEY,
        safe_json_dumps(cfg)
    )

    print(
        "已更新规律挖掘权重: "
        f"freq={w_freq:.3f}, "
        f"omit={w_omit:.3f}, "
        f"mom={w_mom:.3f}"
    )

    return cfg


# =========================================================
# 波色回测
# =========================================================

def backtest_colors(
    conn,
    recent_limit=12,
    window=10,
    method="weighted"
):

    rows = conn.execute("""
        SELECT special_number
        FROM draws
        ORDER BY draw_date ASC, issue_no ASC
    """).fetchall()

    specials = [
        r["special_number"]
        for r in rows
    ]

    if len(specials) < recent_limit + window:
        return 0, 0, 0, 0, 0

    total = 0
    main_hit = 0
    second_hit = 0
    any_hit = 0

    max_miss = 0
    miss = 0

    start_idx = len(specials) - recent_limit

    for i in range(start_idx, len(specials)):

        train = specials[:i]

        actual = get_color(specials[i])

        main_color, second_color, _, _ = predict_color(
            train,
            window=window,
            method=method
        )

        if main_color == actual:
            main_hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)

        if second_color == actual:
            second_hit += 1

        if (
            main_color == actual
            or second_color == actual
        ):
            any_hit += 1

        total += 1

    return (
        total,
        main_hit,
        second_hit,
        any_hit,
        max_miss
    )


# =========================================================
# 展示
# =========================================================

def print_dashboard(
    conn,
    color_window=10,
    color_method="weighted"
):

    backfill_missing_special_picks(conn)

    latest = conn.execute("""
        SELECT *
        FROM draws
        ORDER BY draw_date DESC, issue_no DESC
        LIMIT 1
    """).fetchone()

    if latest:
        nums = " ".join(
            f"{n:02d}"
            for n in json.loads(latest["numbers_json"])
        )

        print(
            f"最新开奖: {latest['issue_no']} | "
            f"{nums} + "
            f"{latest['special_number']:02d}"
        )

    pending = conn.execute("""
        SELECT id, issue_no, strategy
        FROM prediction_runs
        WHERE status='PENDING'
        ORDER BY strategy
    """).fetchall()

    if pending:

        print(f"\n预测期号: {pending[0]['issue_no']}")

        for r in pending:

            mains = [
                str(x["number"]).zfill(2)
                for x in conn.execute("""
                    SELECT number
                    FROM prediction_picks
                    WHERE run_id=?
                    AND pick_type='MAIN'
                    ORDER BY rank
                """, (r["id"],)).fetchall()
            ]

            special_row = conn.execute("""
                SELECT number
                FROM prediction_picks
                WHERE run_id=?
                AND pick_type='SPECIAL'
                LIMIT 1
            """, (r["id"],)).fetchone()

            special = (
                str(special_row["number"]).zfill(2)
                if special_row else "--"
            )

            label = STRATEGY_LABELS.get(
                r["strategy"],
                r["strategy"]
            )

            print(
                f"  {label:<8s}: "
                f"{' '.join(mains)} + {special}"
            )

            if special_row:
                attrs = special_attributes(
                    special_row["number"]
                )

                print(
                    "         特码属性: "
                    f"{attrs['单双']}/"
                    f"{attrs['大小']} "
                    f"合{attrs['合单双']}/"
                    f"{attrs['合大小']} "
                    f"尾{attrs['尾大小']} "
                    f"{attrs['色波']} "
                    f"{attrs['五行']}"
                )

    all_specials = [
        r["special_number"]
        for r in conn.execute("""
            SELECT special_number
            FROM draws
            ORDER BY draw_date ASC, issue_no ASC
        """).fetchall()
    ]

    if len(all_specials) >= max(color_window, 10):

        main_color, second_color, main_score, second_score = (
            predict_color(
                all_specials,
                window=color_window,
                method=color_method
            )
        )

        method_name = (
            "改进加权（指数衰减+连出奖励）"
            if color_method == "weighted"
            else "简单频率"
        )

        print(
            f"\n🎨 特码波色预测（{method_name}，"
            f"基于最近 {color_window} 期）："
        )

        print(
            f"   主强: {main_color} "
            f"(得分 {main_score:.3f})   "
            f"次强: {second_color} "
            f"(得分 {second_score:.3f})"
        )

        total, main_hit, second_hit, any_hit, max_miss = (
            backtest_colors(
                conn,
                recent_limit=12,
                window=color_window,
                method=color_method
            )
        )

        if total > 0:

            print(f"\n📊 历史回测（最近 {total} 期）：")

            print(
                f"   主强命中率: "
                f"{main_hit}/{total} "
                f"({main_hit / total * 100:.1f}%)"
            )

            print(
                f"   二中一命中率: "
                f"{any_hit}/{total} "
                f"({any_hit / total * 100:.1f}%)"
            )

            print(f"   最大连错: {max_miss}期")

        else:
            print("\n波色回测数据不足")

    else:
        print("\n特码数据不足，无法预测波色")

    stats = conn.execute("""
        SELECT
            strategy,
            COUNT(*) AS cnt,
            ROUND(AVG(hit_count), 2) AS avg_hit,
            ROUND(AVG(hit_rate) * 100, 1) AS hit_rate_pct,
            ROUND(AVG(COALESCE(special_hit, 0)) * 100, 1)
                AS special_rate_pct
        FROM prediction_runs
        WHERE status='REVIEWED'
        GROUP BY strategy
        ORDER BY avg_hit DESC
    """).fetchall()

    if stats:

        print("\n历史命中统计:")

        for s in stats:

            label = STRATEGY_LABELS.get(
                s["strategy"],
                s["strategy"]
            )

            print(
                f"  {label:<8s}: "
                f"期数={s['cnt']}, "
                f"平均命中={s['avg_hit']}个, "
                f"命中率={s['hit_rate_pct']}%, "
                f"特别号命中率={s['special_rate_pct']}%"
            )

    else:
        print("\n暂无复盘数据")


# =========================================================
# 命令
# =========================================================

def cmd_sync(args):

    conn = connect_db(args.db)

    try:
        init_db(conn)

        records, source_label, used_url = (
            fetch_online_records_with_multi_fallback(
                args.official_url,
                THIRD_PARTY_URLS_DEFAULT
            )
        )

        total, ins, upd = sync_from_records(
            conn,
            records,
            source_label
        )

        print(
            "数据同步完成: "
            f"total={total}, "
            f"new={ins}, "
            f"updated={upd}, "
            f"source={source_label} "
            f"({used_url})"
        )

        latest_row = conn.execute("""
            SELECT issue_no
            FROM draws
            ORDER BY draw_date DESC, issue_no DESC
            LIMIT 1
        """).fetchone()

        if latest_row:
            latest_issue = latest_row["issue_no"]

            review_issue(conn, latest_issue)

        if args.with_backtest:

            recent = [
                r["issue_no"]
                for r in conn.execute("""
                    SELECT issue_no
                    FROM draws
                    ORDER BY draw_date DESC
                    LIMIT 30
                """).fetchall()
            ]

            for issue in recent:
                review_issue(conn, issue)

        if args.auto_tune:
            auto_tune_mined_config(conn)

        issue = generate_predictions(conn)

        print(f"已生成 {issue} 期预测")

        print_dashboard(
            conn,
            color_window=args.color_window,
            color_method=args.color_method
        )

    except Exception as e:
        print(f"错误: {e}")

    finally:
        conn.close()


def cmd_show(args):

    conn = connect_db(args.db)

    try:
        print_dashboard(
            conn,
            color_window=args.color_window,
            color_method=args.color_method
        )

    finally:
        conn.close()


# =========================================================
# main
# =========================================================

def main():

    p = argparse.ArgumentParser(
        description="新澳门六合彩预测工具"
    )

    p.add_argument(
        "--db",
        default=DB_PATH_DEFAULT
    )

    p.add_argument(
        "--official-url",
        default=OFFICIAL_URL_DEFAULT
    )

    p.add_argument(
        "--color-window",
        type=int,
        default=10,
        help="波色预测窗口大小"
    )

    p.add_argument(
        "--color-method",
        choices=["simple", "weighted"],
        default="weighted",
        help="波色预测方法"
    )

    sub = p.add_subparsers(
        dest="cmd",
        required=True
    )

    sp = sub.add_parser("sync")

    sp.add_argument(
        "--with-backtest",
        action="store_true"
    )

    sp.add_argument(
        "--auto-tune",
        action="store_true"
    )

    sp.set_defaults(func=cmd_sync)

    show_parser = sub.add_parser("show")

    show_parser.set_defaults(func=cmd_show)

    args = p.parse_args()

    args.func(args)


if __name__ == "__main__":
    main()