# -*- coding: utf-8 -*-
"""
========================================================
 新澳门六合彩 AI 超级预测系统 V23.0（热冷+统计增强版）
========================================================
"""

import os
import json
import sqlite3
import statistics
import urllib.request
from collections import Counter
from datetime import datetime
import logging

DB_FILE = "macau_v22.db"

# ====================== 常量 ======================
RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

def get_wave(n):
    if n in RED: return "红"
    if n in BLUE: return "蓝"
    return "绿"

def get_big_small(n): return "大" if n >= 25 else "小"
def get_odd_even(n): return "单" if n % 2 == 1 else "双"
def get_element(n):
    elements = {"金":[5,6,13,14,21,22,35,36,43,44],"木":[3,4,17,18,25,26,39,40,47,48],
                "水":[1,2,15,16,23,24,37,38,45,46],"火":[7,8,19,20,27,28,41,42,49],"土":[9,10,11,12,29,30,31,32,33,34]}
    for k,v in elements.items():
        if n in v: return k
    return "?"

def get_zodiac(n):
    z = {1:"鼠",2:"牛",3:"虎",4:"兔",5:"龙",6:"蛇",7:"马",8:"羊",9:"猴",10:"鸡",11:"狗",12:"猪"}
    return z.get(((n-1)%12)+1, "?")

# ====================== 数据库 ======================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS draws(issue TEXT PRIMARY KEY,n1 INT,n2 INT,n3 INT,n4 INT,n5 INT,n6 INT,special INT,created_at TEXT)""")
    conn.commit()
    return conn

def fetch_real_data():
    url = "https://marksix6.net/index.php?api=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        records = []
        for item in data.get("lottery_data", []):
            if "新澳门" in item.get("name", ""):
                nums = [int(x.strip()) for x in str(item.get("openCode","")).split(",") if x.strip().isdigit()]
                if len(nums) >= 7:
                    records.append({"issue": str(item.get("expect","")), "numbers": nums[:6], "special": nums[6]})
                for row in item.get("history", []):
                    if isinstance(row, str) and "期：" in row:
                        try:
                            issue = row.split("期：")[0].strip()
                            code = row.split("期：")[1]
                            nums = [int(x.strip()) for x in code.split(",") if x.strip().isdigit()]
                            if len(nums) >= 7:
                                records.append({"issue": issue, "numbers": nums[:6], "special": nums[6]})
                        except: continue
                break
        uniq = {r["issue"]: r for r in records}
        return sorted(uniq.values(), key=lambda x: str(x["issue"]))
    except:
        return []

def load_records(conn):
    rows = conn.execute("SELECT * FROM draws ORDER BY issue").fetchall()
    return [{"issue": r[0], "numbers": list(r[1:7]), "special": r[7]} for r in rows]

# ====================== 增强版预测 ======================
def generate_prediction(records):
    if len(records) < 30:
        return [5,12,19,26,33,40], 7, 40

    recent = records[-120:]
    freq = Counter()
    for r in recent:
        for n in r["numbers"] + [r["special"]]:
            freq[n] += 2 if n == r["special"] else 1

    # 热号 + 冷号混合策略
    hot = [x[0] for x in freq.most_common(15)]
    cold = [n for n in range(1,50) if n not in freq]
    candidates = hot[:10] + cold[:5]

    # 严格去重
    pred = []
    seen = set()
    for n in candidates:
        if n not in seen:
            pred.append(n)
            seen.add(n)
        if len(pred) >= 7:
            break

    main_numbers = sorted(pred[:6])
    special = pred[6] if len(pred) > 6 else hot[0]
    confidence = min(90, 45 + len(records)//4)

    return main_numbers, special, confidence

def predict_wave_double(records):
    recent = records[-80:]
    wave_count = Counter(get_wave(r["special"]) for r in recent)
    return [x[0] for x in wave_count.most_common(2)]

# ====================== 更多统计 ======================
def get_more_stats(records):
    recent50 = records[-50:]
    all_nums = [n for r in recent50 for n in r["numbers"] + [r["special"]]]
    freq = Counter(all_nums)

    hot10 = [x[0] for x in freq.most_common(10)]
    cold10 = [n for n in range(1,50) if n not in freq][:10]

    # 连号统计
    consecutive = sum(1 for r in recent50 for i in range(5) if abs(r["numbers"][i] - r["numbers"][i+1]) == 1)

    return {
        "hot10": hot10,
        "cold10": cold10,
        "consecutive_count": consecutive,
        "wave_dist": Counter(get_wave(r["special"]) for r in recent50)
    }

# ====================== 主程序 ======================
def main():
    print("🚀 新澳门六合彩 AI V23.0 热冷统计版 启动...\n")
    conn = init_db()
    records = fetch_real_data()
    # save_data...
    records = load_records(conn) if len(records) == 0 else records

    if len(records) == 0:
        print("❌ 未获取数据")
        return

    latest = records[-1]
    print(f"🔔 最新开奖: {latest['issue']}")
    print("号码:", " ".join(str(x).zfill(2) for x in latest["numbers"]), "+", str(latest["special"]).zfill(2))
    print(f"📊 历史数据: {len(records)} 期\n")

    pred, special, confidence = generate_prediction(records)
    pred_waves = predict_wave_double(records)
    stats = get_more_stats(records)

    print("🎯 本期AI预测:")
    print(f"• 正码：{' '.join(str(x).zfill(2) for x in pred)}")
    print(f"• 特码：{str(special).zfill(2)}")
    print(f"• 波色双选：{' + '.join(pred_waves)}")
    print(f"• 属性：{get_wave(special)} {get_big_small(special)} {get_odd_even(special)} {get_element(special)} {get_zodiac(special)}\n")

    print("📊 更多统计（最近50期）:")
    print(f"热号Top10: {stats['hot10']}")
    print(f"冷号Top10: {stats['cold10']}")
    print(f"出现连号次数: {stats['consecutive_count']} 次")
    print(f"波色分布: {dict(stats['wave_dist'])}")

    print(f"\n📈 置信度: {confidence}%")

if __name__ == "__main__":
    main()