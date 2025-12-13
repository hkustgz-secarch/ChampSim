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

def parse_arguments():
    parser = argparse.ArgumentParser(description="Full Details: Access / Miss / MR comparison.")
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

def human_format(num):
    """将大数值转换为紧凑格式 (1.2M, 34k)"""
    if num == 0: return "0"
    magnitude = 0
    abs_num = abs(num)
    while abs_num >= 1000:
        magnitude += 1
        abs_num /= 1000.0
    val = '{:.1f}{}'.format(abs_num, ['', 'k', 'M', 'G'][magnitude])
    return val if num >= 0 else '-' + val

def calc_ratio_diff(base, exp):
    """计算百分比变化 (100 -> 150 = +50%)"""
    if base == 0: 
        return (0.0, True) if exp == 0 else (100.0, True)
    return ((exp - base) / base) * 100, True

def calc_abs_diff(base, exp):
    """计算绝对值变化 (90% -> 92% = +2.0)"""
    return (exp - base), False

def format_cell_detail(base, exp, diff, is_rel, m_type):
    """
    m_type: 'access', 'miss_count', 'mr', 'ipc'
    """
    # 1. 数值显示
    if m_type == "mr" or m_type == "ipc":
        val_str = f"{base:.1f}->{exp:.1f}"
    else:
        val_str = f"{human_format(base)}->{human_format(exp)}"

    # 2. 差值显示
    if is_rel:
        d_str = f"{diff:+.1f}%"
    else:
        d_str = f"{diff:+.2f}"

    # 3. 颜色逻辑
    color = ""
    if m_type == "ipc":
        if diff > 0.5: color = "\033[92m" 
        elif diff < -0.5: color = "\033[91m" 
    elif m_type == "miss_count" or m_type == "mr":
        if diff < -0.05: color = "\033[92m" 
        elif diff > 0.05: color = "\033[91m" 
    elif m_type == "access":
        if abs(diff) > 5.0: color = "\033[93m" 

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
    
    max_len = max([len(k) for k in common_keys]) if common_keys else 20
    # [修改] Name 列宽增加到 45
    name_w = min(max_len, 45) 

    # [修改] 分割线增加到 300
    SEP_LINE = "=" * 300
    SUB_SEP_LINE = "-" * 300

    # =========================================================================
    # PART 1: IPC
    # =========================================================================
    logger.log(SEP_LINE)
    logger.log(f"1. IPC PERFORMANCE (Base -> Exp)")
    logger.log(SEP_LINE)
    header_ipc = f"{'Trace Name':<{name_w}} | {'Base':>8} | {'Exp':>8} | {'Speedup':>8} | {'Diff%':>9}"
    logger.log(header_ipc)
    logger.log(SUB_SEP_LINE)
    
    ipc_ratios = []
    for key in common_keys:
        ipc_b = base_data[key].get(IPC_COL, 0)
        ipc_e = exp_data[key].get(IPC_COL, 0)
        disp_name = (key[:name_w-2] + "..") if len(key) > name_w else key
        
        if ipc_b > 0:
            speedup = ipc_e / ipc_b
            ipc_ratios.append(speedup)
            diff, is_rel = calc_ratio_diff(ipc_b, ipc_e)
            
            color = "\033[92m" if diff > 0.5 else ("\033[91m" if diff < -0.5 else "")
            diff_str = f"{color}{diff:+.1f}%\033[0m"
            sp_color = "\033[92m" if speedup > 1.005 else ("\033[91m" if speedup < 0.995 else "")
            
            logger.log(f"{disp_name:<{name_w}} | {ipc_b:8.3f} | {ipc_e:8.3f} | {sp_color}{speedup:8.3f}\033[0m | {diff_str:>9}")
        else:
            logger.log(f"{disp_name:<{name_w}} | {ipc_b:8.3f} | {ipc_e:8.3f} |      --- |       ---")

    if ipc_ratios:
        geomean = math.exp(sum(math.log(x) for x in ipc_ratios) / len(ipc_ratios))
        logger.log(SUB_SEP_LINE)
        gm_color = "\033[92m" if geomean > 1 else "\033[91m"
        logger.log(f"Geometric Mean Speedup: {gm_color}{geomean:.4f}\033[0m")
    logger.log("\n")

    # =========================================================================
    # Helper: Print Wide Table (Calculate MR on the fly)
    # =========================================================================
    def print_wide_table(title, acc_suffix, miss_suffix):
        logger.log(SEP_LINE)
        logger.log(f"{title}")
        logger.log(f"   Format: Access / Miss / MR")
        logger.log(f"   Diffs : (Acc %) / (Miss %) / (MR Abs)")
        logger.log(SEP_LINE)
        
        # [修改] 数据列宽增加到 58
        col_width = 58
        
        # Header
        # 使用 :^{col_width} 确保表头居中并占满宽度
        header_parts = [f"{'Trace Name':<{name_w}}"]
        for lvl in CACHE_LEVELS:
            header_parts.append(f"{lvl:^{col_width}}")
        
        logger.log(" | ".join(header_parts))
        logger.log(SUB_SEP_LINE)

        for key in common_keys:
            disp_name = (key[:name_w-2] + "..") if len(key) > name_w else key
            row_parts = [f"{disp_name:<{name_w}}"]
            
            for lvl in CACHE_LEVELS:
                acc_col = f"{lvl}_{acc_suffix}"
                miss_col = f"{lvl}_{miss_suffix}"
                
                # --- Get Raw Data ---
                ab = base_data[key].get(acc_col, 0)
                ae = exp_data[key].get(acc_col, 0)
                mb = base_data[key].get(miss_col, 0)
                me = exp_data[key].get(miss_col, 0)
                
                # --- Calculate MR (On the fly) ---
                mrb = (mb / ab * 100.0) if ab > 0 else 0.0
                mre = (me / ae * 100.0) if ae > 0 else 0.0

                if ab == 0 and ae == 0 and mb == 0 and me == 0:
                    combined = "-"
                else:
                    d_acc, _ = calc_ratio_diff(ab, ae)
                    acc_str = format_cell_detail(ab, ae, d_acc, True, "access")
                    
                    d_miss, _ = calc_ratio_diff(mb, me)
                    miss_str = format_cell_detail(mb, me, d_miss, True, "miss_count")
                    
                    d_mr, _ = calc_abs_diff(mrb, mre)
                    mr_str = format_cell_detail(mrb, mre, d_mr, False, "mr")

                    combined = f"{acc_str} / {miss_str} / {mr_str}"
                
                # [关键点] 使用 ^{col_width} 强制让较短的内容也占据 58 字符宽度（居中对齐）
                # 这样所有竖线 | 都能对齐
                row_parts.append(f"{combined:^{col_width}}")
            
            logger.log(" | ".join(row_parts))
        logger.log("\n")

    # =========================================================================
    # PART 2 & 3: Tables
    # =========================================================================
    print_wide_table("2. DEMAND LOAD COMPARISON [Access / Miss / MR]", "LOAD_Acc", "LOAD_Miss")
    print_wide_table("3. PREFETCHING COMPARISON [Access / Miss / MR]", "PF_Acc", "PF_Miss")

    # =========================================================================
    # PART 4: RAW LIST
    # =========================================================================
    logger.log(SEP_LINE)
    logger.log(f"4. RAW DATA CHANGE LIST (From CSV Columns Only)")
    logger.log(SEP_LINE)
    
    all_metrics = sorted([m for m in base_fields if m != KEY_COL])

    for key in common_keys:
        logger.log(f"[{key}]")
        changes = []
        for metric in all_metrics:
            b_val = base_data[key].get(metric, 0)
            e_val = exp_data[key].get(metric, 0)
            
            if b_val == 0 and e_val == 0: continue
            
            # 判断类型
            m_type = "neutral"
            if "IPC" in metric: 
                diff, is_rel = calc_ratio_diff(b_val, e_val)
                m_type = "ipc"
            elif "Acc" in metric:
                diff, is_rel = calc_ratio_diff(b_val, e_val)
                m_type = "access"
            elif "Miss" in metric and "Rate" not in metric:
                diff, is_rel = calc_ratio_diff(b_val, e_val)
                m_type = "miss_count"
            else:
                diff, is_rel = calc_ratio_diff(b_val, e_val)

            if is_rel and abs(diff) < 0.1: continue
            
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