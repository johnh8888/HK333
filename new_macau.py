#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ==================== 基础配置 ====================
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

# 数据源（一次返回大量历史数据）
DATA_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "balanced_v1": "平衡策略",
}

# ==================== 数据模型 ====================
@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]          # 正码 6 个
    special_number: int         # 特码

# ==================== 波色 / 属性 ====================
def get_color(num: int) -> str:
    if 1 <= num <= 16:
        return "红"
    elif 17 <= num <= 32:
        return "蓝"
    else:
        return "绿"

def special_attributes(num: int) -> Dict[str, str]:
    ds = "单" if num % 2 == 1 else "双"
    dx = "大" if num >= 25 else "小"
    tens, ones = divmod(num, 10)
    total = tens + ones
    total_ds = "单" if total % 2 == 1 else "双"
    total_dx = "大" if total >= 7 else "小"
    tail = "尾大" if ones >= 5 else "尾小"
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
        "单双": ds,
        "大小": dx,
        "合单双": total_ds,
        "合大小": total_dx,
        "尾大小": tail,
        "色波": color,
        "五行": element,
    }

# ==================== 数据库 ====================
def connect_db(db_path: str = DB_PATH_DEFAULT) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws (
            issue_no TEXT PRIMARY KEY,
            draw_date TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            special_number INTEGER NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()

def save_records(conn, records: List[DrawRecord]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    for r in records:
        exists = conn.execute(
            "SELECT 1 FROM draws WHERE issue_no=?", (r.issue_no,)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO draws VALUES (?,?,?,?,?,?,?)",
            (
                r.issue_no,
                r.draw_date,
                json.dumps(r.numbers),
                r.special_number,
                "online",
                now,
                now,
            ),
        )
        new += 1
    conn.commit()
    return new

def get_history(conn, limit=200) -> List[sqlite3.Row]:
    return conn.execute(
        f"SELECT * FROM draws ORDER BY issue_no ASC LIMIT {limit}"
    ).fetchall()

# ==================== 数据获取 ====================
def _parse_newmacau_history(lottery_item: dict) -> List[DrawRecord]:
    """解析 marksix6.net 接口中单个 lottery 的历史数据"""
    history = lottery_item.get("history")
    if not isinstance(history, list):
        return []

    # 尝试从 openTime 获取基准日期
    base_time = datetime.now()
    open_time_str = lottery_item.get("openTime", "")
    if open_time_str:
        try:
            base_time = datetime.strptime(open_time_str, "%Y-%m-%d %H:%M:%S")
        except:
            pass

    records = []
    for idx, item in enumerate(history):
        if not isinstance(item, str):
            continue
        # 格式1: "2026144期：47,31,29,33,22,26,43"
        if "期：" in item:
            parts = item.split("期：")
            if len(parts) != 2:
                continue
            issue_no = parts[0].strip()
            nums_str = parts[1].split(",")
        else:
            # 格式2: "2026144,47,31,29,33,22,26,43"
            parts = item.split(",")
            if len(parts) < 8:
                continue
            issue_no = parts[0].strip()
            nums_str = parts[1:8]

        if len(nums_str) != 7:
            continue
        try:
            nums = [int(x.strip()) for x in nums_str]
            main = nums[:6]
            special = nums[6]
            # 粗略推算日期：每天一期（新澳门彩）
            draw_date = (base_time - timedelta(days=idx)).strftime("%Y-%m-%d")
            records.append(DrawRecord(issue_no, draw_date, main, special))
        except ValueError:
            continue

    # 按期号升序排列
    records.sort(key=lambda x: int(x.issue_no))
    return records

def _fallback_latest() -> List[DrawRecord]:
    """备用接口：获取最新一期"""
    try:
        req = Request(
            "https://api3.marksix6.net/lottery_api.php?type=newMacau",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        issue = data.get("expect", "")
        code = data.get("openCode", "")
        if not issue or not code:
            return []
        nums = [int(x) for x in code.split(",")]
        if len(nums) != 7:
            return []
        return [
            DrawRecord(
                str(issue),
                str(data.get("openTime", ""))[:10],
                nums[:6],
                nums[6],
            )
        ]
    except Exception as e:
        print(f"备用接口失败: {e}")
        return []

def fetch_all_newmacau_data() -> List[DrawRecord]:
    """主入口：从 marksix6.net 获取全部历史数据"""
    print(f"尝试从 {DATA_URL} 拉取数据...")
    req = Request(DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
    except Exception as e:
        print(f"主接口失败: {e}")
        return _fallback_latest()

    # 处理两种结构
    lottery_data = data.get("lottery_data")
    if isinstance(lottery_data, list):
        for lottery in lottery_data:
            if isinstance(lottery, dict) and lottery.get("name") == "新澳门彩":
                return _parse_newmacau_history(lottery)
        # 如果没找到 "新澳门彩"，尝试第一个
        if lottery_data and isinstance(lottery_data[0], dict):
            print("未找到 '新澳门彩'，尝试使用第一个 lottery_data")
            return _parse_newmacau_history(lottery_data[0])
    elif isinstance(data, list):
        # 纯字符串数组格式
        records = []
        for item in data:
            if not isinstance(item, str):
                continue
            parts = item.split(",")
            if len(parts) != 8:
                continue
            try:
                issue = parts[0].strip()
                nums = [int(x) for x in parts[1:8]]
                records.append(
                    DrawRecord(issue, datetime.now().strftime("%Y-%m-%d"), nums[:6], nums[6])
                )
            except ValueError:
                continue
        records.sort(key=lambda x: int(x.issue_no))
        if records:
            return records

    print("主接口格式未知，尝试备用接口...")
    return _fallback_latest()

# ==================== 策略 ====================
def hot_strategy(draws: List[List[int]]) -> Tuple[Optional[List[int]], Optional[int]]:
    if len(draws) < 50:
        return None, None
    freq = Counter()
    for d in draws[-50:]:
        freq.update(d)
    ranked = freq.most_common(7)
    return [n for n, _ in ranked[:6]], ranked[6][0]

def cold_strategy(draws: List[List[int]]) -> Tuple[Optional[List[int]], Optional[int]]:
    if len(draws) < 50:
        return None, None
    freq = Counter()
    for d in draws[-50:]:
        freq.update(d)
    missing = sorted([n for n in range(1, 50) if n not in freq])
    if len(missing) < 7:
        return None, None
    return missing[:6], missing[6]

def momentum_strategy(draws: List[List[int]]) -> Tuple[Optional[List[int]], Optional[int]]:
    if len(draws) < 30:
        return None, None
    scores = defaultdict(float)
    for i, d in enumerate(draws[-30:]):
        w = 1.0 / (1 + i)
        for n in d:
            scores[n] += w
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    if len(ranked) < 7:
        return None, None
    return [n for n, _ in ranked[:6]], ranked[6][0]

# ==================== 波色预测与回测 ====================
def predict_color_weighted(specials: List[int], window: int = 10) -> Tuple[str, str, float, float]:
    if len(specials) < window:
        return "蓝", "绿", 0.0, 0.0
    recent = specials[-window:]
    scores = defaultdict(float)
    for i, num in enumerate(reversed(recent)):
        scores[get_color(num)] += window - i
    total = sum(scores.values())
    if total == 0:
        return "蓝", "绿", 0.0, 0.0
    sorted_colors = sorted(scores.items(), key=lambda x: -x[1])
    main_color = sorted_colors[0][0]
    main_score = sorted_colors[0][1] / total
    second_color = sorted_colors[1][0] if len(sorted_colors) > 1 else "绿"
    second_score = sorted_colors[1][1] / total if len(sorted_colors) > 1 else 0.0
    return main_color, second_color, main_score, second_score

def backtest_colors(conn, recent_limit: int = 10) -> Tuple[int, int, float]:
    rows = conn.execute(
        "SELECT special_number FROM draws ORDER BY issue_no ASC"
    ).fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + 10:
        return 0, 0, 0.0
    hit = total = 0
    for i in range(len(specials) - recent_limit, len(specials)):
        train = specials[:i]
        actual = get_color(specials[i])
        main, second, _, _ = predict_color_weighted(train, 10)
        if actual in (main, second):
            hit += 1
        total += 1
    rate = round(hit / total * 100, 1) if total else 0.0
    return hit, total, rate

# ==================== 主流程 ====================
def main():
    conn = connect_db()
    init_db(conn)
    try:
        # 1. 获取数据
        print("🔍 正在获取新澳门彩历史数据...")
        records = fetch_all_newmacau_data()
        if not records:
            print("❌ 所有数据源均无法获取数据，脚本退出")
            return

        print(f"📦 获取到 {len(records)} 期数据，范围 {records[0].issue_no} ~ {records[-1].issue_no}")
        new = save_records(conn, records)
        print(f"✅ 本次保存 {new} 条新记录")

        all_rows = get_history(conn)
        if len(all_rows) == 0:
            print("❌ 数据库无数据，无法预测")
            return

        print(f"📊 数据库共有 {len(all_rows)} 期数据")

        # 最新一期
        latest = all_rows[-1]
        numbers = json.loads(latest["numbers_json"])
        print(f"\n最新开奖: {latest['issue_no']} | " +
              " ".join(f"{n:02d}" for n in numbers) +
              f" + {latest['special_number']:02d}")

        # 2. 生成预测（下期）
        next_issue = str(int(latest["issue_no"]) + 1)
        print(f"\n预测期号: {next_issue}")
        draws = [json.loads(r["numbers_json"]) for r in all_rows]

        strategies = {
            "hot_v1": hot_strategy,
            "cold_rebound_v1": cold_strategy,
            "momentum_v1": momentum_strategy,
        }

        for key, func in strategies.items():
            main, special = func(draws)
            label = STRATEGY_LABELS[key]
            if main is None:
                print(f"  {label:　<8s}: 数据不足，无法推荐")
                continue
            nums_str = " ".join(f"{n:02d}" for n in main)
            print(f"  {label:　<8s}: {nums_str} + {special:02d}")
            if special is not None:
                attrs = special_attributes(special)
                print(f"         特码属性: {attrs['单双']}/{attrs['大小']} 合{attrs['合单双']}/{attrs['合大小']} 尾{attrs['尾大小']} {attrs['色波']} {attrs['五行']}")

        # 3. 波色预测与回测
        specials = [r["special_number"] for r in all_rows]
        if len(specials) >= 10:
            main_color, second_color, ms, ss = predict_color_weighted(specials, 10)
            print(f"\n🎨 特码波色预测（加权，近10期）: 主强 {main_color} ({ms:.2f})  次强 {second_color} ({ss:.2f})")
            hit, total, rate = backtest_colors(conn, 10)
            if total:
                print(f"📈 近10期波色回测（二中一）: 命中率 {rate}% ({hit}/{total})")
            else:
                print("📈 波色回测数据不足")
        else:
            print("\n🎨 特码波色预测: 数据不足")

    except Exception as e:
        print(f"运行出错: {e}")
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
