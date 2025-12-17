import os
import subprocess
import argparse
import time
import sys
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

# ================= 配置区域 =================
# 默认分析器脚本路径
ANALYZER_SCRIPT = "scripts/champsim_trace_analyzer.py"

# 默认搜索的根目录
DEFAULT_INPUT_ROOT = "/local-ssd/ben/dpc4-traces/logs/1C.fullBW.baseline_pfrefilltrace"

# 结果保存根目录
RESULTS_ROOT_DIR = "results/champsim_trace_analysis"

# 默认过滤关键字 (只处理包含此字符串的文件)
DEFAULT_PATTERN = "L1D"

# 默认最大核心数 (保留资源防止卡死)
DEFAULT_CORES = max(1, multiprocessing.cpu_count() // 2) 
# ===========================================

def parse_arguments():
    parser = argparse.ArgumentParser(description="Batch ChampSim Trace Analyzer Runner (L1D Filtered)")
    
    parser.add_argument(
        '--input', 
        type=str, 
        default=DEFAULT_INPUT_ROOT,
        help=f'Root directory to scan for .csv.gz files. Default: {DEFAULT_INPUT_ROOT}'
    )
    
    parser.add_argument(
        '--cores', 
        type=int, 
        default=DEFAULT_CORES,
        help=f'Max concurrent jobs. Default: {DEFAULT_CORES}'
    )

    parser.add_argument(
        '--analyzer',
        type=str,
        default=ANALYZER_SCRIPT,
        help=f'Path to the analyzer script. Default: {ANALYZER_SCRIPT}'
    )

    parser.add_argument(
        '--pattern',
        type=str,
        default=DEFAULT_PATTERN,
        help=f'Filename substring to filter. Only files containing this string will be processed. Default: "{DEFAULT_PATTERN}"'
    )
    
    return parser.parse_args()

def ensure_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            if e.errno != os.errno.EEXIST:
                raise

# === [核心功能] 负载监控 ===
def read_proc_stat():
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        values = [int(x) for x in parts[1:]]
        total_time = sum(values)
        idle_time = values[3] 
        return total_time, idle_time
    except FileNotFoundError:
        return 0, 0

def get_instant_busy_cores(interval=0.1):
    try:
        t1, i1 = read_proc_stat()
        time.sleep(interval)
        t2, i2 = read_proc_stat()
        
        delta_total = t2 - t1
        delta_idle = i2 - i1
        
        if delta_total == 0: return 0.0, 0.0
        
        usage_percent = 1.0 - (delta_idle / delta_total)
        total_cores = multiprocessing.cpu_count()
        busy_cores = usage_percent * total_cores
        
        return busy_cores, usage_percent * 100.0
    except Exception:
        return 0.0, 0.0

# === 单个任务执行函数 ===
def run_single_analysis(task_info):
    rel_folder, filename, full_path, analyzer_script, output_root = task_info
    
    # 构建输出路径 (保持目录结构)
    safe_rel_folder = rel_folder
    if safe_rel_folder == ".": safe_rel_folder = "root"
    
    output_dir = os.path.join(output_root, safe_rel_folder)
    ensure_dir(output_dir)
    
    # 结果日志文件名
    log_filename = f"{filename}.analysis.txt"
    log_path = os.path.join(output_dir, log_filename)
    
    cmd = ["python3", analyzer_script, full_path]

    print(f"[Started] {safe_rel_folder}/{filename}", flush=True)

    try:
        start_time = time.time()
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            text=True
        )
        duration = time.time() - start_time
        
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"CMD: {' '.join(cmd)}\n")
            f.write("-" * 40 + "\n")
            f.write(result.stdout)
        
        status = "SUCCESS" if result.returncode == 0 else "FAIL"
        
        return {
            "status": status,
            "folder": safe_rel_folder,
            "file": filename,
            "duration": duration,
            "log_path": log_path
        }

    except Exception as e:
        return {
            "status": "ERROR",
            "folder": safe_rel_folder,
            "file": filename,
            "duration": 0,
            "error_msg": str(e)
        }

def main():
    args = parse_arguments()
    
    if not os.path.exists(args.analyzer):
        print(f"Error: Analyzer script not found: {args.analyzer}")
        sys.exit(1)
        
    if not os.path.exists(args.input):
        print(f"Error: Input directory not found: {args.input}")
        sys.exit(1)

    print("=" * 80)
    print(f"Analyzer Script  : {args.analyzer}")
    print(f"Scanning Dir     : {args.input}")
    print(f"Filter Pattern   : '{args.pattern}' (Only matching files will run)")
    print(f"Output Dir       : {RESULTS_ROOT_DIR}")
    print(f"Max Cores        : {args.cores}")
    print("=" * 80)

    # === 扫描文件 (带过滤) ===
    tasks = []
    print(f"Scanning for .csv.gz files containing '{args.pattern}'...")
    
    for root, dirs, files in os.walk(args.input):
        rel_folder = os.path.relpath(root, args.input)
        for file in files:
            # 1. 必须是 csv.gz
            if not file.endswith('.csv.gz'):
                continue
                
            # 2. 必须包含指定的 Pattern (默认 L1D)
            if args.pattern not in file:
                continue

            full_path = os.path.join(root, file)
            tasks.append((
                rel_folder,
                file,
                full_path,
                args.analyzer,
                RESULTS_ROOT_DIR
            ))

    total_tasks = len(tasks)
    if total_tasks == 0:
        print(f"No files found matching pattern '{args.pattern}'. Exiting.")
        sys.exit(0)
    
    print(f"Found {total_tasks} files matching '{args.pattern}'.")
    
    # === 执行循环 ===
    pending_tasks = list(tasks)
    running_futures = set()
    finished_count = 0
    success_count = 0
    
    ensure_dir(RESULTS_ROOT_DIR)
    executor = ProcessPoolExecutor(max_workers=args.cores)

    try:
        while pending_tasks or running_futures:
            # A. 检查完成
            done_futures = {f for f in running_futures if f.done()}
            for f in done_futures:
                res = f.result()
                finished_count += 1
                if res['status'] == "SUCCESS":
                    success_count += 1
                    print(f"[{finished_count}/{total_tasks}] DONE: {res['file']} ({res['duration']:.2f}s)")
                else:
                    msg = res.get('error_msg', 'Check log')
                    print(f"[{finished_count}/{total_tasks}] FAIL: {res['file']} | {msg}")
            running_futures -= done_futures

            # B. 提交新任务
            while pending_tasks and len(running_futures) < args.cores:
                current_busy_cores, current_usage_pct = get_instant_busy_cores()
                if current_busy_cores >= args.cores:
                    sys.stdout.write(f"\r[Load Monitor] {int(current_busy_cores)} cores busy. Waiting...   ")
                    sys.stdout.flush()
                    time.sleep(1)
                    break
                
                task = pending_tasks.pop(0)
                future = executor.submit(run_single_analysis, task)
                running_futures.add(future)
                sys.stdout.write("\r" + " " * 60 + "\r")
            
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopping...")
        executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(130)
    finally:
        executor.shutdown(wait=True)

    print("-" * 80)
    print(f"Finished. Success: {success_count}/{total_tasks}")
    print(f"Analysis logs saved to: {RESULTS_ROOT_DIR}")

if __name__ == "__main__":
    main()