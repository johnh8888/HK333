#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH_DEFAULT = str(SCRIPT_DIR / "marksix_local.db")
OFFICIAL_URL_DEFAULT = "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
THIRD_PARTY_URLS_DEFAULT: List[str] = ["https://marksix6.net/index.php?api=1"]
MINED_CONFIG_KEY = "mined_strategy_config_v1"
ALL_NUMBERS = list(range(1, 50))
STRATEGY_LABELS = {
    "balanced_v1": "组合策略", "hot_v1": "热号策略", "cold_rebound_v1": "冷号回补",
    "momentum_v1": "近期动量", "ensemble_v2": "集成投票", "pattern_mined_v1": "规律挖掘",
}
STRATEGY_IDS = ["balanced_v1", "hot_v1", "cold_rebound_v1", "momentum_v1", "ensemble_v2", "pattern_mined_v1"]

# ---------- 波色工具 ----------
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
    return {"单双": odd_even, "大小": big_small, "合单双": total_odd_even,
            "合大小": total_big_small, "尾大小": tail_big_small, "色波": color, "五行": element}

# ---------- 波色预测（新加权方法 + 原简单方法） ----------
def predict_color_simple(specials: List[int], window: int = 3) -> Tuple[str, str, float, float]:
    """简单频率统计"""
    if not specials: return "蓝", "绿", 0.0, 0.0
    recent = specials[-window:]
    counter = Counter(get_color(n) for n in recent)
    sorted_colors = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    main_color = sorted_colors[0][0]
    main_freq = sorted_colors[0][1] / len(recent)
    second_color = sorted_colors[1][0] if len(sorted_colors) > 1 else "绿"
    second_freq = sorted_colors[1][1] / len(recent) if len(sorted_colors) > 1 else 0.0
    return main_color, second_color, main_freq, second_freq

def predict_color_weighted(specials: List[int], window: int = 10) -> Tuple[str, str, float, float]:
    """加权频率：最近一期权重最高，线性递减"""
    if not specials: return "蓝", "绿", 0.0, 0.0
    recent = specials[-window:]
    scores = defaultdict(float)
    total_weight = 0
    # 权重：最近一期权重 = window，最远一期权重 = 1
    for i, num in enumerate(reversed(recent)):   # i=0 对应最近一期
        weight = window - i
        scores[get_color(num)] += weight
        total_weight += weight
    if total_weight == 0:
        return "蓝", "绿", 0.0, 0.0
    sorted_colors = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    main_color = sorted_colors[0][0]
    main_score = sorted_colors[0][1] / total_weight
    second_color = sorted_colors[1][0] if len(sorted_colors) > 1 else "绿"
    second_score = sorted_colors[1][1] / total_weight if len(sorted_colors) > 1 else 0.0
    return main_color, second_color, main_score, second_score

def predict_color(specials: List[int], window: int = 10, method: str = "weighted") -> Tuple[str, str, float, float]:
    """统一接口"""
    if method == "simple":
        return predict_color_simple(specials, window)
    else:  # 默认加权
        return predict_color_weighted(specials, window)

# ---------- 波色回测 ----------
def backtest_colors(conn, recent_limit: int = 10, window: int = 10, method: str = "weighted") -> Tuple[int, int, int, int]:
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()
    specials = [r["special_number"] for r in rows]
    if len(specials) < recent_limit + max(10, window):
        return 0, 0, 0, 0

    total = main_hit = second_hit = any_hit = 0
    start_idx = len(specials) - recent_limit
    for i in range(start_idx, len(specials)):
        train = specials[:i]
        actual = get_color(specials[i])
        main_color, second_color, _, _ = predict_color(train, window=window, method=method)
        if main_color == actual:
            main_hit += 1
        if second_color == actual:
            second_hit += 1
        if main_color == actual or second_color == actual:
            any_hit += 1
        total += 1
    return total, main_hit, second_hit, any_hit

# ---------- 数据库与基础函数（同前，省略粘贴，请保持之前完整版中的部分） ----------
# ... [此处插入之前脚本中从 @dataclass 到 auto_tune_mined_config 的所有函数] ...

# ---------- 展示 ----------
def print_dashboard(conn, color_window=10, color_method="weighted"):
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
            special_row = conn.execute("SELECT number FROM prediction_picks WHERE run_id=? AND pick_type='SPECIAL'", (r["id"],)).fetchone()
            special = str(special_row["number"]).zfill(2) if special_row else "--"
            label = STRATEGY_LABELS.get(r["strategy"], r["strategy"])
            print(f"  {label:　<8s}: {' '.join(mains)} + {special}")
            if special_row:
                attrs = special_attributes(special_row["number"])
                print(f"         特码属性: {attrs['单双']}/{attrs['大小']} 合{attrs['合单双']}/{attrs['合大小']} 尾{attrs['尾大小']} {attrs['色波']} {attrs['五行']}")
    stats = conn.execute("""SELECT strategy, COUNT(*) AS cnt,
        ROUND(AVG(hit_count),2) AS avg_hit, ROUND(AVG(hit_rate)*100,1) AS hit_rate_pct,
        ROUND(AVG(COALESCE(special_hit,0))*100,1) AS special_rate_pct
        FROM prediction_runs WHERE status='REVIEWED' GROUP BY strategy ORDER BY avg_hit DESC""").fetchall()
    if stats:
        print("\n历史命中统计:")
        for s in stats:
            label = STRATEGY_LABELS.get(s["strategy"], s["strategy"])
            print(f"  {label:　<8s}: 期数={s['cnt']}, 平均命中={s['avg_hit']}个, 命中率={s['hit_rate_pct']}%, 特别号命中率={s['special_rate_pct']}%")
    else:
        print("\n暂无复盘数据。")
    # 波色预测
    all_specials = [r["special_number"] for r in conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC").fetchall()]
    if len(all_specials) >= max(color_window, 10):
        main_color, second_color, main_score, second_score = predict_color(all_specials, window=color_window, method=color_method)
        method_name = "加权频率" if color_method == "weighted" else "简单频率"
        print(f"\n🎨 特码波色预测（{method_name}，基于最近 {color_window} 期）：")
        print(f"   主强: {main_color} (得分 {main_score:.2f})   次强: {second_color} (得分 {second_score:.2f})")
        total, main_hit, second_hit, any_hit = backtest_colors(conn, recent_limit=10, window=color_window, method=color_method)
        if total > 0:
            print(f"\n📊 历史回测（最近 10 期，方法={color_method}，窗口={color_window}）：")
            print(f"   主强命中率: {main_hit/total*100:.1f}%   次强命中率: {second_hit/total*100:.1f}%   二中一命中率: {any_hit/total*100:.1f}%")
        else:
            print("\n波色回测数据不足。")
    else:
        print("\n特码数据不足，无法预测波色。")

# ---------- 命令行 ----------
def cmd_sync(args):
    conn = connect_db(args.db)
    try:
        init_db(conn)
        records, source_label, used_url = fetch_online_records_with_multi_fallback(args.official_url, THIRD_PARTY_URLS_DEFAULT)
        total, ins, upd = sync_from_records(conn, records, source_label)
        print(f"数据同步完成: total={total}, new={ins}, updated={upd}, source={source_label} ({used_url})")
        latest_issue = conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC LIMIT 1").fetchone()["issue_no"]
        review_issue(conn, latest_issue)
        if args.with_backtest:
            recent = [r["issue_no"] for r in conn.execute("SELECT issue_no FROM draws ORDER BY draw_date DESC LIMIT 30").fetchall()]
            for issue in recent: review_issue(conn, issue)
        if args.auto_tune: auto_tune_mined_config(conn)
        issue = generate_predictions(conn)
        print(f"已生成 {issue} 期预测。")
        print_dashboard(conn, color_window=args.color_window, color_method=args.color_method)
    except Exception as e: print(f"错误: {e}")
    finally: conn.close()

def cmd_show(args):
    conn = connect_db(args.db)
    try: print_dashboard(conn, color_window=args.color_window, color_method=args.color_method)
    finally: conn.close()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DB_PATH_DEFAULT)
    p.add_argument("--official-url", default=OFFICIAL_URL_DEFAULT)
    p.add_argument("--color-window", type=int, default=10, help="波色预测窗口大小（最近 N 期，默认10）")
    p.add_argument("--color-method", choices=["simple", "weighted"], default="weighted", help="波色预测方法")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--with-backtest", action="store_true")
    sp.add_argument("--auto-tune", action="store_true")
    sp.set_defaults(func=cmd_sync)
    sub.add_parser("show").set_defaults(func=cmd_show)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()