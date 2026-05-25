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

# ---------------- 配置 ----------------
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "new_macau.db")

# 数据源：marksix6.net 的历史接口可一次性返回约120期数据
DATA_URL = "https://marksix6.net/index.php?api=1"

ALL_NUMBERS = list(range(1, 50))

STRATEGY_LABELS = {
    "balanced_v1": "组合策略",
    "hot_v1": "热号策略",
    "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量",
    "ensemble_v2": "集成投票",
    "pattern_mined_v1": "规律挖掘",
}
STRATEGY_IDS = list(STRATEGY_LABELS.keys())

# ---------------- 波色 / 属性 ----------------
def get_color(num: int) -> str:
    if 1 <= num <= 16: return "红"
    elif 17 <= num <= 32: return "蓝"
    else: return "绿"

def special_attributes(num: int) -> Dict[str, str]:
    odd_even = "单" if num % 2 == 1 else "双"
    big_small = "大" if num >= 25 else "小"
    tens, ones = divmod(num, 10)
    total = tens + ones
    total_odd_even = "单" if total % 2 == 1 else "双"
    total_big_small = "大" if total >= 7 else "小"
    tail_big_small = "大" if ones >= 5 else "小"
    color = get_color(num)
    if ones in (1, 6): element = "水"
    elif ones in (2, 7): element = "火"
    elif ones in (3, 8): element = "木"
    elif ones in (4, 9): element = "金"
    else: element = "土"
    return {
        "单双": odd_even, "大小": big_small,
        "合单双": total_odd_even, "合大小": total_big_small,
        "尾大小": tail_big_small, "色波": color, "五行": element
    }

# ---------------- 数据库 ----------------
def connect_db(db_path=DB_PATH_DEFAULT):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
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
    """)
    conn.commit()

# ---------------- 数据获取（增强鲁棒性） ----------------
def fetch_all_newmacau_data():
    """
    从 marksix6.net 接口拉取新澳门彩所有可用历史数据。
    返回 DrawRecord 列表，按开奖时间升序排列。
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    req = Request(DATA_URL, headers=headers)
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
    except Exception as e:
        print(f"❌ 主数据源请求失败: {e}")
        # 降级：只获取最新一期（如果数据库为空则无法生成预测）
        return _fallback_latest()

    # 尝试多种结构
    lottery_data = data.get("lottery_data")
    if isinstance(lottery_data, list):
        # 查找新澳门彩
        for lottery in lottery_data:
            if isinstance(lottery, dict) and lottery.get("name") == "新澳门彩":
                return _parse_newmacau_history(lottery)
        # 可能名称略有不同，尝试第一个
        if lottery_data:
            print("⚠️ 未找到 '新澳门彩'，尝试使用第一个 lottery_data")
            return _parse_newmacau_history(lottery_data[0])
    elif isinstance(data, list):
        # 直接是字符串数组格式 ["期号,号码,..."]
        records = _parse_string_array(data)
        if records:
            print(f"✅ 解析到 {len(records)} 期数据（数组格式）")
            return records
    # 未知格式，尝试备用源
    print("⚠️ 无法识别主数据源格式，尝试备用接口...")
    return _fallback_latest()

def _parse_newmacau_history(lottery_item):
    """解析单个 lottery 条目中的 history 列表"""
    history = lottery_item.get("history")
    if not isinstance(history, list):
        return []
    # 获取最新开奖时间作为日期推算基准
    open_time_str = lottery_item.get("openTime", "")
    try:
        base_time = datetime.strptime(open_time_str, "%Y-%m-%d %H:%M:%S")
    except:
        base_time = datetime.now()

    records = []
    for idx, item in enumerate(history):
        if not isinstance(item, str):
            continue
        # 格式： "2026144期：47,31,29,33,22,26,43"
        parts = item.split("期：")
        if len(parts) != 2:
            # 也可能没有“期：”，直接是“2026144,47,31,...”
            try:
                nums = [int(x.strip()) for x in item.split(",")]
                if len(nums) == 8:  # 期号 + 7个号码
                    issue_no = str(nums[0])
                    main = nums[1:7]
                    special = nums[7]
                    # 日期粗略按倒序推算（每天一期）
                    draw_date = (base_time - timedelta(days=idx)).strftime("%Y-%m-%d")
                    records.append(DrawRecord(issue_no, draw_date, main, special))
            except:
                continue
            continue
        issue_no = parts[0].strip()
        nums_str = parts[1].split(",")
        if len(nums_str) != 7:
            continue
        try:
            nums = [int(x.strip()) for x in nums_str]
            main = nums[:6]
            special = nums[6]
            draw_date = (base_time - timedelta(days=idx)).strftime("%Y-%m-%d")
            records.append(DrawRecord(issue_no, draw_date, main, special))
        except ValueError:
            continue
    # 按期号升序
    records.sort(key=lambda x: int(x.issue_no))
    return records

def _parse_string_array(data_list):
    """解析纯字符串数组 ['期号,号码1,...'] """
    records = []
    for item in data_list:
        if not isinstance(item, str):
            continue
        parts = item.split(",")
        if len(parts) != 8:
            continue
        try:
            issue = parts[0].strip()
            nums = [int(x) for x in parts[1:8]]
            records.append(DrawRecord(issue, "2026-05-25", nums[:6], nums[6]))
        except ValueError:
            continue
    records.sort(key=lambda x: int(x.issue_no))
    return records

def _fallback_latest():
    """备用：从 api3.marksix6.net 获取最新一期"""
    try:
        req = Request("https://api3.marksix6.net/lottery_api.php?type=newMacau",
                      headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        issue = data.get("expect", "")
        code = data.get("openCode", "")
        nums = [int(x) for x in code.split(",")]
        if len(nums) == 7 and issue:
            return [DrawRecord(str(issue), str(data.get("openTime", ""))[:10], nums[:6], nums[6])]
    except Exception as e:
        print(f"备用接口也失败: {e}")
    return []

# ---------------- 数据存储 ----------------
def save_records(conn, records):
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    for r in records:
        if conn.execute("SELECT 1 FROM draws WHERE issue_no=?", (r.issue_no,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO draws VALUES (?,?,?,?,?,?,?)",
            (r.issue_no, r.draw_date, json.dumps(r.numbers), r.special_number, "online", now, now)
        )
        new += 1
    conn.commit()
    return new

def get_history(conn, limit=200):
    rows = conn.execute(f"SELECT * FROM draws ORDER BY issue_no ASC LIMIT {limit}").fetchall()
    return rows

# ---------------- 预测逻辑（沿用你原有设计，略作精简） ----------------
def predict_color_weighted(specials, window=10):
    if len(specials) < window:
        return "蓝", "绿", 0, 0
    recent = specials[-window:]
    scores = defaultdict(float)
    for i, num in enumerate(reversed(recent)):
        scores[get_color(num)] += window - i
    total = sum(scores.values())
    sorted_c = sorted(scores.items(), key=lambda x: -x[1])
    return sorted_c[0][0], sorted_c[1][0], sorted_c[0][1]/total, sorted_c[1][1]/total

def backtest_colors(conn, recent_limit=10):
    rows = conn.execute("SELECT special_number FROM draws ORDER BY issue_no ASC").fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + 10:
        return 0,0,0
    hit = total = 0
    for i in range(len(specials)-recent_limit, len(specials)):
        train = specials[:i]
        actual = get_color(specials[i])
        main, second, _, _ = predict_color_weighted(train, 10)
        if actual in (main, second):
            hit += 1
        total += 1
    return hit, total, round(hit/total*100,1) if total else 0

def generate_predictions(conn, target_issue):
    draws = [json.loads(r["numbers_json"]) for r in get_history(conn, 200)]
    if len(draws) < 20:
        raise RuntimeError("历史期数不足20期")
    # 简化策略：仅示范几个，实际可扩展
    strategies = {
        "hot_v1": hot_strategy,
        "cold_rebound_v1": cold_strategy,
        "momentum_v1": momentum_strategy,
    }
    # 此处省略详细预测入库，直接打印
    print(f"\n预测期号: {target_issue}")
    for name, func in strategies.items():
        try:
            main, special = func(draws)
            if main is None:
                print(f"  {STRATEGY_LABELS[name]}: 数据不足")
                continue
            nums_str = " ".join(f"{n:02d}" for n in main)
            print(f"  {STRATEGY_LABELS[name]}: {nums_str} + {special:02d}")
        except Exception as e:
            print(f"  {STRATEGY_LABELS[name]} 出错: {e}")

def hot_strategy(draws):
    if len(draws) < 50: return None, None
    freq = Counter()
    for d in draws[-50:]:
        freq.update(d)
    ranked = freq.most_common(7)
    return [n for n, _ in ranked[:6]], ranked[6][0]

def cold_strategy(draws):
    if len(draws) < 50: return None, None
    freq = Counter()
    for d in draws[-50:]:
        freq.update(d)
    missing = sorted([n for n in range(1,50) if n not in freq])
    if len(missing) < 7: return None, None
    return missing[:6], missing[6]

def momentum_strategy(draws):
    if len(draws) < 30: return None, None
    score = defaultdict(float)
    for i, d in enumerate(draws[-30:]):
        w = 1.0/(1+i)
        for n in d: score[n] += w
    ranked = sorted(score.items(), key=lambda x: -x[1])
    if len(ranked) < 7: return None, None
    return [n for n, _ in ranked[:6]], ranked[6][0]

# ---------------- 主流程 ----------------
def main():
    conn = connect_db()
    init_db(conn)
    try:
        # 1. 获取在线数据
        print("🔍 正在获取新澳门彩历史数据...")
        records = fetch_all_newmacau_data()
        if not records:
            print("❌ 所有数据源均失败，退出")
            return
        print(f"📦 获取到 {len(records)} 期数据，范围 {records[0].issue_no} ~ {records[-1].issue_no}")
        new = save_records(conn, records)
        print(f"✅ 本次保存 {new} 条新记录")

        # 2. 查看现有数据量
        all_rows = get_history(conn)
        print(f"📊 数据库共有 {len(all_rows)} 期数据")
        if len(all_rows) == 0:
            print("无数据，无法预测")
            return

        # 3. 最新一期及预测
        latest = all_rows[-1]
        print(f"\n最新开奖: {latest['issue_no']} | " +
              " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"])) +
              f" + {latest['special_number']:02d}")
        next_issue = str(int(latest["issue_no"]) + 1)
        generate_predictions(conn, next_issue)

        # 4. 波色预测与回测
        specials = [r["special_number"] for r in all_rows]
        if len(specials) >= 10:
            main_color, second_color, ms, ss = predict_color_weighted(specials, 10)
            print(f"\n🎨 特码波色预测（加权，近10期）: 主强 {main_color} ({ms:.2f})  次强 {second_color} ({ss:.2f})")
            hit, total, rate = backtest_colors(conn, 10)
            if total:
                print(f"📈 近10期波色回测（二中一）: 命中率 {rate}% ({hit}/{total})")
        else:
            print("\n波色数据不足")

    except Exception as e:
        print(f"运行出错: {e}")
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
