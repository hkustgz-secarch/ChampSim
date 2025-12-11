import csv
import argparse
import sys
import os
import re
import math

# -----------------------------------------------------------------------------
# 配置区域
# -----------------------------------------------------------------------------
DEFAULT_OUT_DIR = "results/compare_metrics"
KEY_COL = "Filename"
IPC_COL = "IPC"
CACHE_LEVELS = ["L1I", "L1D", "L2C", "LLC"]
PCT_KEYWORDS = ["MR", "MissRate", "Miss", "%"]

def parse_arguments():
    parser = argparse.ArgumentParser(description="Full Details: Values Before->After with Diff.")
    parser.add_argument('--baseline', type=str, required=True, help='Path to the Baseline CSV file')
    parser.add_argument('--experiment', type=str, required=True, help='Path to the Experiment CSV file')
    parser.add_argument('--outdir', type=str, default=DEFAULT_OUT_DIR, help='Directory to save the report')
    return parser.parse_args()

class DualLogger:
    def __init__(self, filepath):
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.file = open(filepath, 'w', encoding='utf-8')
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def log(self, message=""):
        print(message)
        clean_message = self.ansi_escape.sub('', message)
        self.file.write(clean_message + "\n")

    def close(self):
        self.file.close()
        print(f"\n[Output] Report saved to: \033[94m{self.filepath}\033[0m")

def get_short_name(path):
    base = os.path.splitext(os.path.basename(path))[0]
    if '_' in base:
        return base.rsplit('_', 1)[0]
    return base

def read_csv(filepath):
    data = {}
    fieldnames = []
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    try:
        with open(filepath, mode='r', encoding='utf-8') as f:
            content = f.read()
            if content.startswith('\ufeff'): content = content[1:]
            from io import StringIO
            reader = csv.DictReader(StringIO(content))
            if reader.fieldnames:
                fieldnames = [fn.strip() for fn in reader.fieldnames]
            for row in reader:
                raw_key = row.get(KEY_COL)
                if not raw_key: continue
                key = raw_key.strip()
                parsed = {}
                for k, v in row.items():
                    if k == KEY_COL or k is None: continue
                    clean_k = k.strip()
                    try:
                        parsed[clean_k] = float(v)
                    except (ValueError, TypeError):
                        parsed[clean_k] = 0.0
                data[key] = parsed
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        sys.exit(1)
    return data, fieldnames

def is_percentage_metric(metric_name):
    return any(kw in metric_name for kw in PCT_KEYWORDS)

def human_format(num):
    """将大数值转换为紧凑格式 (1.2M, 34k)"""
    if num == 0: return "0"
    magnitude = 0
    while abs(num) >= 1000:
        magnitude += 1
        num /= 1000.0
    # 保持最多3位有效数字的长度
    return '{:.1f}{}'.format(num, ['', 'k', 'M', 'G'][magnitude])

def calc_stats(base, exp, metric_name):
    """
    计算对比数据
    返回: (diff_val, is_relative_percent)
    """
    if is_percentage_metric(metric_name):
        # MR: 绝对差值
        return (exp - base), False
    else:
        # IPC/Access: 相对百分比
        if base == 0: return 0.0, True
        return ((exp - base) / base) * 100, True

def format_cell_detail(base, exp, diff, is_rel, m_type):
    """
    生成格式: "10->12 (+20%)" 或 "1.2M->1.3M (+8%)"
    """
    # 1. 格式化数值部分 (Base -> Exp)
    if m_type == "access":
        # Access 数值通常很大，用 human_format
        val_str = f"{human_format(base)}->{human_format(exp)}"
    else:
        # IPC 和 MR 保留一位小数
        val_str = f"{base:.1f}->{exp:.1f}"

    # 2. 格式化差值部分 (+Diff)
    if is_rel:
        d_str = f"{diff:+.1f}%"
    else:
        d_str = f"{diff:+.2f}" # MR 绝对值保留2位

    # 3. 确定颜色
    color = ""
    if m_type == "ipc":
        if diff > 0.1: color = "\033[92m"
        elif diff < -0.1: color = "\033[91m"
    elif m_type == "miss":
        if diff < -0.01: color = "\033[92m" # Drop is good
        elif diff > 0.01: color = "\033[91m"
    elif m_type == "access":
        if abs(diff) > 5.0: color = "\033[93m" # Yellow warning

    return f"{val_str} ({color}{d_str}\033[0m)"

def main():
    args = parse_arguments()
    
    base_short = get_short_name(args.baseline)
    exp_short = get_short_name(args.experiment)
    out_file = os.path.join(args.outdir, f"{base_short}_vs_{exp_short}.txt")
    
    logger = DualLogger(out_file)
    base_data, base_fields = read_csv(args.baseline)
    exp_data, exp_fields = read_csv(args.experiment)
    common_keys = sorted(list(set(base_data.keys()) & set(exp_data.keys())))
    
    # 增加列宽以容纳详细信息
    max_len = max([len(k) for k in common_keys]) if common_keys else 20
    name_w = min(max_len, 35) 

    # =========================================================================
    # PART 1: IPC
    # =========================================================================
    logger.log("="*160)
    logger.log(f"1. IPC PERFORMANCE (Base -> Exp)")
    logger.log("="*160)
    header_ipc = f"{'Trace Name':<{name_w}} | {'Base':>8} | {'Exp':>8} | {'Speedup':>8} | {'Diff%':>9}"
    logger.log(header_ipc)
    logger.log("-" * 160)
    
    ipc_ratios = []
    for key in common_keys:
        ipc_b = base_data[key].get(IPC_COL, 0)
        ipc_e = exp_data[key].get(IPC_COL, 0)
        disp_name = (key[:name_w-2] + "..") if len(key) > name_w else key
        
        if ipc_b > 0:
            speedup = ipc_e / ipc_b
            ipc_ratios.append(speedup)
            diff, is_rel = calc_stats(ipc_b, ipc_e, "IPC")
            
            # 格式化 Diff
            color = "\033[92m" if diff > 0.1 else ("\033[91m" if diff < -0.1 else "")
            diff_str = f"{color}{diff:+.1f}%\033[0m"
            sp_color = "\033[92m" if speedup > 1.001 else ("\033[91m" if speedup < 0.999 else "")
            
            logger.log(f"{disp_name:<{name_w}} | {ipc_b:8.3f} | {ipc_e:8.3f} | {sp_color}{speedup:8.3f}\033[0m | {diff_str:>9}")
        else:
            logger.log(f"{disp_name:<{name_w}} | {ipc_b:8.3f} | {ipc_e:8.3f} |      --- |       ---")

    if ipc_ratios:
        geomean = math.exp(sum(math.log(x) for x in ipc_ratios) / len(ipc_ratios))
        logger.log("-" * 160)
        gm_color = "\033[92m" if geomean > 1 else "\033[91m"
        logger.log(f"Geometric Mean Speedup: {gm_color}{geomean:.4f}\033[0m")
    logger.log("\n")

    # =========================================================================
    # Helper: Print Wide Table
    # =========================================================================
    def print_wide_table(title, acc_suffix, mr_suffix):
        logger.log("="*160)
        logger.log(f"{title}")
        logger.log(f"   Format: Base->Exp (Diff)")
        logger.log(f"   Access Diff is Relative %, MissRate Diff is Absolute Delta")
        logger.log("="*160)
        
        # 宽表设计
        col_width = 30 # 每列预留宽度，因为 "1.2M->1.5M (+10.5%)" 挺长的
        
        # Header
        header_parts = [f"{'Trace Name':<{name_w}}"]
        for lvl in CACHE_LEVELS:
            header_parts.append(f"{lvl:^{col_width}}")
        
        logger.log(" | ".join(header_parts))
        logger.log("-" * 160)

        for key in common_keys:
            disp_name = (key[:name_w-2] + "..") if len(key) > name_w else key
            row_parts = [f"{disp_name:<{name_w}}"]
            
            for lvl in CACHE_LEVELS:
                acc_col = f"{lvl}_{acc_suffix}"
                mr_col = f"{lvl}_{mr_suffix}"
                
                # --- Access ---
                acc_str = "."
                if acc_col in base_fields:
                    ab = base_data[key].get(acc_col, 0)
                    ae = exp_data[key].get(acc_col, 0)
                    if ab == 0 and ae == 0:
                        acc_str = "-"
                    elif ab == 0 and ae > 0:
                        acc_str = "New"
                    else:
                        diff, is_rel = calc_stats(ab, ae, acc_col)
                        acc_str = format_cell_detail(ab, ae, diff, is_rel, "access")
                
                # --- Miss Rate ---
                mr_str = "."
                if mr_col in base_fields:
                    mb = base_data[key].get(mr_col, 0)
                    me = exp_data[key].get(mr_col, 0)
                    if mb == 0 and me == 0:
                        mr_str = "-"
                    else:
                        diff, is_rel = calc_stats(mb, me, mr_col) # is_rel=False
                        mr_str = format_cell_detail(mb, me, diff, is_rel, "miss")

                # Combine in one cell: "Acc_Info / MR_Info"
                # 这种格式会很长，所以用 "/" 分隔
                combined = f"{acc_str} / {mr_str}"
                row_parts.append(f"{combined:^{col_width}}")
            
            logger.log(" | ".join(row_parts))
        logger.log("\n")

    # =========================================================================
    # PART 2 & 3: Tables
    # =========================================================================
    print_wide_table("2. DEMAND LOAD COMPARISON [Access / MissRate]", "LOAD_Acc", "LOAD_MR")
    print_wide_table("3. PREFETCHING COMPARISON [Access / MissRate]", "PF_Acc", "PF_MR")

    # =========================================================================
    # PART 4: RAW LIST
    # =========================================================================
    logger.log("="*160)
    logger.log(f"4. RAW DATA LIST (Significant Changes)")
    logger.log("="*160)
    
    all_metrics = sorted([m for m in base_fields if m != KEY_COL])

    for key in common_keys:
        logger.log(f"[{key}]")
        changes = []
        for metric in all_metrics:
            b_val = base_data[key].get(metric, 0)
            e_val = exp_data[key].get(metric, 0)
            
            if b_val == 0 and e_val == 0: continue
            
            diff, is_rel = calc_stats(b_val, e_val, metric)
            
            # Filter insignificant
            if is_rel and abs(diff) < 0.1: continue
            if not is_rel and abs(diff) < 0.01: continue
            
            # Type
            m_type = "neutral"
            if "IPC" in metric: m_type = "ipc"
            elif is_percentage_metric(metric): m_type = "miss"
            elif "Acc" in metric: m_type = "access"

            # Use Detail Format
            formatted_val = format_cell_detail(b_val, e_val, diff, is_rel, m_type)
            
            changes.append(f"    {metric:<30}: {formatted_val}")
        
        if not changes:
            logger.log("    (No significant changes)")
        else:
            for line in changes:
                logger.log(line)
        logger.log("")

    logger.close()

if __name__ == "__main__":
    main()