#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import random
import sqlite3
from collections import Counter
from datetime import datetime
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

# ==================== 策略生成号码 ====================
def generate_strategy_numbers(specials, strategy):
    recent = specials[-100:]
    if strategy == "hot":
        hot = [x[0] for x in Counter(recent).most_common(20)]
        main6 = sorted(random.sample(hot, 6))
        sp = random.choice(hot[:10])
    elif strategy == "cold":
        appeared = set(recent)
        cold = [n for n in ALL_NUMBERS if n not in appeared][-25:] or ALL_NUMBERS[-25:]
        main6 = sorted(random.sample(cold, 6))
        sp = random.choice(cold[:12])
    else:
        main6 = sorted(random.sample(recent[-60:] + ALL_NUMBERS[:30], 6))
        sp = random.choice(recent[-40:])
    return main6, sp

# ==================== 投票预测 ====================
def vote_predict(specials, attribute_func):
    strategies = ["balanced", "hot", "cold", "momentum", "ensemble", "pattern"]
    votes = Counter()
    
    for strat in strategies:
        _, sp = generate_strategy_numbers(specials, strat)
        attr = attribute_func(sp)
        # 热策略和平衡策略权重稍高
        votes[attr] += 1.8 if strat in ["hot", "balanced"] else 1.0

    total = sum(votes.values())
    main = max(votes, key=votes.get)
    prob = votes[main] / total
    
    # 次推只在波色时返回
    if attribute_func == get_color:
        second = sorted(votes.items(), key=lambda x: x[1], reverse=True)[1][0]
        return main, second, prob
    else:
        return main, None, prob   # 大小和单双不返回次推

# ==================== 最近10期回测 ====================
def show_recent_10_backtest(specials, attr_func, attr_name):
    print(f"\n📊 【最近10期 {attr_name} 预测对错记录】")
    print("-" * 65)
    if attr_name == "波色":
        print(f"{'期号':<6} {'实际':<6} {'主推':<6} {'结果':<4} {'次推':<6} {'结果':<4}")
    else:
        print(f"{'期号':<6} {'实际':<6} {'主推':<6} {'结果':<4}")
    
    correct_main = 0
    start = max(0, len(specials) - 10)

    for i in range(start, len(specials)):
        train = specials[:i]
        actual = attr_func(specials[i])
        
        if attr_name == "波色":
            main, second, _ = vote_predict(train, attr_func)
            main_ok = "✓" if main == actual else "✗"
            second_ok = "✓" if second == actual else "✗"
            print(f"{i+1:<6} {actual:<6} {main:<6} {main_ok:<4} {second:<6} {second_ok:<4}")
        else:
            main, _, _ = vote_predict(train, attr_func)
            main_ok = "✓" if main == actual else "✗"
            print(f"{i+1:<6} {actual:<6} {main:<6} {main_ok:<4}")
        
        if main == actual:
            correct_main += 1

    print("-" * 65)
    print(f"最近10期主推准确率: {correct_main/10*100:.1f}%\n")

# ==================== 主预测 ====================
def predict_lottery(lottery_name):
    db_path = SCRIPT_DIR / LOTTERY_CONFIG[lottery_name]["db"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT special_number FROM draws ORDER BY issue_no DESC").fetchall()
    specials = [r["special_number"] for r in rows if r["special_number"]]

    if len(specials) < 80:
        print(f"⚠️ {lottery_name} 数据不足")
        conn.close()
        return

    print(f"\n{'='*95}")
    print(f"🎯 【{lottery_name} - 号码集合投票预测】")
    print(f"{'='*95}")

    # 最近10期回测
    show_recent_10_backtest(specials, get_color, "波色")
    show_recent_10_backtest(specials, get_big_small, "大小")
    show_recent_10_backtest(specials, get_odd_even, "单双")

    # 当前期预测
    color_main, color_sec, c_prob = vote_predict(specials, get_color)
    size_main, _, s_prob = vote_predict(specials, get_big_small)
    odd_main, _, o_prob = vote_predict(specials, get_odd_even)

    print(f"\n🏆 【下一期最终投票预测】")
    print(f"波色 → 主推: {color_main}波 ({c_prob:.1%})   次推: {color_sec}波")
    print(f"大小 → 主推: {size_main}     ({s_prob:.1%})")
    print(f"单双 → 主推: {odd_main}     ({o_prob:.1%})")
    
    print(f"\n💡 综合推荐：【{color_main} + {size_main} + {odd_main}】")

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