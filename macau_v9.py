# -*- coding: utf-8 -*-
"""
========================================================
 新澳门六合彩 AI 超级预测系统 V22.4 完整属性版
========================================================
优化：加强1234kj历史数据解析 + 属性预测
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

ELEMENTS = {
    "金": [5,6,13,14,21,22,35,36,43,44],
    "木": [3,4,17,18,25,26,39,40,47,48],
    "水": [1,2,15,16,23,24,37,38,45,46],
    "火": [7,8,19,20,27,28,41,42,49],
    "土": [9,10,11,12,29,30,31,32,33,34]
}

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

# ====================== 增强数据抓取 ======================
def fetch_real_data():
    sources = [
        ("https://1234kj.com/api/opencode/2033?type=all", "1234kj"),
        ("https://api3.marksix6.net/lottery_api.php?type=newMacau", "marksix_new"),
        ("https://marksix6.net/index.php?api=1", "marksix_old"),
    ]

    all_records = []

    for url, name in sources:
        try:
            print(f"尝试 [{name}]: {url}")
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            records = []

            # 重点优化 1234kj 数据解析
            if name == "1234kj" and isinstance(data, dict) and "data" in data:
                for key, item in data["data"].items():
                    if isinstance(item, dict) and "openCode" in item and "issue" in item:
                        nums = [int(x.strip()) for x in str(item["openCode"]).split(",") if x.strip().isdigit()]
                        if len(nums) >= 7:
                            records.append({
                                "issue": str(item["issue"]),
                                "numbers": nums[:6],
                                "special": nums[6]
                            })

            # 其他接口兼容
            elif name == "marksix_new" and "openCode" in data:
                nums = [int(x.strip()) for x in str(data.get("openCode","")).split(",") if x.strip().isdigit()]
                if len(nums) >= 7:
                    records.append({"issue": str(data.get("expect","")), "numbers": nums[:6], "special": nums[6]})

            if records:
                print(f"✅ [{name}] 成功获取 {len(records)} 条历史数据")
                all_records.extend(records)
                if len(records) >= 12:  # 优先使用丰富数据源
                    break

        except Exception as e:
            print(f"❌ [{name}] 失败")

    # 去重排序
    uniq = {r["issue"]: r for r in all_records if r.get("issue")}
    result = sorted(uniq.values(), key=lambda x: str(x["issue"]))
    print(f"📊 最终获取有效数据: {len(result)} 条")
    return result

# ====================== 保存 & 加载 ======================
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

# ====================== 属性预测 ======================
def attribute_prediction(records):
    if len(records) < 10:
        return {"wave": "红", "big_small": "大", "odd_even": "单", "element": "火", "zodiac": "马"}
    
    recent = records[-100:]
    waves = Counter(get_wave(r["special"]) for r in recent)
    bigs = Counter(get_big_small(r["special"]) for r in recent)
    odds = Counter(get_odd_even(r["special"]) for r in recent)
    elems = Counter(get_element(r["special"]) for r in recent)
    
    return {
        "wave": waves.most_common(1)[0][0],
        "big_small": bigs.most_common(1)[0][0],
        "odd_even": odds.most_common(1)[0][0],
        "element": elems.most_common(1)[0][0],
        "zodiac": get_zodiac(max([r["special"] for r in recent], default=7))
    }

# ====================== 号码预测 ======================
def generate_prediction(records):
    if len(records) < 30:
        print("⚠️ 数据不足，使用默认热号")
        return [5,12,19,26,33,40], 7, 40

    freq = Counter()
    for r in records[-150:]:
        for n in r["numbers"] + [r["special"]]:
            freq[n] += 2 if n == r["special"] else 1

    pred = [x[0] for x in freq.most_common(6)]
    special = freq.most_common(1)[0][0]
    confidence = min(85, 40 + len(records)//3)
    return pred, special, confidence

# ====================== HTML ======================
def generate_html(records, pred, special, confidence, attr):
    latest = records[-1]
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>澳门六合彩 V22.4</title>
<style>
    body{{font-family:Arial;background:#0a0a1f;color:#0f0;padding:25px;}}
    .panel{{background:#1a1a2e;border-radius:12px;padding:20px;margin:15px 0;}}
    .red{{color:#ff6666;}} .blue{{color:#66bbff;}} .green{{color:#66ff99;}}
</style></head><body>
<h1>🀄 新澳门六合彩 AI V22.4</h1>

<div class="panel"><h2>最新开奖</h2>
<p>{latest['issue']}<br>
{' '.join(str(x).zfill(2) for x in latest['numbers'])} + <span class="{get_wave(latest['special']).lower()}">{str(latest['special']).zfill(2)}</span></p>
</div>

<div class="panel"><h2>🎯 AI号码预测 (置信度 {confidence}%)</h2>
<p>{' '.join(str(x).zfill(2) for x in pred)} + <span class="{get_wave(special).lower()}">{str(special).zfill(2)}</span></p>
</div>

<div class="panel"><h2>🔮 特码属性完整预测</h2>
<p><strong>波色：</strong><span class="{attr['wave'].lower()}">{attr['wave']}</span></p>
<p><strong>大小：</strong>{attr['big_small']}</p>
<p><strong>单双：</strong>{attr['odd_even']}</p>
<p><strong>五行：</strong>{attr['element']}</p>
<p><strong>生肖：</strong>{attr['zodiac']}</p>
</div>
</body></html>"""

    with open("dashboard_v22.html", "w", encoding="utf-8") as f:
        f.write(html)

# ====================== 主程序 ======================
def main():
    print("🚀 新澳门六合彩 AI V22.4 启动...\n")
    conn = init_db()
    records = fetch_real_data()
    save_data(conn, records)
    records = load_records(conn)

    if not records:
        print("❌ 未获取数据")
        return

    latest = records[-1]
    print(f"🔔 最新开奖: {latest['issue']}")
    print("号码:", " ".join(str(x).zfill(2) for x in latest["numbers"]), "+", str(latest["special"]).zfill(2))
    print(f"📊 当前历史数据总量: {len(records)} 期")

    pred, special, confidence = generate_prediction(records)
    attr = attribute_prediction(records)

    print(f"\n🎯 AI预测 (置信度 {confidence}%):")
    print("正码:", " ".join(str(x).zfill(2) for x in pred))
    print("特码:", str(special).zfill(2))
    
    print("\n🔮 特码属性预测:")
    for k, v in attr.items():
        print(f"  {k}：{v}")

    generate_html(records, pred, special, confidence, attr)
    print("\n✅ dashboard_v22.html 已生成")

if __name__ == "__main__":
    main()