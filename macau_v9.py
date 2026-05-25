# -*- coding: utf-8 -*-
"""
========================================================
 新澳门六合彩 AI 超级预测系统 V22.9（去重修复版）
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
LOG_FILE = "prediction_v22.log"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', encoding='utf-8')

# ====================== 常量 ======================
RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ELEMENTS = {"金":[5,6,13,14,21,22,35,36,43,44],"木":[3,4,17,18,25,26,39,40,47,48],
            "水":[1,2,15,16,23,24,37,38,45,46],"火":[7,8,19,20,27,28,41,42,49],
            "土":[9,10,11,12,29,30,31,32,33,34]}

ZODIAC = {1:"鼠",2:"牛",3:"虎",4:"兔",5:"龙",6:"蛇",7:"马",8:"羊",9:"猴",10:"鸡",11:"狗",12:"猪"}

def get_wave(n):
    if n in RED: return "红"
    if n in BLUE: return "蓝"
    return "绿"

def get_element(n):
    for k, v in ELEMENTS.items():
        if n in v: return k
    return "?"

def get_zodiac(n):
    return ZODIAC.get(((n-1) % 12) + 1, "?")

def get_big_small(n):
    return "大" if n >= 25 else "小"

def get_odd_even(n):
    return "单" if n % 2 == 1 else "双"

# ====================== 数据库 ======================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS draws(
        issue TEXT PRIMARY KEY, n1 INT, n2 INT, n3 INT, n4 INT, n5 INT, n6 INT, 
        special INT, created_at TEXT)""")
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
                        except:
                            continue
                break

        uniq = {r["issue"]: r for r in records}
        return sorted(uniq.values(), key=lambda x: str(x["issue"]))
    except Exception as e:
        print(f"❌ 数据获取失败: {e}")
        return []

def save_data(conn, records):
    new_count = 0
    for r in records:
        if not conn.execute("SELECT 1 FROM draws WHERE issue=?", (r["issue"],)).fetchone():
            new_count += 1
        conn.execute("INSERT OR REPLACE INTO draws VALUES(?,?,?,?,?,?,?,?,?)",
                     (r["issue"], *r["numbers"], r["special"], datetime.now().isoformat()))
    conn.commit()
    print(f"💾 数据库更新: 新增 {new_count} 条")

def load_records(conn):
    rows = conn.execute("SELECT * FROM draws ORDER BY issue").fetchall()
    return [{"issue": r[0], "numbers": list(r[1:7]), "special": r[7]} for r in rows]

# ====================== 修复后的预测（增加去重） ======================
def generate_prediction(records):
    freq = Counter()
    for r in records[-150:]:
        for n in r["numbers"] + [r["special"]]:
            freq[n] += 2 if n == r["special"] else 1

    # 取出候选号码并排序
    candidates = [x[0] for x in freq.most_common(20)]

    # 选6个正码 + 1个特码，保证不重复
    pred = []
    seen = set()
    for n in candidates:
        if n not in seen:
            pred.append(n)
            seen.add(n)
        if len(pred) >= 7:
            break

    main_numbers = pred[:6]
    special = pred[6] if len(pred) > 6 else candidates[0]

    confidence = min(88, 40 + len(records)//3)
    return main_numbers, special, confidence

# ====================== 波色双选（针对特码） ======================
def predict_wave_double(records):
    recent = records[-80:]
    wave_count = Counter(get_wave(r["special"]) for r in recent)
    top2 = [x[0] for x in wave_count.most_common(2)]
    if len(top2) < 2:
        top2 = ["绿", "红"]
    return top2

# ====================== 回测 ======================
def walk_forward_backtest(records):
    if len(records) < 20:
        return {"recent10": [], "special_rate": 0, "wave_rate": 0, "max_miss": 0, "max_wave_miss": 0, "avg_hit": 0}

    hits = []
    special_hits = 0
    wave_hits = 0
    miss = 0
    wave_miss = 0
    max_miss = 0
    max_wave_miss = 0
    recent10 = []

    start = max(0, len(records) - 11)
    for i in range(start, len(records)-1):
        train = records[:i]
        real = records[i]
        pred_nums, pred_sp, _ = generate_prediction(train)
        pred_waves = predict_wave_double(train)

        hit_count = len(set(pred_nums) & set(real["numbers"]))
        real_wave = get_wave(real["special"])

        if pred_sp == real["special"]:
            special_hits += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)

        if real_wave in pred_waves:
            wave_hits += 1
            wave_miss = 0
        else:
            wave_miss += 1
            max_wave_miss = max(max_wave_miss, wave_miss)

        hits.append(hit_count)

        if len(recent10) < 10:
            recent10.append({
                "issue": real["issue"],
                "pred_nums": pred_nums,
                "pred_special": pred_sp,
                "pred_waves": pred_waves,
                "real_special": real["special"],
                "real_wave": real_wave,
                "hit": hit_count,
                "wave_hit": "中" if real_wave in pred_waves else "空"
            })

    return {
        "recent10": recent10,
        "special_rate": round(special_hits / len(hits) * 100, 2) if hits else 0,
        "wave_rate": round(wave_hits / len(hits) * 100, 2) if hits else 0,
        "max_miss": max_miss,
        "max_wave_miss": max_wave_miss,
        "avg_hit": round(statistics.mean(hits), 2) if hits else 0
    }

# ====================== 主程序 ======================
def main():
    print("🚀 新澳门六合彩 AI V22.9 去重修复版 启动...\n")
    conn = init_db()
    records = fetch_real_data()
    save_data(conn, records)
    records = load_records(conn)

    latest = records[-1]
    print(f"🔔 最新开奖: {latest['issue']}")
    print("号码:", " ".join(str(x).zfill(2) for x in latest["numbers"]), "+", str(latest["special"]).zfill(2))
    print(f"📊 历史数据总量: {len(records)} 期\n")

    pred, special, confidence = generate_prediction(records)
    pred_waves = predict_wave_double(records)

    backtest = walk_forward_backtest(records)

    print("🎯 本期AI预测:")
    print("正码:", " ".join(str(x).zfill(2) for x in pred))
    print("特码:", str(special).zfill(2))
    print("波色双选:", " + ".join(pred_waves))

    print(f"\n📈 回测统计:")
    print(f"平均命中: {backtest['avg_hit']}/6")
    print(f"特码命中率: {backtest['special_rate']}%")
    print(f"波色双选命中率: {backtest['wave_rate']}%")
    print(f"最大连空: {backtest['max_miss']} 期")
    print(f"波色最大连空: {backtest['max_wave_miss']} 期")

    generate_html = lambda: None  # 简化，暂时不生成HTML
    print("\n✅ 修复完成（已去除号码重复）")

if __name__ == "__main__":
    main()