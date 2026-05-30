#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
澳门彩票数据分析框架
功能：
- 读取 SQLite 数据库
- 滑动窗口权重统计
- 连号、对子、三连号分析
- 波色、单双、大小统计
- 信息增益 / 特征排序
- 生成 CSV/Excel 报告和图表
"""

import sqlite3
import pandas as pd
import numpy as np
from collections import Counter
from itertools import combinations
import matplotlib.pyplot as plt

DB_PATH = "new_macau.db"  # 数据库路径，可修改为实际路径
WINDOW_SIZE = 50  # 滑动窗口大小，可调整

# -----------------------------
# 1. 读取数据库数据
# -----------------------------
def load_data(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = "SELECT * FROM marksix ORDER BY issue ASC"  # 假设表名为 marksix
    df = pd.read_sql(query, conn)
    conn.close()
    # 确保数字列为整数
    num_cols = [f'num{i}' for i in range(1,7)]
    for col in num_cols:
        df[col] = df[col].astype(int)
    return df

# -----------------------------
# 2. 滑动窗口权重统计
# -----------------------------
def sliding_window_weight(df: pd.DataFrame, window_size: int = 50) -> pd.DataFrame:
    num_cols = [f'num{i}' for i in range(1,7)]
    results = []

    for i in range(len(df) - window_size + 1):
        window = df.iloc[i:i+window_size]
        flat_nums = window[num_cols].values.flatten()
        counter = Counter(flat_nums)
        weights = {num: count/window_size for num, count in counter.items()}
        results.append(weights)

    # 转化为 DataFrame
    weight_df = pd.DataFrame(results).fillna(0)
    return weight_df

# -----------------------------
# 3. 连号、对子、三连号分析
# -----------------------------
def combination_analysis(df: pd.DataFrame):
    num_cols = [f'num{i}' for i in range(1,7)]
    consecutive_count = []
    pairs_count = []
    triplets_count = []

    for idx, row in df.iterrows():
        nums = sorted([row[col] for col in num_cols])
        # 连号分析
        consec = sum([1 for i in range(len(nums)-1) if nums[i+1]-nums[i]==1])
        consecutive_count.append(consec)
        # 对子
        pairs = sum([nums.count(n) == 2 for n in set(nums)])
        pairs_count.append(pairs)
        # 三连号
        triplets = sum([nums.count(n) == 3 for n in set(nums)])
        triplets_count.append(triplets)

    df['consecutive'] = consecutive_count
    df['pairs'] = pairs_count
    df['triplets'] = triplets_count
    return df

# -----------------------------
# 4. 波色 / 单双 / 大小统计
# -----------------------------
def feature_stats(df: pd.DataFrame):
    num_cols = [f'num{i}' for i in range(1,7)]
    df['odd_count'] = df[num_cols].apply(lambda x: sum(n%2==1 for n in x), axis=1)
    df['even_count'] = df[num_cols].apply(lambda x: sum(n%2==0 for n in x), axis=1)
    df['big_count'] = df[num_cols].apply(lambda x: sum(n>24 for n in x), axis=1)
    df['small_count'] = df[num_cols].apply(lambda x: sum(n<=24 for n in x), axis=1)
    return df

# -----------------------------
# 5. 信息增益 / 特征排序 (简单示例)
# -----------------------------
def feature_ranking(df: pd.DataFrame):
    # 对每个数字列计算出现频率
    num_cols = [f'num{i}' for i in range(1,7)]
    freq_dict = {col: df[col].value_counts(normalize=True).to_dict() for col in num_cols}
    return freq_dict

# -----------------------------
# 6. 可视化示例
# -----------------------------
def plot_frequency(freq_dict):
    for col, freq in freq_dict.items():
        plt.figure(figsize=(10,4))
        nums, counts = zip(*sorted(freq.items()))
        plt.bar(nums, counts)
        plt.title(f'{col} 数字出现频率')
        plt.xlabel('数字')
        plt.ylabel('频率')
        plt.show()

# -----------------------------
# 主流程
# -----------------------------
def main():
    print("加载数据库...")
    df = load_data(DB_PATH)
    
    print("滑动窗口统计权重...")
    weight_df = sliding_window_weight(df, WINDOW_SIZE)
    weight_df.to_csv("sliding_window_weights.csv", index=False)

    print("组合特征分析...")
    df = combination_analysis(df)
    
    print("波色 / 单双 / 大小统计...")
    df = feature_stats(df)

    print("特征频率统计...")
    freq_dict = feature_ranking(df)
    
    print("生成报告 CSV...")
    df.to_csv("macau_features.csv", index=False)
    
    print("绘制频率图表...")
    plot_frequency(freq_dict)

    print("分析完成！")

if __name__ == "__main__":
    main()