import os
import csv
import re
import pandas as pd

# ================= 配置区域 =================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 输入 Log 目录列表 (将需要处理的相对路径都放在这里)
INPUT_DIR_LIST = [
    # "../results/log/1C.fullBW.berti_pythia_sms_4096sets_default",
    # "../results/log/1C.fullBW.berti_pythia_sms_default",
    # "../results/log/1C.fullBW.baseline_updatePythia",
    # "../results/log/1C.fullBW.nopref_baseline",
    # "../results/log/1C.fullBW.bertiMcmc_pythia_sms_default",
    # "../results/log/1C.fullBW.cmc_pythia_sms_default",
    # "../results/log/1C.fullBW.nextline_pythia_sms_s20_pfrefill",
    "../results/log/1C.fullBW.berti_pythia_sms_pfrefill",
]

# 输出目录
OUTPUT_METRICS_DIR = "../results/detailed_metrics"

# 定义需要处理的 Cache 类型及其顺序
TARGET_CACHES = ['cpu0_L1I', 'cpu0_L1D', 'cpu0_L2C', 'LLC'] 

# 定义需要提取的操作类型
TARGET_OPS = ['LOAD', 'PREFETCH']

# ===========================================

def get_paths(input_rel_path):
    """解析路径并确保目录存在"""
    input_dir = os.path.normpath(os.path.join(SCRIPT_DIR, input_rel_path))
    metrics_dir = os.path.normpath(os.path.join(SCRIPT_DIR, OUTPUT_METRICS_DIR))
    
    os.makedirs(metrics_dir, exist_ok=True)
    
    dir_name = os.path.basename(input_dir)
    return input_dir, metrics_dir, dir_name

def parse_line_value(line, key):
    """提取数值"""
    try:
        parts = line.split()
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return int(parts[idx + 1])
    except ValueError:
        return 0
    return 0

def clean_filename(fname):
    """清洗文件名"""
    parts = fname.split('.')
    if len(parts) > 3:
        return ".".join(parts[:-3])
    return fname.replace(".log", "").replace(".txt", "")

def process_file(filepath, filename):
    """
    解析单个日志文件
    """
    entries = [] 
    ipc_value = 0.0
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                # --- 1. 抓取 IPC ---
                if "cumulative IPC:" in line:
                    try:
                        match = re.search(r"IPC:\s+([\d\.]+)", line)
                        if match:
                            ipc_value = float(match.group(1))
                    except:
                        pass

                # --- 2. 抓取 Cache ---
                # 格式示例: cpu0->cpu0_L1D LOAD ACCESS: 100 ...
                if "ACCESS:" in line and ("cpu0->" in line or "LLC" in line):
                    parts = line.split()
                    full_name_part = parts[0]
                    
                    # 获取 Cache 名称 (如 cpu0_L1D)
                    cache_name = full_name_part.split("->")[1] if "->" in full_name_part else full_name_part
                    
                    # === 过滤 1: 仅排除 ITLB ===
                    if "ITLB" in cache_name:
                        continue
                        
                    # 获取操作类型 (LOAD / PREFETCH ...)
                    row_type = parts[1]
                    
                    # === 过滤 3: 只提取 LOAD 和 PREFETCH ===
                    if row_type not in TARGET_OPS:
                        continue
                    
                    # 提取数据
                    access_val = parse_line_value(line, "ACCESS:")
                    miss_val = parse_line_value(line, "MISS:")
                    
                    # 构建条目
                    component_name = f"{cache_name}_{row_type}"
                    
                    entries.append({
                        'Filename': filename,
                        'Component': component_name,
                        'OriginalCache': cache_name,
                        'Operation': row_type,
                        'Access': access_val,
                        'Miss': miss_val
                    })

    except Exception as e:
        print(f"Error reading {filename}: {e}")
        return []

    # 将 IPC 注入到每条记录
    for entry in entries:
        entry['IPC'] = ipc_value
        
    return entries

def generate_summary(df, output_prefix, output_dir):
    """生成宽表 summary"""
    
    # 1. 计算 Miss Rate (已修改：此处不再需要计算 Rate，直接使用原始 Miss 数据)
    # df['MissRate(%)'] = df.apply(lambda x: (x['Miss'] / x['Access'] * 100) if x['Access'] > 0 else 0, axis=1)
    
    # 2. 提取 IPC (去重)
    ipc_df = df[['Filename', 'IPC']].drop_duplicates().set_index('Filename')
    
    # 3. Pivot 透视表
    # [修改] values 列表改为 'Miss' 而不是 'MissRate(%)'
    pivot_df = df.pivot(index='Filename', columns='Component', values=['Access', 'Miss'])
    
    # 4. 构建理想的列顺序 并 进行重命名
    final_col_order = []
    new_col_names = {} 

    # 遍历我们定义的优先级 Cache
    for cache in TARGET_CACHES:
        # 遍历 Load 和 Prefetch
        for op in TARGET_OPS:
            # 组合出 Component 名字，例如 cpu0_L1D_LOAD
            comp_key = f"{cache}_{op}"
            
            # --- 表头缩写逻辑 ---
            # 简化 Cache 名字
            simple_name = cache.replace("cpu0_", "").replace("LLC", "LLC")
            
            # 简化 Operation 名字 (PREFETCH -> PF)
            op_short = "PF" if op == "PREFETCH" else op
            
            # 构建新的列名 (Acc 代替 Access, [修改] Miss 代替 MR)
            name_access_new = f"{simple_name}_{op_short}_Acc"
            name_miss_new = f"{simple_name}_{op_short}_Miss" # 改为 Miss 后缀
            
            # 原始 Pivot 的 MultiIndex 列名
            col_access_orig = ('Access', comp_key)
            col_miss_orig = ('Miss', comp_key) # [修改] 源列为 Miss
            
            # 存入映射
            new_col_names[col_access_orig] = name_access_new
            new_col_names[col_miss_orig] = name_miss_new
            
            # 加入顺序列表
            final_col_order.append(name_access_new)
            final_col_order.append(name_miss_new)

    # 5. 应用列名重命名
    pivot_df = pivot_df.fillna(0)
    pivot_df.columns = [new_col_names.get(c, f"{c[1]}_{c[0]}") for c in pivot_df.columns]
    
    # 6. 排序并过滤列
    valid_cols = [c for c in final_col_order if c in pivot_df.columns]
    final_df = pivot_df[valid_cols].copy()
    
    # 7. 合并 IPC
    final_df = final_df.join(ipc_df)
    
    # 8. 格式化数据
    # Access/Acc/Miss 列转整数
    for col in final_df.columns:
        # [修改] 增加 "Miss" 到判断条件，确保 Miss 数量也显示为整数
        if "Acc" in col or "Access" in col or "Miss" in col:
            final_df[col] = final_df[col].astype(int)
            
    # 重置索引
    final_df.reset_index(inplace=True)
    
    # 确保 IPC 在最后
    cols = list(final_df.columns)
    if 'IPC' in cols:
        cols.remove('IPC')
        cols.append('IPC')
        final_df = final_df[cols]

    # 9. 输出文件
    out_csv = os.path.join(output_dir, f"{output_prefix}_summary.csv")
    out_txt = os.path.join(output_dir, f"{output_prefix}_summary.txt")
    
    # CSV
    final_df.to_csv(out_csv, index=False, float_format='%.2f')
    
    # TXT
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write(f"Summary for {output_prefix}\n")
        f.write("=" * 150 + "\n")
        f.write(final_df.to_string(index=False, justify='left', line_width=2000, float_format=lambda x: "{:.2f}".format(x)))

    print(f"  -> CSV: {os.path.basename(out_csv)}")
    print(f"  -> TXT: {os.path.basename(out_txt)}")

def process_directory_task(rel_path):
    """处理单个目录的任务函数"""
    input_dir, metrics_dir, dir_name = get_paths(rel_path)
    
    print("-" * 60)
    print(f"正在处理目录: {dir_name}")
    print(f"完整路径: {input_dir}")

    if not os.path.exists(input_dir):
        print(f"错误: 目录不存在 {input_dir}，跳过。")
        return
    
    files = sorted([f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))])
    
    if not files:
        print("目录为空，跳过。")
        return

    all_data = []
    print(f"找到 {len(files)} 个文件，开始解析...")
    
    for idx, filename in enumerate(files):
        filepath = os.path.join(input_dir, filename)
        entries = process_file(filepath, filename)
        all_data.extend(entries)
        
        # 简单的进度显示 (每500个文件显示一次，避免刷屏)
        if len(files) > 500 and (idx + 1) % 500 == 0:
            print(f"  进度: {idx + 1}/{len(files)}")
            
    if not all_data:
        print("未提取到有效数据。请检查 Log 格式或过滤条件。")
        return

    # 创建 DataFrame
    df_raw = pd.DataFrame(all_data)
    
    # 生成 Summary
    df_summary = df_raw.copy()
    df_summary['Filename'] = df_summary['Filename'].apply(clean_filename)
    
    generate_summary(df_summary, dir_name, metrics_dir)

def main():
    print("=== 批量 Log 解析脚本开始 ===")
    print(f"共配置了 {len(INPUT_DIR_LIST)} 个输入目录。\n")
    
    for rel_path in INPUT_DIR_LIST:
        process_directory_task(rel_path)
        
    print("\n=== 所有任务完成 ===")

if __name__ == "__main__":
    main()