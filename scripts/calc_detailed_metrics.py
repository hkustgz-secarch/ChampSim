import os
import csv
import re
import pandas as pd

# ================= 配置区域 =================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 输入 Log 目录
# INPUT_REL_PATH = "../results/log/1C.fullBW.berti_pythia_sms_default"
INPUT_REL_PATH = "../results/log/1C.fullBW.baseline_updatePythia"
# INPUT_REL_PATH = "../results/log/1C.fullBW.nopref_baseline"

# 输出目录 (已重命名为 detailed_metrics)
OUTPUT_METRICS_DIR = "../results/detailed_metrics"

# 定义需要处理的 Cache 类型及其顺序
# 注意：这里只写核心名字，脚本会自动组合 LOAD 和 PREFETCH
# 排除 ITLB 和 L1I
TARGET_CACHES = ['cpu0_L1D', 'cpu0_L2C', 'LLC'] 

# 定义需要提取的操作类型
TARGET_OPS = ['LOAD', 'PREFETCH']

# ===========================================

def get_paths():
    """解析路径并确保目录存在"""
    input_dir = os.path.normpath(os.path.join(SCRIPT_DIR, INPUT_REL_PATH))
    # 修改输出路径变量
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
    只提取 TARGET_CACHES 中的组件，且只提取 LOAD 和 PREFETCH 行
    """
    entries = [] # 存储该文件的所有条目
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
                    
                    # === 过滤 1: 排除 ITLB 和 L1I ===
                    if "ITLB" in cache_name or "L1I" in cache_name:
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
                    # 我们将 CacheName 和 Operation 组合起来作为一个唯一的 Key
                    component_name = f"{cache_name}_{row_type}"
                    
                    entries.append({
                        'Filename': filename,
                        'Component': component_name,  # 组合键
                        'OriginalCache': cache_name,  # 辅助排序
                        'Operation': row_type,        # 辅助排序
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
    
    # 1. 计算 Miss Rate
    df['MissRate(%)'] = df.apply(lambda x: (x['Miss'] / x['Access'] * 100) if x['Access'] > 0 else 0, axis=1)
    
    # 2. 提取 IPC (去重)
    ipc_df = df[['Filename', 'IPC']].drop_duplicates().set_index('Filename')
    
    # 3. Pivot 透视表
    # Index: Filename
    # Columns: Component (例如 cpu0_L1D_LOAD)
    # Values: Access, MissRate
    pivot_df = df.pivot(index='Filename', columns='Component', values=['Access', 'MissRate(%)'])
    
    # 4. 构建理想的列顺序
    final_col_order = []
    new_col_names = {} 

    # 遍历我们定义的优先级 Cache
    for cache in TARGET_CACHES:
        # 遍历 Load 和 Prefetch
        for op in TARGET_OPS:
            # 组合出 Component 名字，例如 cpu0_L1D_LOAD
            comp_key = f"{cache}_{op}"
            
            # 构建原始 Pivot 的 MultiIndex 列名
            col_access_orig = ('Access', comp_key)
            col_rate_orig = ('MissRate(%)', comp_key)
            
            # 构建新的显示列名 (简化名字)
            simple_name = cache.replace("cpu0_", "").replace("LLC", "LLC")
            
            name_access_new = f"{simple_name}_{op}_Access"
            name_rate_new = f"{simple_name}_{op}_MissRate(%)"
            
            # 存入映射
            new_col_names[col_access_orig] = name_access_new
            new_col_names[col_rate_orig] = name_rate_new
            
            # 加入顺序列表 (先 Access 后 MissRate)
            final_col_order.append(name_access_new)
            final_col_order.append(name_rate_new)

    # 5. 应用列名重命名
    pivot_df = pivot_df.fillna(0)
    pivot_df.columns = [new_col_names.get(c, f"{c[1]}_{c[0]}") for c in pivot_df.columns]
    
    # 6. 排序并过滤列
    valid_cols = [c for c in final_col_order if c in pivot_df.columns]
    final_df = pivot_df[valid_cols].copy()
    
    # 7. 合并 IPC
    final_df = final_df.join(ipc_df)
    
    # 8. 格式化数据
    # Access 列转整数
    for col in final_df.columns:
        if "Access" in col:
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

def main():
    # 获取路径
    input_dir, metrics_dir, dir_name = get_paths()
    
    if not os.path.exists(input_dir):
        print(f"错误: 目录不存在 {input_dir}")
        return
        
    print(f"目标目录: {input_dir}")
    print(f"输出目录: {metrics_dir}")
    
    files = sorted([f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))])
    
    all_data = []
    print(f"开始处理 {len(files)} 个文件...")
    
    for idx, filename in enumerate(files):
        filepath = os.path.join(input_dir, filename)
        entries = process_file(filepath, filename)
        all_data.extend(entries)
        
        if (idx + 1) % 100 == 0:
            print(f"进度: {idx + 1}/{len(files)}")
            
    if not all_data:
        print("未提取到数据。请检查 Log 格式或过滤条件。")
        return

    # 创建 DataFrame
    df_raw = pd.DataFrame(all_data)
    
    # 生成 Summary (输出到 detailed_metrics 文件夹)
    print(f"\n正在生成汇总表 (L1D/L2/LLC Split LOAD/PREFETCH)...")
    df_summary = df_raw.copy()
    df_summary['Filename'] = df_summary['Filename'].apply(clean_filename)
    
    # 传入新的 metrics_dir
    generate_summary(df_summary, dir_name, metrics_dir)
    
    print("\n完成！")

if __name__ == "__main__":
    main()