# -*- coding: utf-8 -*-

import os
import re
import ssl
import json
import sqlite3
import urllib.request
from collections import Counter
from datetime import datetime, timezone

DB_FILE = "new_macau.db"

# =========================
# 波色
# =========================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35,
    40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42,
    47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}

# =========================
# 五行
# =========================

ELEMENTS = {
    "金": {5, 6, 13, 14, 21, 22, 35, 36, 43, 44},
    "木": {3, 4, 17, 18, 25, 26, 33, 34, 47, 48},
    "水": {1, 2, 15, 16, 23, 24, 37, 38, 45, 46},
    "火": {7, 8, 19, 20, 27, 28, 41, 42, 49},
    "土": {9, 10, 11, 12, 29, 30, 31, 32, 39, 40},
}

# =========================
# 数据库
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        issue TEXT PRIMARY KEY,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER
    )
    """)
    conn.commit()
    conn.close()

# =========================
# 获取数据（增强鲁棒性）
# =========================

def fetch_data():
    """
    从 marksix6.net 拉取最新开奖数据（通常返回最近120期）
    失败时打印详细错误并返回空列表
    """
    url = "https://marksix6.net/index.php?api=1"
    ctx = ssl._create_unverified_context()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        response = urllib.request.urlopen(req, timeout=20, context=ctx)
        text = response.read().decode("utf-8", errors="ignore")
        data = json.loads(text)

        rows = []
        for item in data:
            issue = str(
                item.get("expect") or
                item.get("period") or
                item.get("issue") or ""
            )
            opencode = (
                item.get("opencode") or
                item.get("openCode") or ""
            )
            nums = re.findall(r"\d+", opencode)
            if len(nums) < 7:
                continue
            nums = list(map(int, nums[:7]))
            rows.append({"issue": issue, "nums": nums})

        rows.sort(key=lambda x: int(x["issue"]))
        rows = rows[-120:]  # 保留最近120期，足够策略分析

        if rows:
            print(f"✅ 网页解析成功: {url}，获取到 {len(rows)} 条记录")
            return rows

    except Exception as e:
        print(f"❌ 数据源失败: {url} -> {e}")
    return []

# =========================
# 保存数据（去重）
# =========================

def save_records(rows):
    conn = sqlite3.connect(DB_FILE)
    new_count = 0
    for row in rows:
        issue = row["issue"]
        nums = row["nums"]
        exists = conn.execute("SELECT issue FROM lottery WHERE issue=?", (issue,)).fetchone()
        if exists:
            continue
        conn.execute("""
        INSERT INTO lottery VALUES(?,?,?,?,?,?,?,?)
        """, (issue, nums[0], nums[1], nums[2], nums[3], nums[4], nums[5], nums[6]))
        new_count += 1
    conn.commit()
    conn.close()
    return new_count

# =========================
# 获取历史
# =========================

def get_history(limit=120):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(f"""
    SELECT * FROM lottery
    ORDER BY issue ASC
    LIMIT {limit}
    """).fetchall()
    conn.close()
    return rows

# =========================
# 波色/五行/属性
# =========================

def get_color(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_element(n):
    for k, v in ELEMENTS.items():
        if n in v:
            return k
    return "?"

def get_attrs(n):
    ds = "单" if n % 2 else "双"
    dx = "大" if n >= 25 else "小"
    hs = sum(map(int, str(n)))
    hds = "合单" if hs % 2 else "合双"
    hdx = "大" if hs >= 7 else "小"
    tail = n % 10
    tw = "尾大" if tail >= 5 else "尾小"
    color = get_color(n)
    element = get_element(n)
    return f"{ds}/{dx} {hds}/{hdx} {tw} {color} {element}"

# =========================
# 选号策略（增加空数据保护）
# =========================

def hot_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-20:]:
        nums = r[1:7]
        c.update(nums)
    if not c:
        return None, None
    main = [x for x, _ in c.most_common(6)]
    sp = c.most_common(1)[0][0]
    return main, sp

def cold_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-30:]:
        nums = r[1:7]
        c.update(nums)
    all_nums = set(range(1, 50))
    miss = list(all_nums - set(c.keys()))
    miss.sort()
    main = miss[:6]
    while len(main) < 6:
        main.append(len(main) + 1)
    sp = main[0]
    return main, sp

def momentum_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-10:]:
        nums = r[1:7]
        c.update(nums)
    if not c:
        return None, None
    main = [x for x, _ in c.most_common(6)]
    sp = main[0]
    return main, sp

def vote_strategy(hist):
    a, _ = hot_strategy(hist) or (None, None)
    b, _ = momentum_strategy(hist) or (None, None)
    if not a or not b:
        return None, None
    c = Counter(a + b)
    main = [x for x, _ in c.most_common(6)]
    sp = main[0]
    return main, sp

def pattern_strategy(hist):
    if len(hist) < 1:
        return None, None
    latest = hist[-1][1:7]
    main = list(latest[:6])
    sp = latest[0]
    return main, sp

# =========================
# 波色预测
# =========================

def color_predict(hist):
    score = {"红": 0, "蓝": 0, "绿": 0}
    recent = hist[-10:]
    if len(recent) < 10:
        return []
    weight = 10
    for r in recent:
        sp = r[7]
        color = get_color(sp)
        score[color] += weight
        weight -= 1
    return sorted(score.items(), key=lambda x: x[1], reverse=True)

# =========================
# 大小单双
# =========================

def dsdx_predict(hist):
    recent = hist[-10:]
    if len(recent) < 10:
        return "数据不足", "数据不足"
    big = small = odd = even = 0
    for r in recent:
        sp = r[7]
        if sp >= 25:
            big += 1
        else:
            small += 1
        if sp % 2:
            odd += 1
        else:
            even += 1
    dx = "大" if big >= small else "小"
    ds = "单" if odd >= even else "双"
    return dx, ds

# =========================
# 回测
# =========================

def backtest(hist):
    recent = hist[-11:]
    if len(recent) < 11:
        return 0, 0, 0, 0
    hit = total = max_miss = miss = 0
    for i in range(1, len(recent)):
        train = recent[:i]
        target = recent[i]
        top = color_predict(train)
        if len(top) < 2:
            continue
        a, b = top[0][0], top[1][0]
        real = get_color(target[7])
        total += 1
        if real in [a, b]:
            hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)
    rate = round(hit / total * 100, 1) if total else 0
    return hit, total, rate, max_miss

# =========================
# 打印策略
# =========================

def show_strategy(name, main, sp):
    if main is None:
        print(f"{name:<16}: 数据不足，无法生成推荐")
        return
    print(f"{name:<16}: " + " ".join(f"{x:02d}" for x in main) + f" + {sp:02d}")
    print(f"{'':16} 特码属性: {get_attrs(sp)}")

# =========================
# 主流程
# =========================

def sync():
    init_db()

    # 1. 尝试在线拉取数据
    rows = fetch_data()
    if rows:
        new_count = save_records(rows)
        print(f"📦 本次保存 {new_count} 条新记录")
    else:
        print("⚠️ 未获取到在线数据，将使用本地数据库进行分析")

    # 2. 获取历史记录
    hist = get_history()
    total = len(hist)
    print(f"📊 当前数据库共有 {total} 期数据")

    if total == 0:
        print("❌ 数据库无任何开奖记录，无法生成预测")
        return

    # 3. 自动补全建议（首次运行后通常已有足够数据）
    if total < 10:
        print("🔍 历史数据不足10期，预测准确性可能较低，请等待定时任务自动积累。")

    # 4. 输出最新开奖及预测
    latest = hist[-1]
    issue_next = int(latest[0]) + 1

    print("\n最新开奖:")
    print(f"{latest[0]} | " +
          " ".join(f"{x:02d}" for x in latest[1:7]) +
          f" + {latest[7]:02d}")
    print(f"\n预测期号: {issue_next}")

    # 各策略推荐
    h_main, h_sp = hot_strategy(hist)
    c_main, c_sp = cold_strategy(hist)
    m_main, m_sp = momentum_strategy(hist)
    v_main, v_sp = vote_strategy(hist)
    p_main, p_sp = pattern_strategy(hist)

    show_strategy("组合策略 (投票)", v_main, v_sp)
    show_strategy("冷号回补", c_main, c_sp)
    show_strategy("热号策略", h_main, h_sp)
    show_strategy("近期动量", m_main, m_sp)
    show_strategy("规律挖掘", p_main, p_sp)

    # 波色预测
    print("\n🎨 特码波色预测（加权频率，基于最近10期）：")
    top = color_predict(hist)
    if top:
        print(f"   主强: {top[0][0]} (得分 {top[0][1]})   "
              f"次强: {top[1][0]} (得分 {top[1][1]})")
    else:
        print("   数据不足，无法预测")

    # 大小单双
    dx, ds = dsdx_predict(hist)
    print("\n📊 大小单双预测（最近10期真实数据）：")
    print(f"   大小预测: {dx}   单双预测: {ds}")

    # 回测
    hit, total_bt, rate, max_miss = backtest(hist)
    if total_bt > 0:
        print("\n📈 历史回测（最近10期）：")
        print(f"   二中一命中率: {rate}%")
        print(f"   最近10期命中: {hit}/{total_bt}")
        print(f"   最大连空: {max_miss}期")
    else:
        print("\n📈 历史回测：数据不足，无法计算")

if __name__ == "__main__":
    sync()