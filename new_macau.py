# -*- coding: utf-8 -*-
# 新澳门六合彩 智能预测系统（最终完整版）
# 支持:
# 1. 新澳门真实数据在线同步
# 2. 最近10期真实回测
# 3. 波色二中一预测
# 4. 大小单双预测
# 5. 最大连空统计
# 6. 多策略融合
# 7. 特码属性分析
# 8. GitHub Actions 直接运行

import json
import random
import sqlite3
import sys
import ssl
import urllib.request
from collections import Counter

DB_FILE = "new_macau.db"

# =========================
# 波色
# =========================

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

# 五行
ELEMENT = {
    "金": [5,6,13,14,21,22,35,36,43,44],
    "木": [3,4,17,18,25,26,33,34,47,48],
    "水": [1,2,15,16,23,24,37,38,45,46],
    "火": [7,8,19,20,27,28,41,42,49],
    "土": [9,10,11,12,29,30,31,32,39,40]
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
# 获取真实数据
# =========================

def fetch_data():

    urls = [
        "https://www.macaumarksix.com/api/macaujc/history",
        "https://www.macaumarksix.com/api/history",
        "https://api.macaumarksix.com/api/history",
        "https://marksix6.net/index.php?api=1"
    ]

    ctx = ssl._create_unverified_context()

    for url in urls:

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            res = urllib.request.urlopen(
                req,
                timeout=20,
                context=ctx
            )

            text = res.read().decode("utf-8", "ignore")

            print(f"网页解析成功: {url}")

            # marksix6
            if "marksix" in text.lower():

                data = json.loads(text)

                records = []

                for item in data:

                    nums = item.get("numbers", [])

                    if len(nums) < 7:
                        continue

                    records.append({
                        "issue": str(item.get("expect")),
                        "numbers": nums[:6],
                        "special": nums[6]
                    })

                return records

        except Exception as e:
            print(f"数据源失败: {url} -> {e}")

    return []

# =========================
# 保存
# =========================

def save_records(records):

    conn = sqlite3.connect(DB_FILE)

    new_count = 0

    for r in records:

        issue = r["issue"]

        exists = conn.execute(
            "SELECT issue FROM lottery WHERE issue=?",
            (issue,)
        ).fetchone()

        if exists:
            continue

        nums = r["numbers"]

        conn.execute("""
        INSERT INTO lottery
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            issue,
            nums[0],
            nums[1],
            nums[2],
            nums[3],
            nums[4],
            nums[5],
            r["special"]
        ))

        new_count += 1

    conn.commit()
    conn.close()

    return new_count

# =========================
# 获取历史
# =========================

def load_history(limit=120):

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute(f"""
    SELECT *
    FROM lottery
    ORDER BY issue DESC
    LIMIT {limit}
    """).fetchall()

    conn.close()

    data = []

    for r in rows:

        data.append({
            "issue": r[0],
            "numbers": [r[1], r[2], r[3], r[4], r[5], r[6]],
            "special": r[7]
        })

    return data

# =========================
# 波色
# =========================

def get_color(n):

    if n in RED:
        return "红"

    if n in BLUE:
        return "蓝"

    return "绿"

# =========================
# 五行
# =========================

def get_element(n):

    for k, arr in ELEMENT.items():

        if n in arr:
            return k

    return "?"

# =========================
# 属性
# =========================

def special_attrs(n):

    ds = "单" if n % 2 else "双"

    dx = "大" if n >= 25 else "小"

    he = sum(map(int, str(n)))

    hds = "合单" if he % 2 else "合双"

    hdx = "大" if he >= 7 else "小"

    tail = n % 10

    wdx = "尾大" if tail >= 5 else "尾小"

    color = get_color(n)

    element = get_element(n)

    return f"{ds}/{dx} {hds}/{hdx} {wdx} {color} {element}"

# =========================
# 热号
# =========================

def hot_strategy(hist):

    c = Counter()

    for r in hist[:20]:
        c.update(r["numbers"])

    nums = [x for x, _ in c.most_common(6)]

    sp = nums[0]

    return nums, sp

# =========================
# 冷号
# =========================

def cold_strategy(hist):

    c = Counter()

    for r in hist[:50]:
        c.update(r["numbers"])

    nums = [x for x, _ in c.most_common()[-6:]]

    sp = nums[0]

    return nums, sp

# =========================
# 动量
# =========================

def momentum_strategy(hist):

    recent = hist[:10]

    c = Counter()

    for r in recent:
        c.update(r["numbers"])

    nums = [x for x, _ in c.most_common(6)]

    sp = nums[0]

    return nums, sp

# =========================
# 规律挖掘
# =========================

def pattern_strategy(hist):

    latest = hist[0]["numbers"]

    nums = sorted(latest[:5] + [random.randint(1,49)])

    sp = latest[0]

    return nums[:6], sp

# =========================
# 组合策略
# =========================

def combo_strategy(hist):

    h,_ = hot_strategy(hist)

    c,_ = cold_strategy(hist)

    nums = list(dict.fromkeys(h[:3] + c[:3]))

    while len(nums) < 6:
        n = random.randint(1,49)

        if n not in nums:
            nums.append(n)

    sp = nums[0]

    return nums[:6], sp

# =========================
# 集成投票
# =========================

def vote_strategy(hist):

    all_nums = []

    for fn in [
        hot_strategy,
        cold_strategy,
        momentum_strategy,
        pattern_strategy,
        combo_strategy
    ]:

        n,_ = fn(hist)

        all_nums.extend(n)

    c = Counter(all_nums)

    nums = [x for x,_ in c.most_common(6)]

    sp = nums[0]

    return nums, sp

# =========================
# 波色预测
# =========================

def predict_color(hist):

    recent = hist[:10]

    score = {
        "红":0,
        "蓝":0,
        "绿":0
    }

    weight = 10

    for r in recent:

        color = get_color(r["special"])

        score[color] += weight

        weight -= 1

    sorted_colors = sorted(
        score.items(),
        key=lambda x:x[1],
        reverse=True
    )

    return (
        sorted_colors[0][0],
        sorted_colors[1][0],
        sorted_colors[0][1],
        sorted_colors[1][1]
    )

# =========================
# 大小单双
# =========================

def predict_dxds(hist):

    recent = hist[:10]

    big = 0
    small = 0
    odd = 0
    even = 0

    for r in recent:

        n = r["special"]

        if n >= 25:
            big += 1
        else:
            small += 1

        if n % 2:
            odd += 1
        else:
            even += 1

    dx = "大" if big >= small else "小"

    ds = "单" if odd >= even else "双"

    return dx, ds

# =========================
# 回测
# =========================

def backtest_color(hist):

    hit = 0
    total = 0
    max_miss = 0
    miss = 0

    for i in range(10,0,-1):

        future = hist[i-1]

        train = hist[i:i+10]

        if len(train) < 10:
            continue

        c1,c2,_,_ = predict_color(train)

        real = get_color(future["special"])

        total += 1

        if real in [c1,c2]:

            hit += 1

            if miss > max_miss:
                max_miss = miss

            miss = 0

        else:

            miss += 1

    if miss > max_miss:
        max_miss = miss

    rate = round(hit/total*100,1) if total else 0

    return hit, total, rate, max_miss

# =========================
# 历史统计
# =========================

def strategy_stats(hist, fn):

    total_hit = 0
    total_special = 0
    count = 0

    for i in range(10):

        if i+11 >= len(hist):
            continue

        train = hist[i+1:]

        target = hist[i]

        nums,sp = fn(train)

        hit = len(
            set(nums) &
            set(target["numbers"])
        )

        total_hit += hit

        if sp == target["special"]:
            total_special += 1

        count += 1

    avg = round(total_hit/count,1) if count else 0

    rate = round(total_hit/(count*6)*100,1) if count else 0

    sp_rate = round(total_special/count*100,1) if count else 0

    return count, avg, rate, sp_rate

# =========================
# 主逻辑
# =========================

def sync():

    init_db()

    records = fetch_data()

    if not records:
        print("未抓到真实开奖数据")
        return

    new_count = save_records(records)

    hist = load_history(120)

    latest = hist[0]

    next_issue = str(int(latest["issue"]) + 1)

    print(f"数据同步完成: total={len(hist)}, new={new_count}")
    print(f"最新开奖: {latest['issue']} | {' '.join(map(str, latest['numbers']))} + {latest['special']}")
    print()

    print(f"预测期号: {next_issue}")

    strategies = {
        "组合策略": combo_strategy,
        "冷号回补": cold_strategy,
        "集成投票": vote_strategy,
        "热号策略": hot_strategy,
        "近期动量": momentum_strategy,
        "规律挖掘": pattern_strategy
    }

    for name, fn in strategies.items():

        nums, sp = fn(hist)

        print(f"  {name:<12}: {' '.join(f'{x:02d}' for x in nums)} + {sp:02d}")
        print(f"         特码属性: {special_attrs(sp)}")

    print()
    print("历史命中统计:")

    for name, fn in strategies.items():

        count,avg,rate,sp_rate = strategy_stats(hist, fn)

        print(
            f"  {name:<12}: "
            f"期数={count}, "
            f"平均命中={avg}个, "
            f"命中率={rate}%, "
            f"特别号命中率={sp_rate}%"
        )

    print()

    c1,c2,s1,s2 = predict_color(hist)

    print("🎨 特码波色预测（加权频率，基于最近 10 期）：")
    print(f"   主强: {c1} (得分 {s1})   次强: {c2} (得分 {s2})")
    print()

    dx,ds = predict_dxds(hist)

    print("📊 大小单双预测（最近10期真实数据）：")
    print(f"   大小预测: {dx}   单双预测: {ds}")
    print()

    hit,total,rate,max_miss = backtest_color(hist)

    print("📊 历史回测（最近 10 期，方法=weighted，窗口=10）：")
    print(f"   二中一命中率: {rate}%")
    print(f"   最近10期命中: {hit}/{total}")
    print(f"   最大连空: {max_miss}期")

# =========================
# main
# =========================

def main():

    if len(sys.argv) >= 2:

        cmd = sys.argv[1]

        if cmd == "sync":
            sync()
            return

    sync()

if __name__ == "__main__":
    main()