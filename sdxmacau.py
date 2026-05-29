#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 三彩种智能预测系统 V16 REAL API FINAL
#
# 功能：
# - 新澳门彩
# - 老澳门彩
# - 香港彩
#
# 核心：
# - 动态状态机
# - 熵检测
# - 连续同波检测
# - 高频反转检测
# - 趋势惯性
# - 单双联动
# - 动态单双/大小
# - 指数衰减权重
#
# 新增：
# - 香港真实数据源
# - marksix6.net 数据源
# - HKJC 官方数据源
# - 自动API切换
# - 自动解析开奖号
#
# Python 3.11+
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from collections import Counter
from datetime import datetime

# =========================================================
# 波色映射
# =========================================================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35, 40,
    45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42, 47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22, 27,
    28, 32, 33, 38, 39, 43, 44, 49
}

# =========================================================
# 真实数据源
# =========================================================

HKJC_URL = (
    "https://bet.hkjc.com/contentserver/"
    "jcbw/cmc/last30draw.json"
)

MARKSIX_URL = (
    "https://marksix6.net/index.php?api=1"
)

# =========================================================
# 工具函数
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
# HTTP请求
# =========================================================

def fetch_json(url):

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0"
                )
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=15
        ) as resp:

            data = resp.read().decode("utf-8")

            return json.loads(data)

    except Exception as e:

        print(f"数据源失败: {url}")
        print(e)

        return None

# =========================================================
# 解析 HKJC 官方数据
# =========================================================

def parse_hkjc_data(raw):

    history = []

    if not raw:
        return history

    try:

        draws = raw.get("draws", [])

        for row in draws:

            draw = row.get("drawNumber")

            date = row.get("drawDate")

            nums = row.get("winningNo", "")

            if not nums:
                continue

            first_num = int(
                nums.split("+")[0]
                .split()[0]
            )

            history.append({
                "issue": str(draw),
                "date": date,
                "number": first_num
            })

    except Exception as e:

        print("HKJC解析失败:", e)

    return history

# =========================================================
# 解析 marksix 数据
# =========================================================

def parse_marksix_data(raw):

    history = []

    if not raw:
        return history

    try:

        if isinstance(raw, dict):

            for k, v in raw.items():

                if not isinstance(v, dict):
                    continue

                issue = v.get("expect", k)

                date = v.get("opentime", "")

                code = (
                    v.get("opencode")
                    or v.get("openCode")
                    or ""
                )

                if not code:
                    continue

                first = int(code.split(",")[0])

                history.append({
                    "issue": str(issue),
                    "date": date,
                    "number": first
                })

        elif isinstance(raw, list):

            for row in raw:

                issue = row.get("expect")

                date = row.get("opentime", "")

                code = (
                    row.get("opencode")
                    or row.get("openCode")
                    or ""
                )

                if not code:
                    continue

                first = int(code.split(",")[0])

                history.append({
                    "issue": str(issue),
                    "date": date,
                    "number": first
                })

    except Exception as e:

        print("marksix解析失败:", e)

    return history

# =========================================================
# 获取真实历史数据
# =========================================================

def fetch_real_history():

    # 优先HKJC官方

    raw = fetch_json(HKJC_URL)

    history = parse_hkjc_data(raw)

    if len(history) >= 20:

        history.reverse()

        return history

    # 备用源

    raw = fetch_json(MARKSIX_URL)

    history = parse_marksix_data(raw)

    if len(history) >= 20:

        history.reverse()

        return history

    raise Exception("无法获取真实数据")

# =========================================================
# 熵计算
# =========================================================

def calc_entropy(probs):

    entropy = 0.0

    for p in probs:

        if p > 0:
            entropy -= p * math.log(p)

    return entropy

# =========================================================
# 状态检测
# =========================================================

def detect_repeat_pattern(colors):

    if len(colors) < 4:
        return False

    return (
        colors[-1]
        == colors[-2]
        == colors[-3]
    )


def detect_flip_pattern(colors):

    if len(colors) < 5:
        return False

    flip = 0

    for i in range(-4, -1):

        if colors[i] != colors[i + 1]:
            flip += 1

    return flip >= 3


def detect_hot_cold_shift(freq):

    values = sorted(
        freq.values(),
        reverse=True
    )

    if len(values) < 3:
        return False

    gap = values[0] - values[2]

    return gap < 0.08


def detect_entropy_rise(
    entropy_now,
    entropy_prev
):

    return (
        entropy_now - entropy_prev
    ) > 0.08

# =========================================================
# 综合状态机
# =========================================================

def analyze_market_state(
    recent_colors,
    probs,
    entropy_now,
    entropy_prev
):

    freq = {
        "红": probs[0],
        "蓝": probs[1],
        "绿": probs[2],
    }

    repeat_state = detect_repeat_pattern(
        recent_colors
    )

    flip_state = detect_flip_pattern(
        recent_colors
    )

    chaos_state = detect_hot_cold_shift(
        freq
    )

    entropy_rise = detect_entropy_rise(
        entropy_now,
        entropy_prev
    )

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
# 趋势惯性系统
# =========================================================

def inertia_adjustment(
    current_probs,
    recent_colors
):

    if len(recent_colors) < 3:
        return current_probs

    last = recent_colors[-1]

    streak = 1

    for i in range(
        len(recent_colors)-2,
        -1,
        -1
    ):

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
# 单双联动增强
# =========================================================

def odd_even_boost(
    probs,
    odd_prob,
    even_prob
):

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
# 概率计算
# =========================================================

def calculate_probabilities(history):

    red_score = 0
    blue_score = 0
    green_score = 0

    big_score = 0
    small_score = 0

    odd_score = 0
    even_score = 0

    recent_colors = []

    total_weight = 0

    for i, row in enumerate(
        reversed(history[-60:])
    ):

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

    probs = {
        "红": red_prob,
        "蓝": blue_prob,
        "绿": green_prob,
    }

    probs = inertia_adjustment(
        probs,
        recent_colors
    )

    probs = odd_even_boost(
        probs,
        odd_prob,
        even_prob
    )

    entropy = calc_entropy([
        probs["红"],
        probs["蓝"],
        probs["绿"]
    ])

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
# 策略系统
# =========================================================

def strategy_engine(
    data,
    prev_entropy=1.0
):

    probs = data["probs"]

    entropy = data["entropy"]

    recent_colors = data["recent_colors"]

    sorted_colors = sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    top1 = sorted_colors[0][0]
    top2 = sorted_colors[1][0]

    market_state = analyze_market_state(
        recent_colors,
        [
            probs["红"],
            probs["蓝"],
            probs["绿"]
        ],
        entropy,
        prev_entropy
    )

    if market_state == "混沌":

        final_predict = [top1, top2]
        strategy_mode = "混沌双推"

    elif market_state == "熵突增":

        final_predict = [top1, top2]
        strategy_mode = "熵增防御"

    elif market_state == "高频反转":

        final_predict = [top2]
        strategy_mode = "反转防追"

    elif market_state == "连续同波":

        final_predict = [top2, top1]
        strategy_mode = "连续同波反转"

    elif market_state == "均衡震荡":

        final_predict = [top1, top2]
        strategy_mode = "动态双推"

    else:

        final_predict = [top1]
        strategy_mode = "单推"

    return {
        "predict": final_predict,
        "mode": strategy_mode,
        "state": market_state
    }

# =========================================================
# 回测
# =========================================================

def backtest(history):

    hit_main = 0
    hit_double = 0

    size_hit = 0
    odd_even_hit = 0

    total = 0

    prev_entropy = 1.0

    print("=" * 60)
    print("最近10期详细回测")
    print("=" * 60)

    for i in range(-11, -1):

        sub_history = history[:i]

        actual = history[i]

        actual_color = get_color(
            actual["number"]
        )

        data = calculate_probabilities(
            sub_history
        )

        result = strategy_engine(
            data,
            prev_entropy
        )

        prev_entropy = data["entropy"]

        predict = result["predict"]

        strategy_mode = result["mode"]

        probs = data["probs"]

        top1 = max(
            probs,
            key=probs.get
        )

        main_hit = (
            top1 == actual_color
        )

        double_hit = (
            actual_color in predict
        )

        if main_hit:
            hit_main += 1

        if double_hit:
            hit_double += 1

        size_predict = (
            "大"
            if data["big_prob"]
            > data["small_prob"]
            else "小"
        )

        size_real = get_big_small(
            actual["number"]
        )

        size_ok = (
            size_predict == size_real
        )

        if size_ok:
            size_hit += 1

        oe_predict = (
            "单"
            if data["odd_prob"]
            > data["even_prob"]
            else "双"
        )

        oe_real = get_odd_even(
            actual["number"]
        )

        oe_ok = (
            oe_predict == oe_real
        )

        if oe_ok:
            odd_even_hit += 1

        total += 1

        print(
            f'{actual["issue"]} '
            f'{actual["date"]} | '
            f'波色:{"+".join(predict)} | '
            f'开:{actual_color} | '
            f'主推:{"√" if main_hit else "×"} | '
            f'双推:{"√" if double_hit else "×"} | '
            f'熵:{data["entropy"]:.3f} | '
            f'模式:{strategy_mode} | '
            f'大小:{size_predict}/{size_real} '
            f'{"√" if size_ok else "×"} | '
            f'单双:{oe_predict}/{oe_real} '
            f'{"√" if oe_ok else "×"}'
        )

    print()

    print("=" * 60)
    print("最近10期命中统计")
    print("=" * 60)

    print(
        f"波色主推命中率: "
        f"{hit_main/total*100:.2f}%"
    )

    print(
        f"波色双推命中率: "
        f"{hit_double/total*100:.2f}%"
    )

    print(
        f"大小命中率: "
        f"{size_hit/total*100:.2f}%"
    )

    print(
        f"单双命中率: "
        f"{odd_even_hit/total*100:.2f}%"
    )

# =========================================================
# 主预测
# =========================================================

def run_predict(lottery_name):

    print("=" * 60)
    print(lottery_name)
    print("=" * 60)
    print()

    history = fetch_real_history()

    print(
        f"同步完成 "
        f"真实数据:{len(history)}期"
    )

    print()

    backtest(history)

    data = calculate_probabilities(
        history
    )

    result = strategy_engine(data)

    probs = data["probs"]

    print()
    print("=" * 60)
    print("下期预测")
    print("=" * 60)
    print()

    print("【波色】")

    for k, v in sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        print(f"{k} : {v*100:.2f}%")

    print()

    print(
        f'推荐: '
        f'{" + ".join(result["predict"])}'
    )

    print(f'策略模式: {result["mode"]}')

    print(
        f'系统熵值: '
        f'{data["entropy"]:.4f}'
    )

    print(f'状态: {result["state"]}')

    print()

    print("【大小】")

    print(
        f'大 : '
        f'{data["big_prob"]*100:.2f}%'
    )

    print(
        f'小 : '
        f'{data["small_prob"]*100:.2f}%'
    )

    print()

    print("【单双】")

    print(
        f'单 : '
        f'{data["odd_prob"]*100:.2f}%'
    )

    print(
        f'双 : '
        f'{data["even_prob"]*100:.2f}%'
    )

    print()
    print("=" * 60)

# =========================================================
# MAIN
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        type=str,
        default="香港彩"
    )

    args = parser.parse_args()

    run_predict(args.lottery)

# =========================================================

if __name__ == "__main__":
    main()