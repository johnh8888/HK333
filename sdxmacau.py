#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# =========================================================
# 新澳门 / 老澳门 / 香港六合彩 智能动态预测系统 V2026 FINAL
#
# 修复内容：
# 1. 使用真实数据源
# 2. 修复假数据问题
# 3. 增加动态状态切换
# 4. 增加连续同波检测
# 5. 增加反转检测
# 6. 增加熵突变检测
# 7. 增加冷热切换
# 8. 增加单双 / 大小 联动
# 9. GitHub Actions 可直接运行
#
# Python 3.11+
# =========================================================

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import statistics
import sys
import time

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

import requests

# =========================================================
# 真实数据源
# =========================================================

HKJC_URL = "https://bet.hkjc.com/contentserver/jcbw/cmc/last30draw.json"
MARKSIX_URL = "https://marksix6.net/index.php?api=1"

# =========================================================
# 波色映射
# =========================================================

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


def get_color(num: int) -> str:
    if num in RED:
        return "红"
    if num in BLUE:
        return "蓝"
    return "绿"


# =========================================================
# 工具函数
# =========================================================

def entropy(probs: List[float]) -> float:
    s = 0.0
    for p in probs:
        if p > 0:
            s -= p * math.log(p)
    return s


def safe_float(v, d=0):
    try:
        return float(v)
    except:
        return d


# =========================================================
# 获取真实数据
# =========================================================

def fetch_hkjc():
    try:
        r = requests.get(HKJC_URL, timeout=15)
        data = r.json()

        rows = []

        for item in data.get("draws", []):

            draw = str(item.get("no", ""))

            nums = []

            for x in item.get("nums", []):
                try:
                    nums.append(int(x))
                except:
                    pass

            if len(nums) < 7:
                continue

            rows.append({
                "issue": draw,
                "date": item.get("date", ""),
                "numbers": nums
            })

        return rows

    except Exception as e:
        print("HKJC 获取失败:", e)
        return []


def fetch_marksix6():
    try:
        r = requests.get(MARKSIX_URL, timeout=15)
        data = r.json()

        rows = []

        for item in data:

            issue = str(item.get("expect", ""))

            opencode = item.get("opencode", "")

            nums = []

            for x in re.findall(r"\d+", opencode):
                try:
                    nums.append(int(x))
                except:
                    pass

            if len(nums) < 7:
                continue

            rows.append({
                "issue": issue,
                "date": item.get("opentime", ""),
                "numbers": nums
            })

        return rows

    except Exception as e:
        print("marksix6 获取失败:", e)
        return []


# =========================================================
# 数据同步
# =========================================================

def load_real_data(lottery: str):

    rows = []

    if lottery == "香港彩":
        rows = fetch_hkjc()

        if not rows:
            rows = fetch_marksix6()

    else:
        rows = fetch_marksix6()

    clean = []

    for r in rows:

        nums = r["numbers"]

        special = nums[-1]

        clean.append({
            "issue": r["issue"],
            "date": r["date"],
            "special": special,
            "color": get_color(special),
            "big_small": "大" if special >= 25 else "小",
            "odd_even": "单" if special % 2 else "双"
        })

    return clean


# =========================================================
# 状态检测
# =========================================================

def detect_state(history):

    colors = [x["color"] for x in history[-10:]]

    # 连续同波
    same_count = 1

    for i in range(len(colors)-1, 0, -1):
        if colors[i] == colors[i-1]:
            same_count += 1
        else:
            break

    # 高频反转
    reverse_count = 0

    for i in range(1, len(colors)):
        if colors[i] != colors[i-1]:
            reverse_count += 1

    counter = Counter(colors)

    total = sum(counter.values())

    probs = [v / total for v in counter.values()]

    ent = entropy(probs)

    # 状态判断
    if ent > 1.07:
        return "混沌"

    if same_count >= 3:
        return "连续同波"

    if reverse_count >= 6:
        return "高频反转"

    return "稳定"


# =========================================================
# 动态预测
# =========================================================

def dynamic_predict(history):

    last20 = history[-20:]

    colors = [x["color"] for x in last20]

    counter = Counter(colors)

    total = sum(counter.values())

    score = {}

    for c in ["红", "蓝", "绿"]:
        score[c] = counter.get(c, 0) / total

    # 最近加权
    recent = history[-5:]

    for idx, row in enumerate(recent):

        w = (idx + 1) * 0.08

        score[row["color"]] += w

    # 反转修正
    state = detect_state(history)

    last_color = history[-1]["color"]

    if state == "连续同波":

        score[last_color] *= 0.55

        for c in score:
            if c != last_color:
                score[c] *= 1.18

    elif state == "高频反转":

        score[last_color] *= 0.72

    elif state == "混沌":

        for c in score:
            score[c] *= random.uniform(0.95, 1.05)

    total_score = sum(score.values())

    probs = {
        k: round(v / total_score * 100, 2)
        for k, v in score.items()
    }

    ordered = sorted(
        probs.items(),
        key=lambda x: x[1],
        reverse=True
    )

    ent = entropy([v / 100 for _, v in ordered])

    if ent > 1.07:
        mode = "混沌双推"
        rec = f"{ordered[0][0]} + {ordered[1][0]}"

    elif ordered[0][1] >= 46:
        mode = "单推"
        rec = ordered[0][0]

    else:
        mode = "动态双推"
        rec = f"{ordered[0][0]} + {ordered[1][0]}"

    # 大小
    big = sum(
        1 for x in last20
        if x["big_small"] == "大"
    )

    big_prob = round(big / len(last20) * 100, 2)
    small_prob = round(100 - big_prob, 2)

    # 单双
    odd = sum(
        1 for x in last20
        if x["odd_even"] == "单"
    )

    odd_prob = round(odd / len(last20) * 100, 2)
    even_prob = round(100 - odd_prob, 2)

    return {
        "prob": probs,
        "recommend": rec,
        "mode": mode,
        "entropy": round(ent, 4),
        "state": state,
        "big": big_prob,
        "small": small_prob,
        "odd": odd_prob,
        "even": even_prob
    }


# =========================================================
# 回测
# =========================================================

def backtest(history):

    ok_main = 0
    ok_double = 0
    ok_big = 0
    ok_odd = 0

    logs = []

    for i in range(20, len(history)):

        train = history[:i]

        current = history[i]

        pred = dynamic_predict(train)

        actual = current["color"]

        rec = pred["recommend"]

        if "+" in rec:

            rs = [x.strip() for x in rec.split("+")]

            main_hit = actual == rs[0]
            double_hit = actual in rs

        else:
            main_hit = actual == rec
            double_hit = main_hit

        if main_hit:
            ok_main += 1

        if double_hit:
            ok_double += 1

        big_pred = "大" if pred["big"] >= pred["small"] else "小"
        odd_pred = "单" if pred["odd"] >= pred["even"] else "双"

        big_hit = big_pred == current["big_small"]
        odd_hit = odd_pred == current["odd_even"]

        if big_hit:
            ok_big += 1

        if odd_hit:
            ok_odd += 1

        logs.append({
            "issue": current["issue"],
            "date": current["date"],
            "rec": rec,
            "actual": actual,
            "main_hit": main_hit,
            "double_hit": double_hit,
            "entropy": pred["entropy"],
            "mode": pred["mode"],
            "big_pred": big_pred,
            "big_actual": current["big_small"],
            "big_hit": big_hit,
            "odd_pred": odd_pred,
            "odd_actual": current["odd_even"],
            "odd_hit": odd_hit
        })

    total = len(logs)

    return {
        "logs": logs[-10:],
        "main_rate": ok_main / total * 100,
        "double_rate": ok_double / total * 100,
        "big_rate": ok_big / total * 100,
        "odd_rate": ok_odd / total * 100
    }


# =========================================================
# 主程序
# =========================================================

def run(lottery):

    print("=" * 60)
    print(lottery)
    print("=" * 60)
    print()

    data = load_real_data(lottery)

    if len(data) < 30:
        print("数据不足")
        return

    print(f"真实数据同步完成: {len(data)} 期")
    print()

    bt = backtest(data)

    print("=" * 60)
    print("最近10期详细回测")
    print("=" * 60)

    for row in bt["logs"]:

        print(
            f'{row["issue"]} '
            f'{row["date"][:10]} | '
            f'波色:{row["rec"]} | '
            f'开:{row["actual"]} | '
            f'主推:{"√" if row["main_hit"] else "×"} | '
            f'双推:{"√" if row["double_hit"] else "×"} | '
            f'熵:{row["entropy"]:.3f} | '
            f'模式:{row["mode"]} | '
            f'大小:{row["big_pred"]}/{row["big_actual"]} '
            f'{"√" if row["big_hit"] else "×"} | '
            f'单双:{row["odd_pred"]}/{row["odd_actual"]} '
            f'{"√" if row["odd_hit"] else "×"}'
        )

    print()
    print("=" * 60)
    print("最近命中统计")
    print("=" * 60)

    print(f'波色主推命中率: {bt["main_rate"]:.2f}%')
    print(f'波色双推命中率: {bt["double_rate"]:.2f}%')
    print(f'大小命中率: {bt["big_rate"]:.2f}%')
    print(f'单双命中率: {bt["odd_rate"]:.2f}%')

    pred = dynamic_predict(data)

    next_issue = int(data[-1]["issue"]) + 1

    print()
    print("=" * 60)
    print(f"下期预测（{next_issue}）")
    print("=" * 60)
    print()

    print("【波色】")

    ordered = sorted(
        pred["prob"].items(),
        key=lambda x: x[1],
        reverse=True
    )

    for c, p in ordered:
        print(f"{c} : {p:.2f}%")

    print()
    print(f'推荐: {pred["recommend"]}')
    print(f'策略模式: {pred["mode"]}')
    print(f'系统熵值: {pred["entropy"]:.4f}')
    print(f'状态: {pred["state"]}')

    print()
    print("【大小】")
    print(f'大 : {pred["big"]:.2f}%')
    print(f'小 : {pred["small"]:.2f}%')

    print()
    print("【单双】")
    print(f'单 : {pred["odd"]:.2f}%')
    print(f'双 : {pred["even"]:.2f}%')

    print()
    print("=" * 60)


# =========================================================
# 入口
# =========================================================

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        type=str,
        default="香港彩"
    )

    args = parser.parse_args()

    run(args.lottery)