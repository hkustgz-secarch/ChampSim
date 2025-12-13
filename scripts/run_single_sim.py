# Usage: python3 scripts/run_single_sim.py --trace /local-ssd/ben/dpc4-traces/ai-ml/rwkv_trace_1.champsimtrace.gz
# set the DEFAULT_BINARY_NAME and DEFAULT_EXP_NAME
import os
import subprocess
import re
import csv
import sys
import argparse
import time

# ================= 配置区域 (保持与批量脚本一致) =================
TRACE_ROOT = "/local-ssd/ben/dpc4-traces"  # 必须与批量脚本一致，用于计算相对路径
BINARY_DIR = "bin"
RESULTS_ROOT_DIR = "results"

# DEFAULT_BINARY_NAME = "1C.limitBW.baseline"
DEFAULT_BINARY_NAME = "1C.fullBW.baseline"
DEFAULT_EXP_NAME = "baseline"

WARMUP_INST = "50000000"
SIM_INST = "200000000"
# =============================================================

def parse_arguments():
    parser = argparse.ArgumentParser(description="ChampSim Single Trace Repair Runner")
    
    parser.add_argument(
        '--trace', 
        type=str, 
        required=True,
        help='Full path to the trace file (e.g., /local-ssd/.../trace.gz)'
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
        help=f'Experiment name suffix (Must match the batch run)'
    )
    
    return parser.parse_args()

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def parse_output(output_text):
    pattern = r"CPU 0 cumulative IPC:\s+([\d\.]+)\s+instructions:\s+(\d+)\s+cycles:\s+(\d+)"
    match = re.search(pattern, output_text)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, "0", "0"

def get_trace_category(trace_full_path):
    """
    尝试计算 trace 文件相对于 TRACE_ROOT 的路径，
    以此来模拟批量脚本中的 'rel_folder' 逻辑。
    """
    abs_trace = os.path.abspath(trace_full_path)
    abs_root = os.path.abspath(TRACE_ROOT)

    if abs_trace.startswith(abs_root):
        # 如果文件在 TRACE_ROOT 下，计算相对路径作为 category
        rel_path = os.path.relpath(os.path.dirname(abs_trace), abs_root)
        if rel_path == ".":
            return "root"
        return rel_path
    else:
        # 如果文件不在 TRACE_ROOT 下（比如你考到了别的盘），就用父文件夹名
        print(f"Warning: Trace is not inside {TRACE_ROOT}. Using parent folder name as category.")
        return os.path.basename(os.path.dirname(abs_trace))

def main():
    args = parse_arguments()

    # 1. 路径构建 (必须与批量脚本完全一致)
    cwd = os.getcwd()
    binary_full_path = os.path.join(cwd, BINARY_DIR, args.binary)
    
    # 实验 ID 和 输出路径
    experiment_id = f"{args.binary}_{args.name}"
    log_dir = os.path.join(cwd, RESULTS_ROOT_DIR, 'log', experiment_id)
    csv_file_path = os.path.join(cwd, RESULTS_ROOT_DIR, f"{experiment_id}.csv")

    trace_full_path = os.path.abspath(args.trace)
    trace_filename = os.path.basename(trace_full_path)
    
    # 获取类别 (Category/Folder)
    rel_folder = get_trace_category(trace_full_path)

    # 检查
    if not os.path.exists(binary_full_path):
        print(f"Error: Binary not found: {binary_full_path}")
        sys.exit(1)
    if not os.path.exists(trace_full_path):
        print(f"Error: Trace not found: {trace_full_path}")
        sys.exit(1)

    # 确保日志目录存在
    ensure_dir(log_dir)

    print("=" * 80)
    print(f"REPAIR MODE: Appending to existing experiment")
    print(f"CSV File   : {csv_file_path}")
    print(f"Log Dir    : {log_dir}")
    print(f"Trace      : {trace_filename}")
    print(f"Category   : {rel_folder}")
    print("=" * 80)

    # 2. 准备命令
    cmd = [
        binary_full_path,
        "--warmup-instructions", WARMUP_INST,
        "--simulation-instructions", SIM_INST,
        trace_full_path
    ]

    # 3. 执行仿真
    print(f"Running {trace_filename} ... ", end="", flush=True)
    start_time = time.time()
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        duration = time.time() - start_time
        output_content = result.stdout

        # 4. 保存日志 (逻辑与批量脚本一致：safe_rel_folder)
        safe_rel_folder = rel_folder.replace(os.sep, '_')
        log_filename = f"{safe_rel_folder}_{trace_filename}.log"
        log_path = os.path.join(log_dir, log_filename)

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        # 5. 解析结果
        ipc, insts, cycles = parse_output(output_content)
        status = "SUCCESS" if ipc else "PARSE_FAIL"

        if status == "SUCCESS":
            print(f"DONE (IPC: {ipc}, Time: {duration:.2f}s)")
        else:
            print(f"FAILED (See log: {log_filename})")

        # 6. 追加写入 CSV
        # 检查 CSV 是否存在，如果不存在说明可能路径错了，或者这是一个新实验
        file_exists = os.path.isfile(csv_file_path)
        
        with open(csv_file_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            # 如果文件不存在，先写表头（防止是新文件）
            if not file_exists:
                writer.writerow(['Category_Folder', 'Trace_Name', 'IPC', 'Instructions', 'Cycles', 'Status', 'Duration(s)'])
            
            # 写入数据行
            writer.writerow([
                rel_folder, 
                trace_filename, 
                ipc if ipc else "N/A", 
                insts, 
                cycles, 
                status, 
                f"{duration:.2f}"
            ])
            
        print(f"Result appended to {os.path.basename(csv_file_path)}")

    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()