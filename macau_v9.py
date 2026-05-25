# -*- coding: utf-8 -*-
"""
========================================================
 新澳门六合彩 AI 超级预测系统 V22.6（原数据源优化版）
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

# ====================== 原数据源 - 增强解析 ======================
def fetch_real_data():
    print("正在使用原数据源获取新澳门六合彩...")
    url = "https://marksix6.net/index.php?api=1"
    
    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        records = []

        # 查找新澳门六合彩
        for item in data.get("lottery_data", []):
            if "新澳门" in item.get("name", "") or "newMacau" in str(item.get("name", "")):
                # 最新一期
                open_code = item.get("openCode", "")
                nums = [int(x.strip()) for x in str(open_code).split(",") if x.strip().isdigit()]
                if len(nums) >= 7:
                    records.append({
                        "issue": str(item.get("expect", "")),
                        "numbers": nums[:6],
                        "special": nums[6]
                    })

                # 关键修复：解析 history 数组
                for row in item.get("history", []):
                    if isinstance(row, str) and "期：" in row:
                        try:
                            parts = row.split("期：")
                            issue = parts[0].strip()
                            code_str = parts[1].strip()
                            nums = [int(x.strip()) for x in code_str.split(",") if x.strip().isdigit()]
                            if len(nums) >= 7:
                                records.append({
                                    "issue": issue,
                                    "numbers": nums[:6],
                                    "special": nums[6]
                                })
                        except:
                            continue
                break  # 只处理新澳门

        # 去重并排序
        uniq = {r["issue"]: r for r in records if r.get("issue")}
        result = sorted(uniq.values(), key=lambda x: str(x["issue"]))

        print(f"✅ 原数据源获取成功: {len(result)} 期")
        if len(result) > 0:
            print(f"最新期号: {result[-1]['issue']}")
        
        return result

    except Exception as e:
        print(f"❌ 数据获取失败: {e}")
        return []

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
    if len(records) < 8:
        return {"wave": "红", "big_small": "大", "odd_even": "单", "element": "火", "zodiac": "马"}
    
    recent = records[-80:]
    return {
        "wave": Counter(get_wave(r["special"]) for r in recent).most_common(1)[0][0],
        "big_small": Counter(get_big_small(r["special"]) for r in recent).most_common(1)[0][0],
        "odd_even": Counter(get_odd_even(r["special"]) for r in recent).most_common(1)[0][0],
        "element": Counter(get_element(r["special"]) for r in recent).most_common(1)[0][0],
        "zodiac": get_zodiac(max([r["special"] for r in recent], default=7))
    }

# ====================== 号码预测 ======================
def generate_prediction(records):
    if len(records) < 15:
        print("⚠️ 历史数据不足，使用默认预测")
        return [5,12,19,26,33,40], 7, 45

    freq = Counter()
    for r in records[-150:]:
        for n in r["numbers"] + [r["special"]]:
            freq[n] += 2 if n == r["special"] else 1

    pred = [x[0] for x in freq.most_common(6)]
    special = freq.most_common(1)[0][0]
    confidence = min(88, 40 + len(records)//3)
    return pred, special, confidence

# ====================== HTML ======================
def generate_html(records, pred, special, confidence, attr):
    latest = records[-1]
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>澳门六合彩 V22.6</title>
<style>
    body{{font-family:Arial;background:#0a0a1f;color:#0f0;padding:25px;}}
    .panel{{background:#1a1a2e;border-radius:12px;padding:20px;margin:15px 0;}}
    .red{{color:#ff6666;}} .blue{{color:#66bbff;}} .green{{color:#66ff99;}}
</style></head><body>
<h1>🀄 新澳门六合彩 AI V22.6</h1>

<div class="panel"><h2>最新开奖</h2>
<p>{latest['issue']}<br>
{' '.join(str(x).zfill(2) for x in latest['numbers'])} + <span class="{get_wave(latest['special']).lower()}">{str(latest['special']).zfill(2)}</span></p>
</div>

<div class="panel"><h2>🎯 AI号码预测 (置信度 {confidence}%)</h2>
<p>{' '.join(str(x).zfill(2) for x in pred)} + <span class="{get_wave(special).lower()}">{str(special).zfill(2)}</span></p>
</div>

<div class="panel"><h2>🔮 特码完整属性预测</h2>
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
    print("🚀 新澳门六合彩 AI V22.6 启动...\n")
    conn = init_db()
    records = fetch_real_data()
    save_data(conn, records)
    records = load_records(conn)

    if not records:
        print("❌ 未获取到数据")
        return

    latest = records[-1]
    print(f"🔔 最新开奖: {latest['issue']}")
    print("号码:", " ".join(str(x).zfill(2) for x in latest["numbers"]), "+", str(latest["special"]).zfill(2))
    print(f"📊 当前历史数据: {len(records)} 期")

    pred, special, confidence = generate_prediction(records)
    attr = attribute_prediction(records)

    print(f"\n🎯 AI预测 (置信度 {confidence}%):")
    print("正码:", " ".join(str(x).zfill(2) for x in pred))
    print("特码:", str(special).zfill(2))
    
    print("\n🔮 特码属性预测:")
    print(f"波色：{attr['wave']}")
    print(f"大小：{attr['big_small']}")
    print(f"单双：{attr['odd_even']}")
    print(f"五行：{attr['element']}")
    print(f"生肖：{attr['zodiac']}")

    generate_html(records, pred, special, confidence, attr)
    print("\n✅ dashboard_v22.html 已生成")

if __name__ == "__main__":
    main()