#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import random
import sqlite3
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

LOTTERY_CONFIG = {
    "新澳门彩": {"db": "xin_macau.db"},
    "老澳门彩": {"db": "old_macau.db"},
    "香港彩":   {"db": "hk_macau.db"}
}

ALL_NUMBERS = list(range(1, 50))

def get_color(num):
    if num <= 16: return "红"
    elif num <= 32: return "蓝"
    else: return "绿"

def get_big_small(num): return "大" if num >= 25 else "小"
def get_odd_even(num):  return "单" if num % 2 == 1 else "双"

# ==================== 安全策略生成 ====================
def generate_strategy_numbers(specials, strategy):
    if len(specials) == 0:
        main6 = sorted(random.sample(ALL_NUMBERS, 6))
        sp = random.choice(ALL_NUMBERS)
        return main6, sp

    recent = specials[-100:]
    appeared = set(recent)

    if strategy == "hot":
        hot = [x[0] for x in Counter(recent).most_common(30)]
        if len(hot) < 6:
            hot.extend(random.sample(ALL_NUMBERS, 6))
        main6 = sorted(random.sample(hot[:30], 6))
        sp = random.choice(hot[:15] or ALL_NUMBERS)
    elif strategy == "cold":
        cold = [n for n in ALL_NUMBERS if n not in appeared]
        if len(cold) < 6:
            cold.extend(ALL_NUMBERS[-50:])
        main6 = sorted(random.sample(cold[:40], 6))
        sp = random.choice(cold[:20] or ALL_NUMBERS)
    else:  # balanced
        main6 = sorted(random.sample(recent[-70:] + ALL_NUMBERS, 6))
        sp = random.choice(recent[-40:] or ALL_NUMBERS)
    
    return main6, sp

# ==================== 多窗口加权投票 ====================
def multi_window_vote(specials, attribute_func):
    windows = [3, 5, 10, 20]
    weights = [0.35, 0.30, 0.20, 0.15]
    votes = Counter()
    
    for win_size, weight in zip(windows, weights):
        if len(specials) < win_size:
            continue
        recent = specials[-win_size:]
        for strat in ["balanced", "hot", "cold"]:
            _, sp = generate_strategy_numbers(recent, strat)
            attr = attribute_func(sp)
            votes[attr] += weight * (1.8 if strat == "hot" else 1.0)
    
    total = sum(votes.values())
    main = max(votes, key=votes.get)
    prob = votes[main] / total if total > 0 else 0.333
    
    advantage = prob - (1/3 if attribute_func == get_color else 0.5)
    confidence = round(min(9.5, 5.0 + advantage * 22), 1)
    
    if attribute_func == get_color:
        second = sorted(votes.items(), key=lambda x: x[1], reverse=True)[1][0] if len(votes) > 1 else None
        return main, second, prob, confidence
    return main, None, prob, confidence

# ==================== 回测 ====================
def show_recent_10_backtest(specials, issue_nos, attr_func, attr_name):
    print(f"\n📊 【最近10期 {attr_name} 预测对错记录】")
    print("-" * 90)
    if attr_name == "波色":
        print(f"{'期号':<10} {'实际':<6} {'主推':<6} {'结果':<4} {'次推':<6} {'结果':<4} {'置信度':<6}")
    else:
        print(f"{'期号':<10} {'实际':<6} {'主推':<6} {'结果':<4} {'置信度':<6}")
    
    correct_main = 0
    start = max(0, len(specials) - 10)

    for i in range(start, len(specials)):
        train = specials[:i]
        actual = attr_func(specials[i])
        issue = issue_nos[i]
        
        if attr_name == "波色":
            main, second, _, conf = multi_window_vote(train, attr_func)
            main_ok = "✓" if main == actual else "✗"
            second_ok = "✓" if second == actual else "✗"
            print(f"{issue:<10} {actual:<6} {main:<6} {main_ok:<4} {second or '--':<6} {second_ok:<4} {conf:<6}")
        else:
            main, _, _, conf = multi_window_vote(train, attr_func)
            main_ok = "✓" if main == actual else "✗"
            print(f"{issue:<10} {actual:<6} {main:<6} {main_ok:<4} {conf:<6}")
        
        if main == actual:
            correct_main += 1

    print("-" * 90)
    print(f"最近10期主推准确率: {correct_main/10*100:.1f}%\n")

def predict_lottery(lottery_name):
    db_path = SCRIPT_DIR / LOTTERY_CONFIG[lottery_name]["db"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT issue_no, special_number FROM draws ORDER BY issue_no ASC").fetchall()
    
    issue_nos = [r["issue_no"] for r in rows]
    specials = [r["special_number"] for r in rows]

    if len(specials) < 30:
        print(f"⚠️ {lottery_name} 数据不足（当前 {len(specials)} 期）")
        conn.close()
        return

    print(f"\n{'='*110}")
    print(f"🎯 【{lottery_name} - 多窗口加权投票预测】")
    print(f"{'='*110}")

    show_recent_10_backtest(specials, issue_nos, get_color, "波色")
    show_recent_10_backtest(specials, issue_nos, get_big_small, "大小")
    show_recent_10_backtest(specials, issue_nos, get_odd_even, "单双")

    color_main, color_sec, c_prob, c_conf = multi_window_vote(specials, get_color)
    size_main, _, s_prob, s_conf = multi_window_vote(specials, get_big_small)
    odd_main, _, o_prob, o_conf = multi_window_vote(specials, get_odd_even)

    total_conf = round((c_conf + s_conf + o_conf) / 3, 1)

    print(f"\n🏆 【下一期最终预测】")
    print(f"波色 → 主推: {color_main}波 ({c_prob:.1%})  次推: {color_sec}波  置信度: {c_conf}/10")
    print(f"大小 → 主推: {size_main}     ({s_prob:.1%})   置信度: {s_conf}/10")
    print(f"单双 → 主推: {odd_main}     ({o_prob:.1%})   置信度: {o_conf}/10")
    print(f"📊 综合置信度: {total_conf}/10")
    print(f"\n💡 综合推荐：【{color_main} + {size_main} + {odd_main}】\n")

    conn.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lottery", choices=["新澳门彩","老澳门彩","香港彩","all"], default="all")
    args = parser.parse_args()

    lots = LOTTERY_CONFIG.keys() if args.lottery == "all" else [args.lottery]
    for lot in lots:
        predict_lottery(lot)

if __name__ == "__main__":
    main()