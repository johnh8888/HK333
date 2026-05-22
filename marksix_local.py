#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "marksix_local.db")
CSV_PATH_DEFAULT = str(SCRIPT_DIR / "Mark_Six.csv")  # 保留但不再自动使用
OFFICIAL_URL_DEFAULT = "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
THIRD_PARTY_MAX_PAGES_DEFAULT = 5
THIRD_PARTY_URLS_DEFAULT: List[str] = [
    "https://marksix6.net/index.php?api=1",
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
STRATEGY_IDS = ["balanced_v1", "hot_v1", "cold_rebound_v1", "momentum_v1", "ensemble_v2", "pattern_mined_v1"]


@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
            UNIQUE(run_id, number),
            FOREIGN KEY(run_id) REFERENCES prediction_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS prediction_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            pool_size INTEGER NOT NULL,
            numbers_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, pool_size),
            FOREIGN KEY(run_id) REFERENCES prediction_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS model_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _ensure_migrations(conn)
    conn.commit()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _ensure_migrations(conn: sqlite3.Connection) -> None:
    if not _column_exists(conn, "prediction_picks", "pick_type"):
        conn.execute("ALTER TABLE prediction_picks ADD COLUMN pick_type TEXT NOT NULL DEFAULT 'MAIN'")
    if not _column_exists(conn, "prediction_runs", "special_hit"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN special_hit INTEGER")
    if not _column_exists(conn, "prediction_runs", "hit_count_10"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_count_10 INTEGER")
    if not _column_exists(conn, "prediction_runs", "hit_rate_10"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_rate_10 REAL")
    if not _column_exists(conn, "prediction_runs", "hit_count_14"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_count_14 INTEGER")
    if not _column_exists(conn, "prediction_runs", "hit_rate_14"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_rate_14 REAL")
    if not _column_exists(conn, "prediction_runs", "hit_count_20"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_count_20 INTEGER")
    if not _column_exists(conn, "prediction_runs", "hit_rate_20"):
        conn.execute("ALTER TABLE prediction_runs ADD COLUMN hit_rate_20 REAL")


def get_model_state(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM model_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_model_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO model_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now),
    )


# ================== 多源数据获取 ==================
def _parse_marksix6_response(payload: dict) -> List[DrawRecord]:
    records = []
    hk_data = None
    for lottery in payload.get("lottery_data", []):
        if lottery.get("name") == "香港彩":
            hk_data = lottery
            break
    if not hk_data:
        return records

    latest_open_time_str = hk_data.get("openTime", "")
    try:
        latest_open_time = datetime.strptime(latest_open_time_str, "%Y-%m-%d %H:%M:%S")
    except:
        latest_open_time = datetime.now()

    for idx, item in enumerate(hk_data.get("history", [])):
        try:
            parts = item.split("期：")
            if len(parts) != 2:
                continue
            issue_no = parts[0].strip()
            all_numbers = [int(n.strip()) for n in parts[1].split(",")]
            if len(all_numbers) != 7:
                continue
            numbers = all_numbers[:6]
            special = all_numbers[6]
            draw_date = (latest_open_time - timedelta(days=idx * 2)).strftime("%Y-%m-%d")
            records.append(DrawRecord(issue_no, draw_date, numbers, special))
        except:
            continue
    return records


def _parse_official_json(payload: list) -> List[DrawRecord]:
    records = []
    for item in payload:
        try:
            issue_no = str(item.get("drawNo") or item.get("issueNo"))
            draw_date = str(item.get("drawDate", ""))[:10]
            numbers = [int(item[f"no{i}"]) for i in range(1, 7)]
            special = int(item.get("specialNumber") or item.get("no7"))
            if issue_no and draw_date and len(numbers) == 6:
                records.append(DrawRecord(issue_no, draw_date, numbers, special))
        except:
            continue
    return records


def fetch_online_records_with_multi_fallback(
    official_url: str,
    third_party_urls: Sequence[str],
    third_party_max_pages: int = THIRD_PARTY_MAX_PAGES_DEFAULT,
) -> Tuple[List[DrawRecord], str, str]:
    """按优先级尝试在线数据源，返回记录列表、来源标签、实际使用的URL"""
    # 1. 官方
    if official_url.strip():
        try:
            req = Request(official_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8-sig"))
            records = _parse_official_json(payload)
            if records:
                return records, "official_api", official_url
        except Exception as e:
            print(f"官方源失败: {e}")

    # 2. 第三方（优先 marksix6）
    for url in third_party_urls:
        try:
            if "marksix6.net" in url:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                records = _parse_marksix6_response(payload)
                if records:
                    return records, "marksix6", url
            else:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8-sig"))
                records = _parse_official_json(payload)
                if records:
                    return records, f"third_party", url
        except Exception as e:
            print(f"第三方源 {url} 失败: {e}")

    raise RuntimeError("所有在线数据源均无法获取数据，请检查网络或配置。")


def upsert_draw(conn: sqlite3.Connection, record: DrawRecord, source: str) -> str:
    now = utc_now()
    existing = conn.execute("SELECT issue_no FROM draws WHERE issue_no = ?", (record.issue_no,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE draws SET draw_date=?, numbers_json=?, special_number=?, source=?, updated_at=? WHERE issue_no=?",
            (record.draw_date, json.dumps(record.numbers), record.special_number, source, now, record.issue_no),
        )
        return "updated"
    else:
        conn.execute(
            "INSERT INTO draws VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record.issue_no, record.draw_date, json.dumps(record.numbers), record.special_number, source, now, now),
        )
        return "inserted"


def sync_from_records(conn, records, source):
    ins = upd = 0
    for r in records:
        res = upsert_draw(conn, r, source)
        if res == "inserted":
            ins += 1
        else:
            upd += 1
    conn.commit()
    return len(records), ins, upd


def next_issue(issue_no: str) -> str:
    digits = ''.join(ch for ch in issue_no if ch.isdigit())
    if not digits:
        return issue_no
    num = int(digits) + 1
    if '/' in issue_no:
        parts = issue_no.rsplit('/', 1)
        return f"{parts[0]}/{num:0{len(digits)}d}"
    return f"{num:0{len(digits)}d}"


# ================== 核心预测逻辑 ==================
def _normalize(score_map: Dict[int, float]) -> Dict[int, float]:
    values = list(score_map.values())
    mn, mx = min(values), max(values)
    if mx == mn:
        return {k: 0.0 for k in score_map}
    return {k: (v - mn) / (mx - mn) for k, v in score_map.items()}


def _freq_map(draws: List[List[int]]) -> Dict[int, float]:
    freq = {n: 0.0 for n in ALL_NUMBERS}
    for draw in draws:
        for n in draw:
            freq[n] += 1.0
    return freq


def _omission_map(draws: List[List[int]]) -> Dict[int, float]:
    omission = {n: float(len(draws) + 1) for n in ALL_NUMBERS}
    for i, draw in enumerate(draws):
        for n in draw:
            omission[n] = min(omission[n], float(i + 1))
    return omission


def _momentum_map(draws: List[List[int]]) -> Dict[int, float]:
    m = {n: 0.0 for n in ALL_NUMBERS}
    for i, draw in enumerate(draws):
        w = 1.0 / (1.0 + i)
        for n in draw:
            m[n] += w
    return m


def _pair_affinity_map(draws: List[List[int]], window: int = 200) -> Dict[int, float]:
    pair_count: Dict[Tuple[int, int], int] = {}
    for draw in draws[:window]:
        s = sorted(draw)
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                key = (s[i], s[j])
                pair_count[key] = pair_count.get(key, 0) + 1
    social = {n: 0.0 for n in ALL_NUMBERS}
    for (a, b), c in pair_count.items():
        social[a] += float(c)
        social[b] += float(c)
    return social


def _zone_heat_map(draws: List[List[int]], window: int = 80) -> Dict[int, float]:
    zone_counts = [0.0] * 5
    w = draws[:window]
    if not w:
        return {n: 0.0 for n in ALL_NUMBERS}
    for draw in w:
        for n in draw:
            zone = min(4, (n - 1) // 10)
            zone_counts[zone] += 1.0
    expected = 6.0 * len(w) / 5.0
    zone_score = [expected - c for c in zone_counts]
    return {n: zone_score[min(4, (n - 1) // 10)] for n in ALL_NUMBERS}


def _pick_top_six(scores: Dict[int, float], reason: str) -> List[Tuple[int, int, float, str]]:
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    picked: List[Tuple[int, float]] = []
    for n, s in ranked:
        if len(picked) == 6:
            break
        proposal = [pn for pn, _ in picked] + [n]
        odd_count = sum(1 for x in proposal if x % 2 == 1)
        if len(proposal) >= 4 and (odd_count == 0 or odd_count == len(proposal)):
            continue
        zone_counts: Dict[int, int] = {}
        for x in proposal:
            z = min(4, (x - 1) // 10)
            zone_counts[z] = zone_counts.get(z, 0) + 1
        if any(c >= 4 for c in zone_counts.values()):
            continue
        picked.append((n, s))
    while len(picked) < 6:
        for n, s in ranked:
            if n not in [pn for pn, _ in picked]:
                picked.append((n, s))
                break
    return [(n, idx + 1, s, f"{reason} score={s:.4f}") for idx, (n, s) in enumerate(picked)]


def _default_mined_config() -> Dict[str, float]:
    return {"window": 80.0, "w_freq": 0.40, "w_omit": 0.30, "w_mom": 0.20, "w_pair": 0.05, "w_zone": 0.05, "special_bonus": 0.10}


def _apply_weight_config(draws, config, reason):
    window_size = int(config.get("window", 80))
    window = draws[:max(20, window_size)]
    freq = _normalize(_freq_map(window))
    omission = _normalize(_omission_map(window))
    momentum = _normalize(_momentum_map(window))
    pair = _normalize(_pair_affinity_map(window, window=min(200, len(window))))
    zone = _normalize(_zone_heat_map(window, window=min(80, len(window))))

    w_freq = float(config.get("w_freq", 0.45))
    w_omit = float(config.get("w_omit", 0.35))
    w_mom = float(config.get("w_mom", 0.20))
    w_pair = float(config.get("w_pair", 0.00))
    w_zone = float(config.get("w_zone", 0.00))

    scores = {}
    for n in ALL_NUMBERS:
        scores[n] = (freq[n] * w_freq + omission[n] * w_omit + momentum[n] * w_mom + pair[n] * w_pair + zone[n] * w_zone)
    main_picks = _pick_top_six(scores, reason)
    main_set = {n for n, _, _, _ in main_picks}
    candidates = [(n, s) for n, s in sorted(scores.items(), key=lambda x: x[1], reverse=True) if n not in main_set]
    if not candidates:
        candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    special_number, special_score = candidates[0]
    return main_picks, special_number, special_score, scores


def generate_strategy(draws, strategy, mined_config=None):
    if strategy == "hot_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.8, "w_omit": 0.0, "w_mom": 0.2, "w_pair": 0.0, "w_zone": 0.0}, "热号")
    if strategy == "cold_rebound_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.0, "w_omit": 0.7, "w_mom": 0.3, "w_pair": 0.0, "w_zone": 0.0}, "冷号")
    if strategy == "momentum_v1":
        return _apply_weight_config(draws, {"window": 80, "w_freq": 0.1, "w_omit": 0.0, "w_mom": 0.9, "w_pair": 0.0, "w_zone": 0.0}, "动量")
    if strategy == "ensemble_v2":
        return _ensemble_strategy(draws, mined_config)
    if strategy == "pattern_mined_v1":
        cfg = mined_config or _default_mined_config()
        return _apply_weight_config(draws, cfg, "规律挖掘")
    return _apply_weight_config(draws, {"window": 80, "w_freq": 0.4, "w_omit": 0.3, "w_mom": 0.2, "w_pair": 0.05, "w_zone": 0.05}, "平衡")


def _ensemble_strategy(draws, mined_cfg=None):
    m_hot = _apply_weight_config(draws, {"window": 80, "w_freq": 0.8, "w_omit": 0.0, "w_mom": 0.2, "w_pair": 0.0, "w_zone": 0.0}, "热号")
    m_cold = _apply_weight_config(draws, {"window": 80, "w_freq": 0.0, "w_omit": 0.7, "w_mom": 0.3, "w_pair": 0.0, "w_zone": 0.0}, "冷号")
    m_mom = _apply_weight_config(draws, {"window": 80, "w_freq": 0.1, "w_omit": 0.0, "w_mom": 0.9, "w_pair": 0.0, "w_zone": 0.0}, "动量")
    m_bal = _apply_weight_config(draws, {"window": 80, "w_freq": 0.4, "w_omit": 0.3, "w_mom": 0.2, "w_pair": 0.05, "w_zone": 0.05}, "平衡")
    m_mined = _apply_weight_config(draws, mined_cfg or _default_mined_config(), "规律挖掘")
    score_maps = [m_hot[3], m_cold[3], m_mom[3], m_bal[3], m_mined[3]]
    votes = {n: 0.0 for n in ALL_NUMBERS}
    for m in score_maps:
        ranked = sorted(m.items(), key=lambda x: x[1], reverse=True)
        for rank, (n, _) in enumerate(ranked):
            votes[n] += float(49 - rank)
    voted = _normalize(votes)
    picked = _pick_top_six(voted, "集成投票")
    main_set = {n for n, _, _, _ in picked}
    candidates = [(n, s) for n, s in sorted(voted.items(), key=lambda x: x[1], reverse=True) if n not in main_set]
    if not candidates:
        candidates = sorted(voted.items(), key=lambda x: x[1], reverse=True)
    special_number, special_score = candidates[0]
    return picked, special_number, special_score, voted


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
    conn.execute("DELETE FROM prediction_pools WHERE run_id = ?", (run_id,))
    now = utc_now()
    for size, nums in pools.items():
        conn.execute("INSERT INTO prediction_pools(run_id, pool_size, numbers_json, created_at) VALUES (?, ?, ?, ?)",
                     (run_id, size, json.dumps(nums), now))


def generate_predictions(conn, issue_no=None):
    row = conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("No draws in database.")
    target_issue = issue_no or next_issue(row["issue_no"])
    draws = [json.loads(r["numbers_json"]) for r in conn.execute(
        "SELECT numbers_json FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200"
    ).fetchall()]
    if len(draws) < 20:
        raise RuntimeError("Need at least 20 draws.")
    mined_cfg = _default_mined_config()
    for strategy in STRATEGY_IDS:
        now = utc_now()
        cur = conn.execute(
            "INSERT OR REPLACE INTO prediction_runs(issue_no, strategy, status, created_at) VALUES (?, ?, 'PENDING', ?)",
            (target_issue, strategy, now)
        )
        run_id = cur.lastrowid
        picks, special_number, special_score, scores = generate_strategy(draws, strategy, mined_cfg)
        main_numbers = [n for n, _, _, _ in picks]
        conn.executemany(
            "INSERT OR REPLACE INTO prediction_picks(run_id, pick_type, number, rank, score, reason) VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, "MAIN", n, rank, score, reason) for n, rank, score, reason in picks] +
            [(run_id, "SPECIAL", special_number, 1, special_score, "特别号")]
        )
        pools = _build_candidate_pools(scores, main_numbers)
        _save_prediction_pools(conn, run_id, pools)
    conn.commit()
    return target_issue


def review_issue(conn, issue_no):
    draw = conn.execute("SELECT numbers_json, special_number FROM draws WHERE issue_no = ?", (issue_no,)).fetchone()
    if not draw:
        return 0
    winning = set(json.loads(draw["numbers_json"]))
    winning_special = int(draw["special_number"])
    runs = conn.execute("SELECT id FROM prediction_runs WHERE issue_no = ? AND status = 'PENDING'", (issue_no,)).fetchall()
    count = 0
    for run in runs:
        run_id = run["id"]
        mains = [r["number"] for r in conn.execute(
            "SELECT number FROM prediction_picks WHERE run_id = ? AND pick_type = 'MAIN' ORDER BY rank", (run_id,)
        ).fetchall()]
        special = next((r["number"] for r in conn.execute(
            "SELECT number FROM prediction_picks WHERE run_id = ? AND pick_type = 'SPECIAL'", (run_id,)
        ).fetchall()), None)
        pool10 = [r[0] for r in conn.execute("SELECT number FROM prediction_pools WHERE run_id = ? AND pool_size = 10", (run_id,)).fetchall()] or mains
        pool14 = [r[0] for r in conn.execute("SELECT number FROM prediction_pools WHERE run_id = ? AND pool_size = 14", (run_id,)).fetchall()] or mains
        pool20 = [r[0] for r in conn.execute("SELECT number FROM prediction_pools WHERE run_id = ? AND pool_size = 20", (run_id,)).fetchall()] or mains
        hit_count = _pool_hit_count(mains, winning)
        hit_count_10 = _pool_hit_count(pool10, winning)
        hit_count_14 = _pool_hit_count(pool14, winning)
        hit_count_20 = _pool_hit_count(pool20, winning)
        special_hit = 1 if special == winning_special else 0
        conn.execute(
            """UPDATE prediction_runs SET status='REVIEWED', hit_count=?, hit_rate=?,
               hit_count_10=?, hit_rate_10=?, hit_count_14=?, hit_rate_14=?, hit_count_20=?, hit_rate_20=?,
               special_hit=?, reviewed_at=?
               WHERE id=?""",
            (hit_count, hit_count/6.0, hit_count_10, hit_count_10/6.0, hit_count_14, hit_count_14/6.0, hit_count_20, hit_count_20/6.0,
             special_hit, utc_now(), run_id)
        )
        count += 1
    conn.commit()
    return count


def backfill_missing_special_picks(conn):
    runs = conn.execute("SELECT id, strategy FROM prediction_runs WHERE status='PENDING'").fetchall()
    patched = 0
    for run in runs:
        run_id = run["id"]
        existing = conn.execute("SELECT 1 FROM prediction_picks WHERE run_id = ? AND pick_type = 'SPECIAL'", (run_id,)).fetchone()
        if existing:
            continue
        mains = [r["number"] for r in conn.execute("SELECT number FROM prediction_picks WHERE run_id = ? AND pick_type = 'MAIN'", (run_id,)).fetchall()]
        draws = [json.loads(r["numbers_json"]) for r in conn.execute(
            "SELECT numbers_json FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 200"
        ).fetchall()]
        _, special_number, special_score, _ = generate_strategy(draws, run["strategy"])
        if special_number in mains:
            for n in ALL_NUMBERS:
                if n not in mains:
                    special_number = n
                    break
        conn.execute(
            "INSERT OR REPLACE INTO prediction_picks(run_id, pick_type, number, rank, score, reason) VALUES (?, 'SPECIAL', ?, 1, ?, '补齐')",
            (run_id, special_number, special_score)
        )
        patched += 1
    if patched:
        conn.commit()
    return patched


def print_dashboard(conn):
    backfill_missing_special_picks(conn)
    latest = conn.execute("SELECT * FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if latest:
        nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
        print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")

    pending = conn.execute("SELECT id, issue_no, strategy FROM prediction_runs WHERE status='PENDING' ORDER BY strategy").fetchall()
    if pending:
        print(f"\n预测期号: {pending[0]['issue_no']}")
        for r in pending:
            mains = [str(x["number"]).zfill(2) for x in conn.execute(
                "SELECT number FROM prediction_picks WHERE run_id = ? AND pick_type = 'MAIN' ORDER BY rank", (r["id"],)
            ).fetchall()]
            special = next((str(x["number"]).zfill(2) for x in conn.execute(
                "SELECT number FROM prediction_picks WHERE run_id = ? AND pick_type = 'SPECIAL'", (r["id"],)
            ).fetchall()), "--")
            label = STRATEGY_LABELS.get(r["strategy"], r["strategy"])
            print(f"  {label:　<8s}: {' '.join(mains)} + {special}")


# ================== 命令行 ==================
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        # 完全在线获取，不回退到 CSV
        records, source_label, used_url = fetch_online_records_with_multi_fallback(
            args.official_url, THIRD_PARTY_URLS_DEFAULT
        )
        total, ins, upd = sync_from_records(conn, records, source_label)
        print(f"数据同步完成: total={total}, new={ins}, updated={upd}, source={source_label} ({used_url})")
        # 复盘最新一期
        latest_issue = conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC LIMIT 1").fetchone()["issue_no"]
        review_issue(conn, latest_issue)
        # 回测（如果启用）
        if args.with_backtest:
            from datetime import timedelta
            # 简单增量回测最近30期
            recent_issues = [r["issue_no"] for r in conn.execute(
                "SELECT issue_no FROM draws ORDER BY draw_date DESC LIMIT 30"
            ).fetchall()]
            for issue in recent_issues:
                review_issue(conn, issue)
        # 生成预测
        issue = generate_predictions(conn)
        print(f"已生成 {issue} 期预测。")
        print_dashboard(conn)
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()


def cmd_show(args):
    conn = connect_db(args.db)
    try:
        print_dashboard(conn)
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--official-url", default=OFFICIAL_URL_DEFAULT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--with-backtest", action="store_true")
    sp.set_defaults(func=cmd_sync)
    sub.add_parser("show").set_defaults(func=cmd_show)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()