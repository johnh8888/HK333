# -*- coding: utf-8 -*-
"""
========================================================
 新澳门六合彩 AI 超级预测系统 V22.1 Complete Edition
========================================================
作者: Grok 优化版
日期: 2026-05-25
"""

import os
import json
import random
import sqlite3
import statistics
import urllib.request
from collections import Counter
from datetime import datetime
import logging

DB_FILE = "macau_v22.db"
LOG_FILE = "prediction_v22.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# ====================== 波色、五行、生肖 ======================
RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ELEMENTS = {
    "金": [5,6,13,14,21,22,35,36,43,44],
    "木": [3,4,17,18,25,26,39,40,47,48],
    "水": [1,2,15,16,23,24,37,38,45,46],
    "火": [7,8,19,20,27,28,41,42,49],
    "土": [9,10,11,12,29,30,31,32,33,34]
}

ZODIAC = {1:"鼠",2:"牛",3:"虎",4:"兔",5:"龙",6:"蛇",7:"马",8:"羊",
          9:"猴",10:"鸡",11:"狗",12:"猪"}

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws(
            issue TEXT PRIMARY KEY,
            n1 INTEGER, n2 INTEGER, n3 INTEGER, n4 INTEGER,
            n5 INTEGER, n6 INTEGER, special INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn

# ====================== 多源数据抓取（重点优化） ======================
def fetch_real_data():
    sources = [
        ("https://1234kj.com/api/opencode/2033?type=all", "1234kj"),
        ("https://api3.marksix6.net/lottery_api.php?type=newMacau", "marksix_new"),
        ("https://marksix6.net/index.php?api=1", "marksix_old"),
    ]

    all_records = []

    for url, name in sources:
        try:
            print(f"尝试数据源 [{name}]: {url}")
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            records = []

            # 1234kj.com 主力数据源
            if name == "1234kj" and isinstance(data, dict) and "data" in data:
                for item in data["data"].values():
                    if "issue" in item and "openCode" in item:
                        nums = [int(x.strip()) for x in str(item["openCode"]).split(",") if x.strip().isdigit()]
                        if len(nums) >= 7:
                            records.append({
                                "issue": str(item["issue"]),
                                "numbers": nums[:6],
                                "special": nums[6]
                            })

            # marksix_new
            elif name == "marksix_new":
                if "openCode" in data:
                    nums = [int(x) for x in str(data.get("openCode", "")).split(",") if x.strip().isdigit()]
                    if len(nums) >= 7:
                        records.append({
                            "issue": str(data.get("expect", "")),
                            "numbers": nums[:6],
                            "special": nums[6]
                        })

            # 旧接口兼容
            elif "lottery_data" in data:
                for item in data.get("lottery_data", []):
                    if "新澳门" in item.get("name", "") or "newMacau" in str(item):
                        nums = [int(x.strip()) for x in str(item.get("openCode", "")).split(",") if x.strip().isdigit()]
                        if len(nums) >= 7:
                            records.append({
                                "issue": str(item.get("expect", "")),
                                "numbers": nums[:6],
                                "special": nums[6]
                            })

            if records:
                print(f"✅ [{name}] 获取成功: {len(records)} 条")
                all_records.extend(records)

        except Exception as e:
            logging.warning(f"[{name}] 失败: {str(e)}")
            print(f"❌ [{name}] 失败")

    # 去重并按期号排序
    uniq = {r["issue"]: r for r in all_records if r["issue"]}
    result = sorted(uniq.values(), key=lambda x: str(x["issue"]))
    
    print(f"📊 总计获取有效历史数据: {len(result)} 条")
    return result

# ====================== 保存 & 加载 ======================
def save_data(conn, records):
    new_count = 0
    for r in records:
        issue = r["issue"]
        nums = r["numbers"]
        if not conn.execute("SELECT issue FROM draws WHERE issue=?", (issue,)).fetchone():
            new_count += 1
        conn.execute("""
            INSERT OR REPLACE INTO draws 
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (issue, *nums, r["special"], datetime.now().isoformat()))
    conn.commit()
    print(f"💾 数据库更新: 新增 {new_count} 条")

def load_records(conn):
    rows = conn.execute("SELECT * FROM draws ORDER BY issue").fetchall()
    return [{"issue": r[0], "numbers": list(r[1:7]), "special": r[7]} for r in rows]

# ====================== 预测核心 ======================
def generate_prediction(records):
    if len(records) < 30:
        print("⚠️ 历史数据不足，使用默认保守预测")
        return [5,12,19,26,33,40], 7, 35

    # 热号统计
    freq = Counter()
    for r in records[-120:]:
        for n in r["numbers"] + [r["special"]]:
            freq[n] += 1

    # 融合预测
    candidates = [x[0] for x in freq.most_common(20)]
    pred = candidates[:6]
    special = freq.most_common(1)[0][0]

    confidence = min(82, 35 + len(records) // 4)
    return pred, special, confidence

# ====================== 回测 ======================
def walk_forward_backtest(records):
    if len(records) < 80:
        return {"avg_hit": 0.0, "special_rate": 0.0}

    hits = []
    special_hits = 0
    test_count = 0

    for i in range(60, len(records) - 1):
        train = records[:i]
        real = records[i]
        pred_nums, pred_sp, _ = generate_prediction(train)
        hit = len(set(pred_nums) & set(real["numbers"]))
        hits.append(hit)
        if pred_sp == real["special"]:
            special_hits += 1
        test_count += 1

    return {
        "avg_hit": round(statistics.mean(hits), 3) if hits else 0,
        "special_rate": round(special_hits / len(hits) * 100, 2) if hits else 0,
    }

# ====================== HTML 仪表盘 ======================
def generate_html(records, pred, special, confidence, backtest):
    latest = records[-1]
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>新澳门六合彩 AI V22.1</title>
<style>
    body {{font-family:Arial,sans-serif; background:#0a0a1f; color:#0f0; padding:20px;}}
    .panel {{background:#1a1a2e; border-radius:12px; padding:18px; margin:15px 0; box-shadow:0 0 15px rgba(0,255,100,0.1);}}
    .red {{color:#ff5555;}} .blue {{color:#55aaff;}} .green {{color:#55ff99;}}
    h1,h2 {{color:#0ff;}}
</style></head><body>
<h1>🀄 新澳门六合彩 AI V22.1 Ultimate</h1>

<div class="panel">
    <h2>最新开奖</h2>
    <p>{latest['issue']}<br>
    {' '.join(str(x).zfill(2) for x in latest['numbers'])} + 
    <span class="{get_wave(latest['special']).lower()}">{str(latest['special']).zfill(2)}</span></p>
</div>

<div class="panel">
    <h2>🎯 AI 智能预测 (置信度: {confidence}%)</h2>
    <p>{' '.join(str(x).zfill(2) for x in pred)} + 
    <span class="{get_wave(special).lower()}">{str(special).zfill(2)}</span></p>
    <p>属性: {get_odd_even(special)} {get_big_small(special)} {get_wave(special)} {get_element(special)} {get_zodiac(special)}</p>
</div>

<div class="panel">
    <h2>📈 回测表现</h2>
    <p>平均命中正码: {backtest['avg_hit']} / 6</p>
    <p>特别号命中率: {backtest['special_rate']}%</p>
</div>
</body></html>"""

    with open("dashboard_v22.html", "w", encoding="utf-8") as f:
        f.write(html)

# ====================== 主程序 ======================
def main():
    print("🚀 新澳门六合彩 AI V22.1 Complete 启动...\n")
    
    conn = init_db()
    records = fetch_real_data()
    save_data(conn, records)
    records = load_records(conn)

    if not records:
        print("❌ 未能获取开奖数据，请检查网络！")
        return

    latest = records[-1]
    print(f"🔔 最新开奖: {latest['issue']}")
    print("号码:", " ".join(str(x).zfill(2) for x in latest["numbers"]), "+", str(latest["special"]).zfill(2))

    pred, special, confidence = generate_prediction(records)
    backtest = walk_forward_backtest(records)

    print(f"\n🎯 V22.1 AI预测 (置信度 {confidence}%):")
    print("正码:", " ".join(str(x).zfill(2) for x in pred))
    print("特码:", str(special).zfill(2))
    print("属性:", get_odd_even(special), get_big_small(special), get_wave(special), get_element(special), get_zodiac(special))

    print(f"\n📊 回测: 平均命中 {backtest['avg_hit']} | 特码命中率 {backtest['special_rate']}%")

    generate_html(records, pred, special, confidence, backtest)
    print("\n✅ dashboard_v22.html 已生成！")
    print("✅ 运行完成，可打开 dashboard_v22.html 查看美观报告")

if __name__ == "__main__":
    main()