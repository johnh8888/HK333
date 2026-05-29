#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from collections import Counter
from datetime import datetime

# =========================================================
# 六合彩智能预测系统（稳定最终版）
# 支持：
# - 香港彩
# - 新澳门彩
# - 老澳门彩
#
# 最新稳定接口：
# api3.marksix6.net
# macaumarksix.com
#
# GitHub Actions 可直接运行
# =========================================================

# ---------------------------------------------------------
# 数据源
# ---------------------------------------------------------

LOTTERY_URLS = {
    "香港彩": [
        "https://api3.marksix6.net/lottery_api.php?type=hk",
    ],

    "新澳门彩": [
        "https://api3.marksix6.net/lottery_api.php?type=newMacau",
        "https://macaumarksix.com/api/macaujc2.com",
    ],

    "老澳门彩": [
        "https://api3.marksix6.net/lottery_api.php?type=oldMacau",
    ]
}

ALL_NUMBERS = list(range(1, 50))


# =========================================================
# 波色
# =========================================================

RED_WAVE = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35,
    40, 45, 46
}

BLUE_WAVE = {
    3, 4, 9, 10, 14, 15, 20,
    25, 26, 31, 36, 37,
    41, 42, 47, 48
}

GREEN_WAVE = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}


def get_wave(num):

    if num in RED_WAVE:
        return "红波"

    if num in BLUE_WAVE:
        return "蓝波"

    return "绿波"


# =========================================================
# 获取真实数据
# =========================================================

def fetch_real_history(lottery_name):

    if lottery_name not in LOTTERY_URLS:
        raise Exception(f"未知彩种: {lottery_name}")

    urls = LOTTERY_URLS[lottery_name]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        )
    }

    for url in urls:

        try:

            print("=" * 60)
            print(f"正在获取 {lottery_name} 数据")
            print(url)
            print("=" * 60)

            req = urllib.request.Request(
                url,
                headers=headers
            )

            with urllib.request.urlopen(req, timeout=30) as response:

                raw = response.read().decode(
                    "utf-8",
                    errors="ignore"
                )

            data = json.loads(raw)

            history = []

            # -------------------------------------------------
            # 数据结构兼容
            # -------------------------------------------------

            if isinstance(data, list):

                items = data

            elif isinstance(data, dict):

                if isinstance(data.get("data"), list):

                    items = data["data"]

                elif isinstance(data.get("result"), list):

                    items = data["result"]

                else:

                    items = []

            else:

                items = []

            # -------------------------------------------------
            # 解析
            # -------------------------------------------------

            for item in items:

                try:

                    issue = str(
                        item.get("expect")
                        or item.get("issue")
                        or item.get("period")
                        or item.get("turnNum")
                        or item.get("preDrawIssue")
                        or ""
                    ).strip()

                    code = (
                        item.get("openCode")
                        or item.get("code")
                        or item.get("numbers")
                        or item.get("num")
                        or item.get("preDrawCode")
                        or ""
                    )

                    nums = []

                    # 字符串格式
                    if isinstance(code, str):

                        code = (
                            code.replace("+", ",")
                            .replace("|", ",")
                            .replace(" ", ",")
                        )

                        for x in code.split(","):

                            x = x.strip()

                            if x.isdigit():

                                nums.append(int(x))

                    # list格式
                    elif isinstance(code, list):

                        nums = [
                            int(x)
                            for x in code
                            if str(x).isdigit()
                        ]

                    # 必须7个号码
                    if len(nums) < 7:
                        continue

                    history.append({
                        "issue": issue,
                        "numbers": nums[:6],
                        "special": nums[6]
                    })

                except:
                    continue

            # -------------------------------------------------
            # 成功
            # -------------------------------------------------

            if history:

                history.sort(
                    key=lambda x: x["issue"],
                    reverse=True
                )

                print(f"成功获取 {len(history)} 条历史数据")

                return history

        except Exception as e:

            print(f"数据源失败: {e}")

            continue

    raise Exception("无法获取真实数据")


# =========================================================
# 热号统计
# =========================================================

def calc_hot_numbers(history, top_n=20):

    counter = Counter()

    for row in history:

        for n in row["numbers"]:
            counter[n] += 1

        counter[row["special"]] += 0.5

    ranked = counter.most_common(top_n)

    return [x[0] for x in ranked]


# =========================================================
# 冷号统计
# =========================================================

def calc_cold_numbers(history, top_n=20):

    counter = Counter()

    for row in history:

        for n in row["numbers"]:
            counter[n] += 1

        counter[row["special"]] += 0.5

    ranked = sorted(
        counter.items(),
        key=lambda x: x[1]
    )

    return [x[0] for x in ranked[:top_n]]


# =========================================================
# 动量号码
# =========================================================

def calc_recent_numbers(history, top_n=15):

    counter = Counter()

    recent = history[:20]

    weight = 20

    for row in recent:

        for n in row["numbers"]:
            counter[n] += weight

        counter[row["special"]] += weight * 0.5

        weight -= 1

    ranked = counter.most_common(top_n)

    return [x[0] for x in ranked]


# =========================================================
# 波色统计
# =========================================================

def predict_wave(history):

    specials = [
        row["special"]
        for row in history[:20]
    ]

    counter = Counter()

    for n in specials:

        counter[get_wave(n)] += 1

    ranked = counter.most_common()

    if ranked:
        return ranked[0][0]

    return "红波"


# =========================================================
# 生成预测
# =========================================================

def generate_prediction(history):

    hot = calc_hot_numbers(history)
    cold = calc_cold_numbers(history)
    recent = calc_recent_numbers(history)

    # -----------------------------------------------------
    # 综合号码池
    # -----------------------------------------------------

    final_pool = list(set(
        hot[:12]
        + cold[:8]
        + recent[:10]
    ))

    while len(final_pool) < 25:

        n = random.randint(1, 49)

        if n not in final_pool:
            final_pool.append(n)

    random.shuffle(final_pool)

    main_numbers = sorted(final_pool[:6])

    remain = [
        x for x in ALL_NUMBERS
        if x not in main_numbers
    ]

    special = random.choice(remain)

    return {
        "main": main_numbers,
        "special": special,
        "wave": predict_wave(history),
        "hot": hot[:10],
        "cold": cold[:10],
        "recent": recent[:10]
    }


# =========================================================
# 输出
# =========================================================

def print_result(lottery_name, history, result):

    latest = history[0]

    print("\n")
    print("=" * 60)
    print(f"🎯 {lottery_name} 智能预测")
    print("=" * 60)

    print("\n【最新期开奖】")

    latest_nums = (
        " ".join(
            str(x).zfill(2)
            for x in latest["numbers"]
        )
    )

    print(
        f"{latest['issue']}期 "
        f"{latest_nums} "
        f"+ {str(latest['special']).zfill(2)}"
    )

    print("\n【推荐六肖】")

    print(
        " ".join(
            str(x).zfill(2)
            for x in result["main"]
        )
    )

    print("\n【特别号】")

    print(
        f"{str(result['special']).zfill(2)} "
        f"({get_wave(result['special'])})"
    )

    print("\n【推荐波色】")

    print(result["wave"])

    print("\n【热号参考】")

    print(
        " ".join(
            str(x).zfill(2)
            for x in result["hot"]
        )
    )

    print("\n【冷号参考】")

    print(
        " ".join(
            str(x).zfill(2)
            for x in result["cold"]
        )
    )

    print("\n【近期动量】")

    print(
        " ".join(
            str(x).zfill(2)
            for x in result["recent"]
        )
    )

    print("\n生成时间:")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    print("=" * 60)
    print("\n")


# =========================================================
# 主运行
# =========================================================

def run_predict(lottery_name):

    history = fetch_real_history(lottery_name)

    result = generate_prediction(history)

    print_result(
        lottery_name,
        history,
        result
    )


# =========================================================
# main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--lottery",
        required=True,
        choices=[
            "香港彩",
            "新澳门彩",
            "老澳门彩"
        ]
    )

    args = parser.parse_args()

    run_predict(args.lottery)


if __name__ == "__main__":
    main()