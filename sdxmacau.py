#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import random
import urllib.request
from collections import Counter

# =========================================================
# 最新稳定版
# 支持：
# - 香港彩
# - 新澳门彩
# - 老澳门彩
# 使用最新 api3.marksix6.net 数据源
# =========================================================

LOTTERY_URLS = {
    "香港彩": "https://api3.marksix6.net/lottery_api.php?type=hk",
    "新澳门彩": "https://api3.marksix6.net/lottery_api.php?type=newMacau",
    "老澳门彩": "https://api3.marksix6.net/lottery_api.php?type=oldMacau",
}

ALL_NUMBERS = list(range(1, 50))


# =========================================================
# 获取真实历史数据
# =========================================================
def fetch_real_history(lottery_name):

    if lottery_name not in LOTTERY_URLS:
        raise Exception(f"未知彩种: {lottery_name}")

    url = LOTTERY_URLS[lottery_name]

    print("=" * 60)
    print(lottery_name)
    print(f"正在请求数据源: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    req = urllib.request.Request(url, headers=headers)

    try:

        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")

        data = json.loads(raw)

    except Exception as e:
        raise Exception(f"接口请求失败: {e}")

    history = []

    # -----------------------------------------------------
    # 新接口格式解析
    # -----------------------------------------------------
    if isinstance(data, list):

        for item in data:

            try:

                nums = []

                # 自动识别字段
                for key in [
                    "openCode",
                    "code",
                    "numbers",
                    "num"
                ]:

                    if key in item:

                        value = item[key]

                        # 字符串格式
                        if isinstance(value, str):

                            value = (
                                value.replace("+", ",")
                                .replace(" ", ",")
                            )

                            nums = []

                            for x in value.split(","):

                                x = x.strip()

                                if x.isdigit():
                                    nums.append(int(x))

                        # list格式
                        elif isinstance(value, list):

                            nums = [
                                int(x)
                                for x in value
                            ]

                        break

                # 必须7个号码
                if len(nums) < 7:
                    continue

                issue = str(
                    item.get("expect")
                    or item.get("issue")
                    or item.get("period")
                    or item.get("turnNum")
                    or ""
                )

                history.append({
                    "issue": issue,
                    "numbers": nums[:6],
                    "special": nums[6]
                })

            except:
                continue

    # -----------------------------------------------------
    # 检查数据
    # -----------------------------------------------------
    if not history:
        raise Exception("无法获取真实数据")

    print(f"成功获取 {len(history)} 条历史数据")

    return history


# =========================================================
# 热号统计
# =========================================================
def calc_hot_numbers(history, top_n=15):

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
def calc_cold_numbers(history, top_n=15):

    counter = Counter()

    for row in history:

        for n in row["numbers"]:
            counter[n] += 1

        counter[row["special"]] += 0.5

    ranked = sorted(counter.items(), key=lambda x: x[1])

    return [x[0] for x in ranked[:top_n]]


# =========================================================
# 预测生成
# =========================================================
def generate_prediction(history):

    hot = calc_hot_numbers(history, 20)
    cold = calc_cold_numbers(history, 20)

    final_pool = list(set(hot[:12] + cold[:8]))

    while len(final_pool) < 20:

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
        "hot": hot[:10],
        "cold": cold[:10]
    }


# =========================================================
# 波色
# =========================================================
def get_color(num):

    red = {
        1, 2, 7, 8, 12, 13, 18, 19,
        23, 24, 29, 30, 34, 35,
        40, 45, 46
    }

    blue = {
        3, 4, 9, 10, 14, 15, 20,
        25, 26, 31, 36, 37,
        41, 42, 47, 48
    }

    green = {
        5, 6, 11, 16, 17, 21, 22,
        27, 28, 32, 33, 38, 39,
        43, 44, 49
    }

    if num in red:
        return "红波"

    if num in blue:
        return "蓝波"

    return "绿波"


# =========================================================
# 输出结果
# =========================================================
def print_result(lottery_name, result):

    print("\n" + "=" * 60)
    print(f"🎯 {lottery_name} 智能预测")
    print("=" * 60)

    print("\n【推荐六肖】")
    print(
        " ".join(
            [str(x).zfill(2) for x in result["main"]]
        )
    )

    print("\n【特别号】")
    print(
        f"{str(result['special']).zfill(2)} "
        f"({get_color(result['special'])})"
    )

    print("\n【热号参考】")
    print(
        " ".join(
            [str(x).zfill(2) for x in result["hot"]]
        )
    )

    print("\n【冷号参考】")
    print(
        " ".join(
            [str(x).zfill(2) for x in result["cold"]]
        )
    )

    print("\n")


# =========================================================
# 主逻辑
# =========================================================
def run_predict(lottery_name):

    history = fetch_real_history(lottery_name)

    result = generate_prediction(history)

    print_result(lottery_name, result)


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