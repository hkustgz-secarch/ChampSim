# python3 scripts/calc_score.py \
#   --baseline results/1C.fullBW.baseline_baseline.csv \
#   --experiment results/1C.fullBW.berti_pythia_sms_default.csv
import csv
import math
import argparse
import sys
import os
import re

def parse_arguments():
    parser = argparse.ArgumentParser(description="Calculate IPC Speedup (Auto-width & Clean Names).")
    parser.add_argument('--baseline', type=str, required=True, help='Path to the Baseline CSV file')
    parser.add_argument('--experiment', type=str, required=True, help='Path to the Experiment CSV file')
    return parser.parse_args()

class DualLogger:
    """同时打印到终端和写入文件"""
    def __init__(self, filepath):
        self.filepath = filepath
        self.file = open(filepath, 'w', encoding='utf-8')
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def log(self, message=""):
        print(message)
        clean_message = self.ansi_escape.sub('', message)
        self.file.write(clean_message + "\n")

    def close(self):
        self.file.close()
        print(f"\n[Output] Report saved to: {self.filepath}")

def read_csv_data(filepath):
    data_map = {}
    invalid_set = set()
    
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if 'Category_Folder' not in reader.fieldnames or 'Trace_Name' not in reader.fieldnames or 'IPC' not in reader.fieldnames:
                print(f"Error: Missing columns in {filepath}")
                sys.exit(1)

            for row in reader:
                key = f"{row['Category_Folder']}/{row['Trace_Name']}"
                try:
                    ipc = float(row['IPC'])
                    if ipc > 0:
                        data_map[key] = ipc
                    else:
                        invalid_set.add(key)
                except (ValueError, TypeError):
                    invalid_set.add(key)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        sys.exit(1)
        
    return data_map, invalid_set

def get_display_name(full_key):
    """
    清洗 Trace 名称。
    策略：按 '.' 分割，固定去除最后两段（如果足够长），否则去除最后一段。
    Example: 
      spec17/gcc.champsimtrace.xz -> spec17/gcc
      gap/bfs.trace.gz -> gap/bfs
      old/mcf.gz -> old/mcf
    """
    if '/' in full_key:
        cat, name = full_key.split('/', 1)
    else:
        cat, name = "", full_key
    
    parts = name.split('.')
    
    if len(parts) > 2:
        # 至少有三段 (例如 name.type.gz)，去除最后两段
        clean_name = ".".join(parts[:-2])
    elif len(parts) == 2:
        # 只有两段 (例如 name.gz)，去除最后一段
        clean_name = parts[0]
    else:
        # 没有后缀
        clean_name = name
            
    if cat:
        return f"{cat}/{clean_name}"
    return clean_name

def calculate_geomean(values):
    if not values: return 0.0
    log_sum = sum(math.log(x) for x in values)
    return math.exp(log_sum / len(values))

def main():
    args = parse_arguments()

    # 1. 自动构建输出文件名
    base_name = os.path.splitext(os.path.basename(args.baseline))[0]
    exp_name = os.path.splitext(os.path.basename(args.experiment))[0]
    output_filename = f"{base_name}_vs_{exp_name}.txt"
    
    logger = DualLogger(output_filename)

    logger.log("=" * 90)
    logger.log(f"Speedup Comparison Report")
    logger.log("=" * 90)
    logger.log(f"Baseline   : {args.baseline}")
    logger.log(f"Experiment : {args.experiment}")
    logger.log("-" * 90)

    # 2. 读取数据
    base_data, base_invalid = read_csv_data(args.baseline)
    exp_data, exp_invalid = read_csv_data(args.experiment)

    all_keys = sorted(list(set(base_data.keys()) | set(base_invalid) | set(exp_data.keys()) | set(exp_invalid)))

    # 3. 预处理显示名称并计算最大宽度
    display_names = {}
    max_len = 20 # 最小宽度
    for key in all_keys:
        d_name = get_display_name(key)
        display_names[key] = d_name
        if len(d_name) > max_len:
            max_len = len(d_name)
    
    # 列宽设置
    col_width = max_len + 2
    row_fmt = f"{{:<{col_width}}} | {{:<10}} | {{:<10}} | {{:<10}}{{}}"

    # 4. 打印表头
    header = row_fmt.format("Trace Name", "IPC Base", "IPC Exp", "Speedup", "")
    logger.log(header)
    logger.log("-" * len(header))

    valid_speedups = []
    cnt_missing_exp = 0
    cnt_missing_base = 0
    cnt_invalid = 0

    for key in all_keys:
        ipc_base = base_data.get(key)
        ipc_exp = exp_data.get(key)
        d_name = display_names[key]
        
        # Case A: 缺失
        if key not in base_data and key not in base_invalid:
            logger.log(row_fmt.format(d_name, "MISSING", str(ipc_exp) if ipc_exp else '---', '---', ""))
            cnt_missing_base += 1
            continue
            
        if key not in exp_data and key not in exp_invalid:
            logger.log(row_fmt.format(d_name, str(ipc_base) if ipc_base else '---', "MISSING", '---', ""))
            cnt_missing_exp += 1
            continue

        # Case B: 无效
        if ipc_base is None or ipc_exp is None:
            base_str = f"{ipc_base:.4f}" if ipc_base else "INV/NA"
            exp_str = f"{ipc_exp:.4f}" if ipc_exp else "INV/NA"
            logger.log(row_fmt.format(d_name, base_str, exp_str, "INVALID", ""))
            cnt_invalid += 1
            continue

        # Case C: 正常
        speedup = ipc_exp / ipc_base
        valid_speedups.append(speedup)
        
        mark = " *" if speedup > 1.0 else ""
        
        if speedup > 1.0:
            speedup_str = f"\033[92m{speedup:<9.4f}\033[0m" # Green
        elif speedup < 1.0:
            speedup_str = f"\033[91m{speedup:<9.4f}\033[0m" # Red
        else:
            speedup_str = f"{speedup:<9.4f}"

        logger.log(row_fmt.format(d_name, f"{ipc_base:.4f}", f"{ipc_exp:.4f}", speedup_str, mark))

    # 5. 统计
    logger.log("-" * len(header))
    
    if cnt_missing_exp > 0 or cnt_missing_base > 0 or cnt_invalid > 0:
        logger.log("Warnings:")
        if cnt_missing_exp > 0: logger.log(f"  - Traces missing in Experiment: {cnt_missing_exp}")
        if cnt_missing_base > 0: logger.log(f"  - Traces missing in Baseline  : {cnt_missing_base}")
        if cnt_invalid > 0:      logger.log(f"  - Traces with Invalid IPC     : {cnt_invalid}")
        logger.log("")

    if valid_speedups:
        geomean = calculate_geomean(valid_speedups)
        arithmean = sum(valid_speedups) / len(valid_speedups)
        count = len(valid_speedups)
        
        logger.log(f"Summary ({count} valid traces):")
        logger.log(f"Geometric Mean Speedup : \033[1;32m{geomean:.4f}\033[0m") 
        logger.log(f"Arithmetic Mean Speedup: {arithmean:.4f}")
    else:
        logger.log("No valid common traces found for comparison.")
    
    logger.log("=" * 90)
    logger.close()

if __name__ == "__main__":
    main()