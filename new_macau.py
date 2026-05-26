#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.request import Request, urlopen
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "xinmacau.db")
OFFICIAL_URL_DEFAULT = "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
THIRD_PARTY_URLS_DEFAULT: List[str] = ["https://marksix6.net/index.php?api=1"]

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

ALLOWED_DOMAINS = (
    "bet.hkjc.com",
    "marksix6.net",
)

# ---------- 波色 / 属性工具 ----------

def get_color(num: int) -> str:
    RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
    BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
    GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

    if num in RED:
        return "红"
    elif num in BLUE:
        return "蓝"
    return "绿"


def special_attributes(num: int) -> Dict[str, str]:
    odd_even = "单" if num % 2 == 1 else "双"
    big_small = "大" if num >= 25 else "小"

    tens, ones = divmod(num, 10)
    total = tens + ones

    total_odd_even = "单" if total % 2 == 1 else "双"
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


# ---------- 波色预测 ----------

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

        # 修复：真正比较前一期颜色
        if i > 0 and color == get_color(recent[-i - 1]):
            scores[color] += weight * 0.35

        total_weight += weight

    if total_weight == 0:
        return "绿", "红", 0.0, 0.0

    sorted_colors = sorted(
        scores.items(),
        key=lambda x: (-x[1], x[0])
    )

    main_color = sorted_colors[0][0]
    main_score = sorted_colors[0][1] / total_weight

    second_color = (
        sorted_colors[1][0]
        if len(sorted_colors) > 1 else "绿"
    )

    second_score = (
        sorted_colors[1][1] / total_weight
        if len(sorted_colors) > 1 else 0.0
    )

    return (
        main_color,
        second_color,
        main_score,
        second_score,
    )


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
        main_freq = sorted_colors[0][1] / len(recent)

        second_color = (
            sorted_colors[1][0]
            if len(sorted_colors) > 1 else "绿"
        )

        second_freq = (
            sorted_colors[1][1] / len(recent)
            if len(sorted_colors) > 1 else 0.0
        )

        return (
            main_color,
            second_color,
            main_freq,
            second_freq,
        )

    return predict_color_weighted(specials, window)


# ---------- 数据库 ----------

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

    # 修复：开启外键
    conn.execute("PRAGMA foreign_keys = ON")

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
            FOREIGN KEY(run_id)
                REFERENCES prediction_runs(id)
                ON DELETE CASCADE
        );
    """)

    conn.commit()


# ---------- 数据获取 ----------

def _validate_url(url: str):
    host = urlparse(url).netloc.lower()

    if not any(host.endswith(d) for d in ALLOWED_DOMAINS):
        raise RuntimeError(f"非法数据源: {url}")


def _valid_numbers(nums: List[int]) -> bool:
    return (
        len(nums) == 7
        and all(1 <= n <= 49 for n in nums)
        and len(set(nums)) == 7
    )


def _parse_marksix6_response(payload):
    records = []

    hk_data = next(
        (
            l for l in payload.get("lottery_data", [])
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
    except Exception as e:
        print(f"时间解析失败: {e}")
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

            if not _valid_numbers(nums):
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

        except Exception as e:
            print(f"第三方数据解析失败: {e}")
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
                int(item[f"no{i}"])
                for i in range(1, 7)
            ]

            special = int(
                item.get("specialNumber")
                or item.get("no7")
            )

            all_nums = numbers + [special]

            if (
                issue_no
                and draw_date
                and _valid_numbers(all_nums)
            ):
                records.append(
                    DrawRecord(
                        issue_no,
                        draw_date,
                        numbers,
                        special
                    )
                )

        except Exception as e:
            print(f"官方数据解析失败: {e}")
            continue

    return records


def fetch_online_records_with_multi_fallback(
    official_url,
    third_party_urls
):

    for u in [official_url] + third_party_urls:
        if u:
            _validate_url(u)

    if official_url.strip():
        try:
            req = Request(
                official_url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urlopen(req, timeout=15) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8-sig")
                )

            records = _parse_official_json(payload)

            if records:
                return records, "official_api", official_url

        except Exception as e:
            print(f"官方源失败: {e}")

    for url in third_party_urls:
        try:
            req = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urlopen(req, timeout=20) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8")
                )

            records = _parse_marksix6_response(payload)

            if records:
                return records, "marksix6", url

        except Exception as e:
            print(f"第三方源失败: {e}")

    raise RuntimeError("所有在线数据源均无法获取数据。")


# ---------- 数据写入 ----------

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
            json.dumps(record.numbers),
            record.special_number,
            source,
            now,
            record.issue_no
        ))

        return "updated"

    conn.execute("""
        INSERT INTO draws(
            issue_no,
            draw_date,
            numbers_json,
            special_number,
            source,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?)
    """, (
        record.issue_no,
        record.draw_date,
        json.dumps(record.numbers),
        record.special_number,
        source,
        now,
        now
    ))

    return "inserted"


# ---------- 主入口 ----------

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        default=DB_PATH_DEFAULT
    )

    parser.add_argument(
        "--official-url",
        default=OFFICIAL_URL_DEFAULT
    )

    args = parser.parse_args()

    conn = connect_db(args.db)

    try:
        init_db(conn)

        records, source, used_url = (
            fetch_online_records_with_multi_fallback(
                args.official_url,
                THIRD_PARTY_URLS_DEFAULT
            )
        )

        ins = upd = 0

        for r in records:
            result = upsert_draw(conn, r, source)

            if result == "inserted":
                ins += 1
            else:
                upd += 1

        conn.commit()

        print(
            f"同步完成: total={len(records)}, "
            f"new={ins}, updated={upd}, source={used_url}"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()