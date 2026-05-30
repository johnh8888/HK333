#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种智能预测系统 V16 FINAL (REAL DATA FIX VERSION)
# =========================================================

from __future__ import annotations

import argparse
import math
import random
import requests
from collections import Counter
from datetime import datetime

# =========================================================
# 波色映射
# =========================================================

RED = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

# =========================================================
# ✅ 真实API数据源（新增替换核心）
# =========================================================

API_URL = "https://marksix6.net/index.php?api=1"


def fetch_api_history(lottery_name="香港彩", size=220):
    """
    从真实API获取历史数据（替代 fake_history）
    """
    try:
        r = requests.get(API_URL, timeout=15)
        data = r.json()
    except Exception as e:
        print("API请求失败，使用空数据:", e)
        return []

    history = []

    for item in data.get("lottery_data", []):
        if item.get("name") != lottery_name:
            continue

        issue = item.get("expect")
        open_time = item.get("openTime", "")

        raw = item.get("openCode", "")
        nums = []

        for x in str(raw).replace(",", " ").split():
            if x.isdigit():
                nums.append(int(x))

        if not nums:
            continue

        history.append({
            "issue": str(issue),
            "date": open_time[:10] if open_time else "",
            "number": nums[-1]
        })

    history.sort(key=lambda x: x["issue"])

    return history[-size:]


# =========================================================
# 工具函数（未改动）
# =========================================================

def get_color(num):
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"


def get_big_small(num):
    return "大" if num >= 25 else "小"


def get_odd_even(num):
    return "单" if num % 2 else "双"


# =========================================================
# 熵计算（未改动）
# =========================================================

def calc_entropy(probs):
    entropy = 0.0
    for p in probs:
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


# =========================================================
# 状态检测（未改动）
# =========================================================

def detect_repeat_pattern(colors):
    if len(colors) < 4:
        return False
    return colors[-1] == colors[-2] == colors[-3]


def detect_flip_pattern(colors):
    if len(colors) < 5:
        return False
    flip = 0
    for i in range(-4, -1):
        if colors[i] != colors[i + 1]:
            flip += 1
    return flip >= 3


def detect_hot_cold_shift(freq):
    values = sorted(freq.values(), reverse=True)
    if len(values) < 3:
        return False
    gap = values[0] - values[2]
    return gap < 0.08


def detect_entropy_rise(entropy_now, entropy_prev):
    return entropy_now - entropy_prev > 0.08


# =========================================================
# 状态机（未改动）
# =========================================================

def analyze_market_state(recent_colors, probs, entropy_now, entropy_prev):
    freq = {"红": probs[0], "蓝": probs[1], "绿": probs[2]}

    repeat_state = detect_repeat_pattern(recent_colors)
    flip_state = detect_flip_pattern(recent_colors)
    chaos_state = detect_hot_cold_shift(freq)
    entropy_rise = detect_entropy_rise(entropy_now, entropy_prev)

    if entropy_now > 1.07:
        return "混沌"
    if entropy_rise:
        return "熵突增"
    if flip_state:
        return "高频反转"
    if repeat_state:
        return "连续同波"
    if chaos_state:
        return "均衡震荡"
    return "稳定趋势"


# =========================================================
# 趋势惯性（未改动）
# =========================================================

def inertia_adjustment(current_probs, recent_colors):
    if len(recent_colors) < 3:
        return current_probs

    last = recent_colors[-1]
    streak = 1

    for i in range(len(recent_colors) - 2, -1, -1):
        if recent_colors[i] == last:
            streak += 1
        else:
            break

    if streak >= 3:
        current_probs[last] *= 0.82
    elif streak == 2:
        current_probs[last] *= 0.92

    total = sum(current_probs.values())
    for k in current_probs:
        current_probs[k] /= total

    return current_probs


# =========================================================
# 单双联动（未改动）
# =========================================================

def odd_even_boost(probs, odd_prob, even_prob):
    if odd_prob > 0.60:
        probs["红"] *= 1.06
        probs["绿"] *= 1.03

    if even_prob > 0.60:
        probs["蓝"] *= 1.08

    total = sum(probs.values())
    for k in probs:
        probs[k] /= total

    return probs


# =========================================================
# 概率计算（未改动）
# =========================================================

def calculate_probabilities(history):
    red_score = blue_score = green_score = 0
    big_score = small_score = 0
    odd_score = even_score = 0

    recent_colors = []
    total_weight = 0

    for i, row in enumerate(reversed(history[-60:])):
        num = row["number"]
        color = get_color(num)

        recent_colors.append(color)

        weight = 0.94 ** i
        total_weight += weight

        if color == "红":
            red_score += weight
        elif color == "蓝":
            blue_score += weight
        else:
            green_score += weight

        if get_big_small(num) == "大":
            big_score += weight
        else:
            small_score += weight

        if get_odd_even(num) == "单":
            odd_score += weight
        else:
            even_score += weight

    red_prob = red_score / total_weight
    blue_prob = blue_score / total_weight
    green_prob = green_score / total_weight

    big_prob = big_score / total_weight
    small_prob = small_score / total_weight
    odd_prob = odd_score / total_weight
    even_prob = even_score / total_weight

    probs = {"红": red_prob, "蓝": blue_prob, "绿": green_prob}

    probs = inertia_adjustment(probs, recent_colors)
    probs = odd_even_boost(probs, odd_prob, even_prob)

    entropy = calc_entropy([probs["红"], probs["蓝"], probs["绿"]])

    return {
        "probs": probs,
        "entropy": entropy,
        "big_prob": big_prob,
        "small_prob": small_prob,
        "odd_prob": odd_prob,
        "even_prob": even_prob,
        "recent_colors": recent_colors
    }


# =========================================================
# 策略系统（未改动）
# =========================================================

def strategy_engine(data, prev_entropy=1.0):
    probs = data["probs"]
    entropy = data["entropy"]
    recent_colors = data["recent_colors"]

    sorted_colors = sorted(probs.items(), key=lambda x: x[1], reverse=True)

    top1 = sorted_colors[0][0]
    top2 = sorted_colors[1][0]

    market_state = analyze_market_state(
        recent_colors,
        [probs["红"], probs["蓝"], probs["绿"]],
        entropy,
        prev_entropy
    )

    if market_state == "混沌":
        return {"predict": [top1, top2], "mode": "混沌双推", "state": market_state}
    elif market_state == "熵突增":
        return {"predict": [top1, top2], "mode": "熵增防御", "state": market_state}
    elif market_state == "高频反转":
        return {"predict": [top2], "mode": "反转防追", "state": market_state}
    elif market_state == "连续同波":
        return {"predict": [top2, top1], "mode": "连续同波反转", "state": market_state}
    elif market_state == "均衡震荡":
        return {"predict": [top1, top2], "mode": "动态双推", "state": market_state}
    else:
        return {"predict": [top1], "mode": "单推", "state": market_state}


# =========================================================
# 回测（未改动）
# =========================================================

def backtest(history):
    hit_main = hit_double = size_hit = odd_even_hit = total = 0
    prev_entropy = 1.0

    print("=" * 60)
    print("最近10期详细回测")
    print("=" * 60)

    for i in range(-11, -1):
        sub_history = history[:i]
        actual = history[i]

        actual_color = get_color(actual["number"])
        data = calculate_probabilities(sub_history)
        result = strategy_engine(data, prev_entropy)

        prev_entropy = data["entropy"]

        predict = result["predict"]

        top1 = max(data["probs"], key=data["probs"].get)

        main_hit = top1 == actual_color
        double_hit = actual_color in predict

        if main_hit:
            hit_main += 1
        if double_hit:
            hit_double += 1

        total += 1

        print(f"{actual['issue']} | 开:{actual_color} | 主推:{'√' if main_hit else '×'} | 双推:{'√' if double_hit else '×'}")


# =========================================================
# 主函数（唯一修改点）
# =========================================================

def run_predict(lottery_name):
    print("=" * 60)
    print(lottery_name)
    print("=" * 60)

    history = fetch_api_history(lottery_name, 220)

    print(f"同步完成: {len(history)} 条数据")

    if len(history) < 50:
        print("数据不足")
        return

    backtest(history)

    data = calculate_probabilities(history)
    result = strategy_engine(data)

    print("\n推荐:", result["predict"])
    print("模式:", result["mode"])
    print("状态:", result["state"])


# =========================================================
# MAIN
# =========================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lottery", type=str, default="新澳门彩")
    args = parser.parse_args()

    run_predict(args.lottery)


if __name__ == "__main__":
    main()
