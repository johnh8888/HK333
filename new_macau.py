# -*- coding: utf-8 -*-

import requests
import sqlite3
import random
from collections import Counter

DB_FILE = "new_macau.db"

RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ELEMENTS = {
    "金":[1,2,15,16,29,30,43,44],
    "木":[5,6,19,20,33,34,47,48],
    "水":[9,10,23,24,37,38],
    "火":[13,14,27,28,41,42],
    "土":[3,4,17,18,31,32,45,46]
}

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        period TEXT PRIMARY KEY,
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

    return conn

def get_color(num):

    if num in RED:
        return "红"

    if num in BLUE:
        return "蓝"

    return "绿"

def get_element(num):

    for k,v in ELEMENTS.items():

        if num in v:
            return k

    return "土"

def get_big_small(num):

    return "大" if num >= 25 else "小"

def get_odd_even(num):

    return "双" if num % 2 == 0 else "单"

def fetch_real_draw():

    urls = [
        "https://api3.marksix6.net/lottery_api.php?type=newMacau",
        "https://marksix6.net/index.php?api=1"
    ]

    headers = {
        "User-Agent":"Mozilla/5.0"
    }

    for url in urls:

        try:

            r = requests.get(
                url,
                headers=headers,
                timeout=20
            )

            r.raise_for_status()

            data = r.json()

            # api3
            if isinstance(data, dict):

                if "openCode" in data:

                    expect = str(data.get("expect",""))

                    code = str(data.get("openCode",""))

                    nums = [
                        int(x)
                        for x in code.replace("+",",").split(",")
                        if x.strip().isdigit()
                    ]

                    if len(nums) >= 7:

                        return {
                            "expect": expect,
                            "numbers": nums[:6],
                            "special": nums[6]
                        }

                # api=1
                if "lottery_data" in data:

                    for item in data["lottery_data"]:

                        name = item.get("name","")

                        if "新澳门" in name:

                            expect = str(item.get("expect",""))

                            code = str(item.get("openCode",""))

                            nums = [
                                int(x)
                                for x in code.replace("+",",").split(",")
                                if x.strip().isdigit()
                            ]

                            if len(nums) >= 7:

                                return {
                                    "expect": expect,
                                    "numbers": nums[:6],
                                    "special": nums[6]
                                }

        except Exception as e:

            print(f"接口失败: {url}")
            continue

    return None

def save_draw(conn, draw):

    conn.execute("""
    INSERT OR REPLACE INTO draws
    VALUES (?,?,?,?,?,?,?,?)
    """,(
        draw["expect"],
        draw["numbers"][0],
        draw["numbers"][1],
        draw["numbers"][2],
        draw["numbers"][3],
        draw["numbers"][4],
        draw["numbers"][5],
        draw["special"]
    ))

    conn.commit()

def load_history(conn):

    rows = conn.execute("""
    SELECT * FROM draws
    ORDER BY period DESC
    LIMIT 80
    """).fetchall()

    return rows

def hot_strategy(rows):

    nums=[]

    for r in rows[:20]:
        nums.extend(r[1:7])

    hot=[x for x,_ in Counter(nums).most_common(6)]

    special=random.choice(hot)

    return hot,special

def cold_strategy(rows):

    nums=[]

    for r in rows[:40]:
        nums.extend(r[1:7])

    c=Counter(nums)

    cold=sorted(range(1,50), key=lambda x:c[x])[:6]

    special=random.choice(cold)

    return cold,special

def momentum_strategy(rows):

    nums=[]

    for r in rows[:10]:
        nums.extend(r[1:7])

    top=[x for x,_ in Counter(nums).most_common(6)]

    special=rows[0][7]

    return top,special

def balanced_strategy(rows):

    h,_=hot_strategy(rows)
    c,_=cold_strategy(rows)

    nums=list(dict.fromkeys(h[:3]+c[:3]))[:6]

    while len(nums)<6:

        n=random.randint(1,49)

        if n not in nums:
            nums.append(n)

    special=random.randint(1,49)

    return nums,special

def ensemble_strategy(rows):

    nums=[]

    for s in [
        hot_strategy,
        cold_strategy,
        momentum_strategy
    ]:

        n,_=s(rows)

        nums.extend(n)

    top=[x for x,_ in Counter(nums).most_common(6)]

    special=random.randint(1,49)

    return top,special

def pattern_strategy(rows):

    nums=[]

    for r in rows[:15]:

        nums.extend(r[1:4])

    top=[x for x,_ in Counter(nums).most_common(6)]

    while len(top)<6:

        n=random.randint(1,49)

        if n not in top:
            top.append(n)

    special=random.randint(1,49)

    return top,special

def print_strategy(name, nums, special):

    print(
        f"{name:<10}: "
        + " ".join(f"{x:02d}" for x in nums)
        + f" + {special:02d}"
    )

    print(
        f"特码属性: "
        f"{get_odd_even(special)}/"
        f"{get_big_small(special)} "
        f"{get_color(special)} "
        f"{get_element(special)}"
    )

def max_miss_color(rows):

    colors=[get_color(r[7]) for r in rows[:10]]

    result={}

    for c in ["红","蓝","绿"]:

        miss=0
        maxmiss=0

        for x in colors:

            if x != c:

                miss += 1

            else:

                maxmiss=max(maxmiss,miss)

                miss=0

        maxmiss=max(maxmiss,miss)

        result[c]=maxmiss

    return result

def recent_hit(rows, strategy_func):

    hit=0
    total=0

    for i in range(1,10):

        past=rows[i:]

        nums,_=strategy_func(past)

        real=rows[i-1][1:7]

        h=len(set(nums)&set(real))

        hit += h

        total += 1

    if total == 0:
        return 0

    return round(hit/total,2)

def sync():

    conn=init_db()

    draw=fetch_real_draw()

    if not draw:

        print("未抓到真实开奖数据")

        return

    save_draw(conn,draw)

    rows=load_history(conn)

    print(f"同步完成: {len(rows)} 条")

    print()

    latest=rows[0]

    print("最新开奖:")

    print(
        f"{latest[0]} | "
        + " ".join(f"{x:02d}" for x in latest[1:7])
        + f" + {latest[7]:02d}"
    )

    print()

    next_period=str(int(latest[0])+1)

    print(f"预测期号: {next_period}")

    strategies = {
        "组合策略": balanced_strategy,
        "热号策略": hot_strategy,
        "冷号回补": cold_strategy,
        "近期动量": momentum_strategy,
        "集成投票": ensemble_strategy,
        "规律挖掘": pattern_strategy
    }

    for name,func in strategies.items():

        nums,special=func(rows)

        print_strategy(name,nums,special)

    print()
    print("特码波色预测:")

    colors=[get_color(r[7]) for r in rows[:10]]

    top=Counter(colors).most_common(2)

    if len(top) >= 2:

        print(f"主强: {top[0][0]} 次强: {top[1][0]}")

    print()
    print("大小单双预测:")

    bs=Counter(
        get_big_small(r[7])
        for r in rows[:10]
    ).most_common(1)[0][0]

    oe=Counter(
        get_odd_even(r[7])
        for r in rows[:10]
    ).most_common(1)[0][0]

    print(f"大小: {bs}")
    print(f"单双: {oe}")

    print()
    print("最大连空:")

    miss=max_miss_color(rows)

    for k,v in miss.items():

        print(f"{k}波: {v}期")

    print()
    print("推荐投注方案:")

    print(f"{top[0][0]}: 450 元")

    if len(top) >= 2:
        print(f"{top[1][0]}: 150 元")

    print(f"{bs}: 200 元")
    print(f"{oe}: 200 元")

    print()
    print("赔率参考:")

    print("红波: 2.7")
    print("蓝/绿波: 2.8")
    print("大小: 1.95")
    print("单双: 1.95")

    print()
    print("最近10期历史命中统计:")

    for name,func in strategies.items():

        avg=recent_hit(rows,func)

        print(
            f"{name:<10}: "
            f"期数=9 "
            f"平均命中={avg}"
        )

if __name__ == "__main__":

    sync()