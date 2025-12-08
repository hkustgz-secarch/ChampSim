import struct
import gzip
import sys
import argparse
from collections import defaultdict

# ================= 配置区域 =================
NUM_INSTR_DESTINATIONS = 2
NUM_INSTR_SOURCES = 4
NUM_INSTR_DESTINATIONS_SPARC = 4 
NUM_INSTR_SOURCES_SPARC = 4 
# ===========================================

class TraceReader:
    def __init__(self, file_path, fmt_type='standard'):
        self.file_path = file_path
        self.fmt_type = fmt_type
        self.fmt_str, self.struct_size = self._get_format_string()

    def _get_format_string(self):
        if self.fmt_type == 'standard':
            fmt = f'<QBB{NUM_INSTR_DESTINATIONS}B{NUM_INSTR_SOURCES}B{NUM_INSTR_DESTINATIONS}Q{NUM_INSTR_SOURCES}Q'
            size = 8 + 1 + 1 + NUM_INSTR_DESTINATIONS + NUM_INSTR_SOURCES + (NUM_INSTR_DESTINATIONS * 8) + (NUM_INSTR_SOURCES * 8)
            return fmt, size
        elif self.fmt_type == 'cloudsuite':
            fmt = f'<QBB{NUM_INSTR_DESTINATIONS_SPARC}B{NUM_INSTR_SOURCES_SPARC}B{NUM_INSTR_DESTINATIONS_SPARC}Q{NUM_INSTR_SOURCES_SPARC}Q2B'
            size = (8 + 1 + 1 + NUM_INSTR_DESTINATIONS_SPARC + NUM_INSTR_SOURCES_SPARC + (NUM_INSTR_DESTINATIONS_SPARC * 8) + (NUM_INSTR_SOURCES_SPARC * 8) + 2)
            return fmt, size
        else:
            raise ValueError("Unknown format type")

    def parse_instructions(self, skip=0, run_limit=None):
        open_func = gzip.open if self.file_path.endswith('.gz') else open
        
        d_mem_count = NUM_INSTR_DESTINATIONS if self.fmt_type == 'standard' else NUM_INSTR_DESTINATIONS_SPARC
        s_mem_count = NUM_INSTR_SOURCES if self.fmt_type == 'standard' else NUM_INSTR_SOURCES_SPARC
        d_reg_count = NUM_INSTR_DESTINATIONS if self.fmt_type == 'standard' else NUM_INSTR_DESTINATIONS_SPARC
        s_reg_count = NUM_INSTR_SOURCES if self.fmt_type == 'standard' else NUM_INSTR_SOURCES_SPARC

        try:
            with open_func(self.file_path, 'rb') as f:
                if skip > 0:
                    print(f"Skipping first {skip} instructions...")
                    chunk_size = 10000 
                    bytes_per_chunk = self.struct_size * chunk_size
                    full_chunks = skip // chunk_size
                    remainder = skip % chunk_size
                    for _ in range(full_chunks):
                        if len(f.read(bytes_per_chunk)) < bytes_per_chunk: return
                    f.read(self.struct_size * remainder)

                print(f"Analyzing {run_limit if run_limit else 'all'} instructions...")
                analyzed_count = 0
                while True:
                    if run_limit and analyzed_count >= run_limit: break
                    data = f.read(self.struct_size)
                    if len(data) < self.struct_size: break

                    try:
                        unpacked = struct.unpack(self.fmt_str, data)
                    except struct.error: break

                    idx = 0
                    ip = unpacked[idx]; idx += 1
                    idx += 2 
                    idx += d_reg_count + s_reg_count 
                    idx += d_mem_count 

                    src_mem = unpacked[idx : idx + s_mem_count]
                    valid_accesses = [m for m in src_mem if m != 0]

                    yield {
                        'ip': ip,
                        'access_count': len(valid_accesses),
                        'accesses': valid_accesses
                    }
                    analyzed_count += 1
                    
        except FileNotFoundError:
            print(f"Error: File {self.file_path} not found.")

class TraceAnalyzer:
    def __init__(self):
        self.ip_access_history = defaultdict(list)
        self.multi_access_stats = defaultdict(int) 
        self.total_instr_analyzed = 0

    def add_data(self, instr):
        self.total_instr_analyzed += 1
        count = instr['access_count']
        self.multi_access_stats[count] += 1
        if instr['accesses']:
            self.ip_access_history[instr['ip']].extend(instr['accesses'])

    def print_multi_access_report(self):
        print(f"\n{'='*20} Instruction Memory Access Distribution {'='*20}")
        print(f"Total Instructions Analyzed: {self.total_instr_analyzed}")
        print("-" * 60)
        print(f"{'Accesses per Instr':<20} {'Count':<15} {'Percentage'}")
        print("-" * 60)
        for num_acc in sorted(self.multi_access_stats.keys()):
            count = self.multi_access_stats[num_acc]
            pct = (count / self.total_instr_analyzed) * 100
            desc = ""
            if num_acc == 0: desc = "(Compute only / No Load)"
            elif num_acc == 1: desc = "(Standard Load)"
            elif num_acc > 1: desc = "<-- MULTI-ACCESS (SIMD/Split)"
            print(f"{num_acc:<20} {count:<15} {pct:<6.2f}% {desc}")
        total_reads = sum(k * v for k, v in self.multi_access_stats.items())
        print("-" * 60)
        print(f"Total logical read addresses found: {total_reads}")

    def analyze_ip_strides(self, top_n=10):
        print(f"\n{'='*20} IP Stride Analysis (Summary) {'='*20}")
        sorted_ips = sorted(self.ip_access_history.items(), key=lambda x: len(x[1]), reverse=True)
        print(f"{'IP (Hex)':<18} {'Read Count':<15} {'Pattern Analysis'}")
        print("-" * 80)
        for ip, addrs in sorted_ips[:top_n]:
            if len(addrs) < 2: continue
            strides = [addrs[i] - addrs[i-1] for i in range(1, len(addrs))]
            stride_counts = defaultdict(int)
            for s in strides: stride_counts[s] += 1
            most_common_stride = max(stride_counts, key=stride_counts.get)
            frequency = stride_counts[most_common_stride]
            pattern = f"Stride {most_common_stride}" if most_common_stride != 0 else "Constant"
            print(f"{hex(ip):<18} {len(addrs):<15} {pattern} ({frequency/len(strides):.1%})")

    # ==================================================
    # [新增功能] 输出 Top IP 的前 N 次访存历史
    # ==================================================
    def print_top_ip_history(self, top_n=10, history_limit=100):
        print(f"\n{'='*20} Top {top_n} IPs: First {history_limit} Accesses {'='*20}")
        
        # 1. 按访存总次数从高到低排序 IP
        sorted_ips = sorted(self.ip_access_history.items(), key=lambda x: len(x[1]), reverse=True)
        
        # 2. 遍历前 Top N 个 IP
        for i, (ip, addrs) in enumerate(sorted_ips[:top_n]):
            print(f"\nRank #{i+1} | IP: {hex(ip)} | Total Reads: {len(addrs)}")
            print("-" * 60)
            
            # 3. 截取前 history_limit 个地址
            history_slice = addrs[:history_limit]
            
            # 4. 格式化输出 (每行显示 5 个地址)
            hex_history = [hex(addr) for addr in history_slice]
            
            # 按每行5个打印，保持整洁
            chunk_size = 5
            for j in range(0, len(hex_history), chunk_size):
                chunk = hex_history[j:j+chunk_size]
                # 打印行号和地址
                print(f"  [{j:03d}-{j+len(chunk)-1:03d}]: " + ", ".join(chunk))
                
            if len(addrs) > history_limit:
                print(f"  ... (remaining {len(addrs) - history_limit} accesses omitted)")

def main():
    parser = argparse.ArgumentParser(description="ChampSim Trace Analysis Tool")
    parser.add_argument('input_file', help="Path to trace file")
    parser.add_argument('-s', '--skip', type=int, default=0, help="Instructions to skip")
    parser.add_argument('-r', '--run', type=int, default=100000, help="Instructions to analyze")
    parser.add_argument('--history', type=int, default=100, help="Number of history records to show for top IPs")
    
    args = parser.parse_args()

    reader = TraceReader(args.input_file)
    analyzer = TraceAnalyzer()

    # 处理数据
    for instr in reader.parse_instructions(skip=args.skip, run_limit=args.run):
        analyzer.add_data(instr)
    
    # 打印报告
    analyzer.print_multi_access_report()
    analyzer.analyze_ip_strides()
    
    # [新增] 调用历史记录打印函数
    analyzer.print_top_ip_history(top_n=10, history_limit=args.history)

if __name__ == "__main__":
    main()