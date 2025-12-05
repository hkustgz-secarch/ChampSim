import struct
import gzip
import sys
import argparse
import os

# ================= 配置区域 (对应 C++ 宏定义) =================
# Standard ChampSim Settings
NUM_INSTR_DESTINATIONS = 2
NUM_INSTR_SOURCES = 4

# CloudSuite / SPARC Settings (根据常见的 CloudSuite 配置设定)
# 注意：如果你的 trace 生成器使用了不同的数值，请在此修改
NUM_INSTR_DESTINATIONS_SPARC = 4 
# CloudSuite 通常沿用 standard sources 数量，但也可能是 4，这里假设为 4
NUM_INSTR_SOURCES_SPARC = 4 

# ============================================================

def get_format_string(fmt_type):
    """
    根据格式类型生成 struct.unpack 使用的格式字符串
    参考: https://docs.python.org/3/library/struct.html
    < : 小端序 (Little Endian)
    Q : unsigned long long (8 bytes)
    B : unsigned char (1 byte)
    """
    if fmt_type == 'standard':
        # 对应 input_instr
        # IP(8) + Br(1) + Taken(1) + DestRegs(2*1) + SrcRegs(4*1) + DestMem(2*8) + SrcMem(4*8)
        # Total = 64 bytes
        fmt = f'<QBB{NUM_INSTR_DESTINATIONS}B{NUM_INSTR_SOURCES}B{NUM_INSTR_DESTINATIONS}Q{NUM_INSTR_SOURCES}Q'
        size = 8 + 1 + 1 + NUM_INSTR_DESTINATIONS + NUM_INSTR_SOURCES + (NUM_INSTR_DESTINATIONS * 8) + (NUM_INSTR_SOURCES * 8)
        return fmt, size
    
    elif fmt_type == 'cloudsuite':
        # 对应 cloudsuite_instr
        # IP(8) + Br(1) + Taken(1) + DestRegs(SPARC) + SrcRegs(SPARC) + DestMem(SPARC) + SrcMem(SPARC) + ASID(2)
        fmt = f'<QBB{NUM_INSTR_DESTINATIONS_SPARC}B{NUM_INSTR_SOURCES_SPARC}B{NUM_INSTR_DESTINATIONS_SPARC}Q{NUM_INSTR_SOURCES_SPARC}Q2B'
        size = (8 + 1 + 1 + 
                NUM_INSTR_DESTINATIONS_SPARC + NUM_INSTR_SOURCES_SPARC + 
                (NUM_INSTR_DESTINATIONS_SPARC * 8) + (NUM_INSTR_SOURCES_SPARC * 8) + 
                2) # asid is 2 bytes
        return fmt, size
    else:
        raise ValueError("Unknown format type")

def print_header(fmt_type, out_stream):
    header = f"{'ID':<6} {'IP (Hex)':<18} {'Br':<3} {'Tkn':<3} {'Dest Regs':<15} {'Src Regs':<15} {'Dest Mem (First)':<20} {'Src Mem (First)':<20}"
    if fmt_type == 'cloudsuite':
        header += f" {'ASID':<10}"
    out_stream.write(header + '\n')
    out_stream.write("-" * len(header) + '\n')

def format_regs(regs):
    # 过滤掉 0 的寄存器，保持输出简洁
    return str([r for r in regs if r != 0])

def process_trace(file_path, fmt_type, num_instr, out_stream):
    fmt_str, struct_size = get_format_string(fmt_type)
    
    # 检测是否为 gz 文件
    open_func = gzip.open if file_path.endswith('.gz') else open
    
    try:
        with open_func(file_path, 'rb') as f:
            print_header(fmt_type, out_stream)
            
            instr_count = 0
            while instr_count < num_instr:
                data = f.read(struct_size)
                
                # 如果读不到足够的数据（文件结束），则退出
                if len(data) < struct_size:
                    break
                
                try:
                    unpacked = struct.unpack(fmt_str, data)
                except struct.error as e:
                    out_stream.write(f"Error unpacking instruction {instr_count}: {e}\n")
                    break

                # 解析数据 (利用切片将扁平的 tuple 还原为逻辑分组)
                idx = 0
                
                # 1. IP
                ip = unpacked[idx]; idx += 1
                
                # 2. Branch Info
                is_branch = unpacked[idx]; idx += 1
                branch_taken = unpacked[idx]; idx += 1
                
                # 3. Registers
                if fmt_type == 'standard':
                    d_regs_count = NUM_INSTR_DESTINATIONS
                    s_regs_count = NUM_INSTR_SOURCES
                else:
                    d_regs_count = NUM_INSTR_DESTINATIONS_SPARC
                    s_regs_count = NUM_INSTR_SOURCES_SPARC
                
                dest_regs = unpacked[idx : idx + d_regs_count]
                idx += d_regs_count
                
                src_regs = unpacked[idx : idx + s_regs_count]
                idx += s_regs_count
                
                # 4. Memory
                # 注意：根据结构体定义，cloudsuite 的 memory 数量也跟 dest_regs_sparc 对应
                if fmt_type == 'standard':
                    d_mem_count = NUM_INSTR_DESTINATIONS
                    s_mem_count = NUM_INSTR_SOURCES
                else:
                    d_mem_count = NUM_INSTR_DESTINATIONS_SPARC
                    s_mem_count = NUM_INSTR_SOURCES_SPARC

                dest_mem = unpacked[idx : idx + d_mem_count]
                idx += d_mem_count
                
                src_mem = unpacked[idx : idx + s_mem_count]
                idx += s_mem_count
                
                # 5. ASID (仅 CloudSuite)
                asid = None
                if fmt_type == 'cloudsuite':
                    asid = unpacked[idx : idx + 2] # 2 bytes
                    idx += 2

                # --- 输出格式化 ---
                # 为了简洁，内存地址只打印数组中的第一个非零值，或者 'N/A'
                d_mem_str = hex(dest_mem[0]) if dest_mem[0] != 0 else ""
                s_mem_str = hex(src_mem[0]) if src_mem[0] != 0 else ""
                
                line = (f"{instr_count:<6} {hex(ip):<18} {is_branch:<3} {branch_taken:<3} "
                        f"{format_regs(dest_regs):<15} {format_regs(src_regs):<15} "
                        f"{d_mem_str:<20} {s_mem_str:<20}")
                
                if asid:
                    line += f" {str(asid):<10}"
                
                out_stream.write(line + '\n')
                instr_count += 1

    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

def main():
    parser = argparse.ArgumentParser(description="Read ChampSim binary trace files (.gz supported).")
    
    parser.add_argument('input_file', help="Path to the input trace file (e.g., trace.champsimtrace.gz)")
    parser.add_argument('-n', '--count', type=int, default=20, help="Number of instructions to read (default: 20)")
    parser.add_argument('-f', '--format', choices=['standard', 'cloudsuite'], default='standard', 
                        help="Trace format type: 'standard' or 'cloudsuite' (default: standard)")
    parser.add_argument('-o', '--output', help="Output file path. If not specified, prints to stdout.")
    
    args = parser.parse_args()

    # 设置输出流
    if args.output:
        out_stream = open(args.output, 'w')
    else:
        out_stream = sys.stdout

    try:
        process_trace(args.input_file, args.format, args.count, out_stream)
    finally:
        if args.output:
            out_stream.close()
            print(f"Output written to {args.output}")

if __name__ == "__main__":
    main()