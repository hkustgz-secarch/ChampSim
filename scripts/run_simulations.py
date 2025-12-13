# Usage Example: 
# python3 scripts/run_simulations.py --binary 1C.fullBW --cores 192 --trace cpu0_L2C --trace_dir /data/my_traces

import os
import subprocess
import re
import csv
import sys
import multiprocessing
import argparse
import time
from concurrent.futures import ProcessPoolExecutor

# ================= 默认配置区域 =================
TRACE_ROOT = "/local-ssd/ben/dpc4-traces"
BINARY_DIR = "bin" 
DEFAULT_BINARY_NAME = "1C.fullBW.baseline"
RESULTS_ROOT_DIR = "results"
DEFAULT_EXP_NAME = "default"

# 默认 Trace 输出根目录
DEFAULT_TRACE_OUTPUT = os.path.join(RESULTS_ROOT_DIR, "traces")

WARMUP_INST = "50000000"
SIM_INST = "200000000"

# 默认最大核心数
DEFAULT_CORES = max(1, multiprocessing.cpu_count() // 2) 
# ===========================================

def parse_arguments():
    parser = argparse.ArgumentParser(description="ChampSim Runner (Instant /proc/stat Monitor)")
    
    parser.add_argument(
        '--cores', 
        type=int, 
        default=DEFAULT_CORES,
        help=f'Max Busy Cores Limit. (Default: {DEFAULT_CORES})'
    )
    
    parser.add_argument(
        '--binary', 
        type=str, 
        default=DEFAULT_BINARY_NAME,
        help=f'Binary filename inside {BINARY_DIR}/'
    )
    
    parser.add_argument(
        '--name', 
        type=str, 
        default=DEFAULT_EXP_NAME,
        help=f'Custom experiment name suffix'
    )

    # --- [Trace 控制] ---
    parser.add_argument(
        '--trace',
        type=str,
        default=None,
        help='Set CHAMPSIM_TRACE env var (e.g., "ALL", "cpu0_L2C"). Default: None (Disabled).'
    )

    parser.add_argument(
        '--trace_dir',
        type=str,
        default=DEFAULT_TRACE_OUTPUT,
        help=f'Root directory to save generated traces. Default: {DEFAULT_TRACE_OUTPUT}'
    )
    
    return parser.parse_args()

def ensure_dir(directory):
    if not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            # 忽略多进程同时创建时的冲突错误
            if e.errno != os.errno.EEXIST:
                raise

# === [核心功能] 读取 Linux 内核实时 CPU 数据 ===
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

# ===========================================

def parse_output(output_text):
    pattern = r"CPU 0 cumulative IPC:\s+([\d\.]+)\s+instructions:\s+(\d+)\s+cycles:\s+(\d+)"
    match = re.search(pattern, output_text)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, "0", "0"

def run_single_trace(task_info):
    # 解包参数
    # experiment_id 用于构建子目录
    rel_folder, file_name, full_trace_path, binary_full_path, output_log_dir, trace_config = task_info
    
    trace_arg = trace_config['mode']
    trace_root = trace_config['root_dir']
    experiment_id = trace_config['exp_id']

    trace_msg = f" [TRACE={trace_arg}]" if trace_arg else ""
    print(f"[Started] {rel_folder}/{file_name}{trace_msg}", flush=True) 

    cmd = [
        binary_full_path,
        "--warmup-instructions", WARMUP_INST,
        "--simulation-instructions", SIM_INST,
        full_trace_path
    ]

    # --- [环境变量配置] ---
    env_vars = os.environ.copy()
    
    if trace_arg:
        # 1. 开启 Trace
        env_vars["CHAMPSIM_TRACE"] = trace_arg
        
        # 2. 设置 Trace ID (确保并行唯一性，使用 trace 文件名)
        env_vars["CHAMPSIM_TRACE_ID"] = file_name
        
        # 3. 设置具体的输出目录
        # 结构: <TraceRoot>/<ExperimentID>/<Category>/
        safe_rel_folder = rel_folder.replace(os.sep, '_')
        if safe_rel_folder == ".": safe_rel_folder = "root"
        
        specific_trace_dir = os.path.join(trace_root, experiment_id, safe_rel_folder)
        
        # 确保目录存在 (多进程安全)
        os.makedirs(specific_trace_dir, exist_ok=True)
        
        env_vars["CHAMPSIM_TRACE_DIR"] = specific_trace_dir

    try:
        start_time = time.time()
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            text=True,
            env=env_vars 
        )
        duration = time.time() - start_time
        output_content = result.stdout
        
        # 结果日志保存
        safe_rel_folder = rel_folder.replace(os.sep, '_')
        if safe_rel_folder == ".": safe_rel_folder = "root"
        
        log_filename = f"{safe_rel_folder}_{file_name}.log"
        log_path = os.path.join(output_log_dir, log_filename)
        
        try:
            with open(log_path, 'w', encoding='utf-8') as log_f:
                log_f.write(output_content)
        except Exception as e:
            return {
                "status": "WRITE_ERR", "folder": rel_folder, "trace": file_name,
                "ipc": "N/A", "insts": "0", "cycles": "0", "duration": duration,
                "error_msg": f"Log write failed: {str(e)}"
            }
            
        ipc, insts, cycles = parse_output(output_content)
        status = "SUCCESS" if ipc else "PARSE_FAIL"
        
        return {
            "status": status, "folder": rel_folder, "trace": file_name,
            "ipc": ipc if ipc else "N/A", "insts": insts, "cycles": cycles, "duration": duration
        }

    except Exception as e:
        return {
            "status": "ERROR", "folder": rel_folder, "trace": file_name,
            "ipc": "ERROR", "insts": "0", "cycles": "0", "duration": 0,
            "error_msg": str(e)
        }

# === [安全退出] 清理函数 ===
def cleanup_processes(binary_name):
    print("\n" + "!" * 80)
    print("Force stopping all simulation processes...")
    ret = os.system(f"pkill -f {binary_name}")
    if ret == 0:
        print(f"Successfully killed processes matching '{binary_name}'")
    else:
        print(f"No processes matching '{binary_name}' found or permission denied.")
    print("!" * 80)

def main():
    args = parse_arguments()
    core_limit = args.cores 
    
    # 路径构建
    binary_full_path = os.path.join(os.getcwd(), BINARY_DIR, args.binary)
    experiment_id = f"{args.binary}_{args.name}"
    
    # 日志和 CSV 输出
    current_output_dir = os.path.join(os.getcwd(), RESULTS_ROOT_DIR, 'log', experiment_id)
    csv_file_path = os.path.join(os.getcwd(), RESULTS_ROOT_DIR, f"{experiment_id}.csv")

    print("=" * 80)
    print(f"Running from CWD : {os.getcwd()}")
    print(f"Binary Path      : {binary_full_path}")
    print(f"Log Dir          : {current_output_dir}")
    print(f"CSV Output       : {csv_file_path}")
    print(f"Core Limit       : {core_limit}")
    
    if args.trace:
        # 这里的目录会在 run_single_trace 中结合 experiment_id 进一步细分
        print(f"Trace Enabled    : YES ({args.trace})")
        print(f"Trace Root Dir   : {args.trace_dir}")
    else:
        print(f"Trace Enabled    : NO")
    print("=" * 80)

    if not os.path.exists(binary_full_path):
        print(f"Error: Binary not found: {binary_full_path}")
        sys.exit(1)

    ensure_dir(current_output_dir)
    ensure_dir(os.path.dirname(csv_file_path))
    
    # 预先创建 trace 根目录
    if args.trace:
        ensure_dir(args.trace_dir)

    tasks = []
    print(f"Scanning traces in: {TRACE_ROOT} ...")
    if not os.path.exists(TRACE_ROOT):
        print(f"Error: Trace root not found: {TRACE_ROOT}")
        sys.exit(1)

    # 打包 Trace 配置
    trace_config = {
        'mode': args.trace,
        'root_dir': args.trace_dir,
        'exp_id': experiment_id
    }

    for root, dirs, files in os.walk(TRACE_ROOT):
        rel_folder = os.path.relpath(root, TRACE_ROOT)
        if rel_folder == ".": rel_folder = "root"
        for file in files:
            if file.endswith('.gz') or file.endswith('.xz'):
                tasks.append((
                    rel_folder, 
                    file, 
                    os.path.join(root, file),
                    binary_full_path,
                    current_output_dir,
                    trace_config
                ))
    
    total_tasks = len(tasks)
    if total_tasks == 0:
        print("No traces found. Exiting.")
        sys.exit(0)

    # 准备工作
    pending_tasks = list(tasks)
    running_futures = set()
    finished_count = 0
    success_count = 0
    
    executor = ProcessPoolExecutor(max_workers=core_limit)
    csvfile = open(csv_file_path, 'w', newline='', encoding='utf-8')
    writer = csv.writer(csvfile)
    writer.writerow(['Category_Folder', 'Trace_Name', 'IPC', 'Instructions', 'Cycles', 'Status', 'Duration(s)'])

    print("-" * 80)
    print("Starting simulation... (Press Ctrl+C to Safe Exit)")
    print("-" * 80)

    try:
        while pending_tasks or running_futures:
            # A. 检查已完成任务
            done_futures = {f for f in running_futures if f.done()}
            
            for f in done_futures:
                res = f.result()
                finished_count += 1
                
                writer.writerow([
                    res['folder'], res['trace'], res['ipc'], 
                    res['insts'], res['cycles'], res['status'], 
                    f"{res['duration']:.2f}"
                ])
                csvfile.flush() 
                
                if res['status'] == "SUCCESS":
                    success_count += 1
                    print(f"[{finished_count}/{total_tasks}] DONE: {res['folder']}/{res['trace']} | IPC: {res['ipc']}")
                else:
                    print(f"[{finished_count}/{total_tasks}] FAIL: {res['folder']}/{res['trace']} | {res.get('error_msg')}")

            running_futures -= done_futures

            # B. 提交新任务 (实时 /proc/stat 监控)
            while pending_tasks and len(running_futures) < core_limit:
                
                current_busy_cores, current_usage_pct = get_instant_busy_cores(interval=0.1)
                
                if current_busy_cores >= core_limit:
                    sys.stdout.write(f"\r[Monitor] High Load: {int(current_busy_cores)} busy cores ({current_usage_pct:.1f}%). Limit: {core_limit}. Waiting...   ")
                    sys.stdout.flush()
                    time.sleep(2) 
                    break 
                
                task = pending_tasks.pop(0)
                future = executor.submit(run_single_trace, task)
                running_futures.add(future)
                
                sys.stdout.write("\r" + " " * 80 + "\r")
            
            time.sleep(0.01)

    except KeyboardInterrupt:
        cleanup_processes(args.binary)
        executor.shutdown(wait=False, cancel_futures=True)
        sys.exit(130)

    except Exception as e:
        print(f"Unexpected error: {e}")
        cleanup_processes(args.binary)
        raise

    finally:
        csvfile.close()
        executor.shutdown(wait=True)

    print("-" * 80)
    print(f"All Finished. Success: {success_count}/{total_tasks}")
    print(f"Results saved to: {csv_file_path}")

if __name__ == "__main__":
    main()