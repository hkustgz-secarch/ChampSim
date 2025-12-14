import gzip
import csv
import sys
import argparse
from collections import defaultdict, Counter

# ================= 配置区域 =================
# 每个 PC 保留多少条历史记录用于计算 Stride 分布
# 1000 足够统计出稳定的概率分布，同时防止内存溢出
HISTORY_LIMIT = 1000 
# ===========================================

class CsvTraceReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def parse_rows(self, skip=0, run_limit=None):
        open_func = gzip.open if self.file_path.endswith('.gz') else open
        
        try:
            with open_func(self.file_path, 'rt', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                
                analyzed_count = 0
                skipped_count = 0
                
                for row in reader:
                    # 格式: Cycle, IP, Address, Type, Result
                    if len(row) < 5: continue

                    if skip > 0 and skipped_count < skip:
                        skipped_count += 1
                        continue

                    if run_limit and analyzed_count >= run_limit:
                        break

                    try:
                        # 核心：地址转为 CacheLine 粒度
                        raw_addr = int(row[2], 16)
                        block_addr = raw_addr >> 6

                        yield {
                            'cycle': int(row[0]),
                            'ip': int(row[1], 16) if row[1] != '0' else 0,
                            'block_addr': block_addr,
                            'type': row[3],
                            'result': row[4]
                        }
                        analyzed_count += 1

                    except ValueError:
                        continue
                        
        except FileNotFoundError:
            print(f"错误: 找不到文件 {self.file_path}")

class TraceAnalyzer:
    def __init__(self):
        # 1. Load 侧统计 (Demand Side)
        # IP -> {acc, miss}
        self.load_stats = defaultdict(lambda: {'acc': 0, 'miss': 0})
        
        # 2. 访存历史 (用于 Stride 分析)
        self.pc_history = defaultdict(list)
        
        # 3. Prefetch 侧统计 (Supply Side) - Per PC
        # IP -> {issued, useful, late, early_sum}
        self.pf_stats = defaultdict(lambda: {'issued': 0, 'useful': 0, 'late': 0, 'early_sum': 0})

        # 4. 活跃预取池 (Pending Prefetches)
        # BlockAddress -> { 'cycle': int, 'issuer': int }
        self.active_prefetches = {}
        
        # 5. 全局统计
        self.global_stats = {
            'load_access': 0,
            'load_miss': 0,
            'pf_issued': 0
        }

    def process_row(self, data):
        cycle = data['cycle']
        ip = data['ip']
        block_addr = data['block_addr'] # 已经是 CacheLine
        acc_type = data['type']
        result = data['result']
        
        # ===========================
        # A. PREFETCH 处理
        # ===========================
        if 'PREFETCH' in acc_type:
            self.pf_stats[ip]['issued'] += 1
            self.global_stats['pf_issued'] += 1
            
            # 记录活跃预取 (如果重复预取，覆盖为最新的，或者保留最早的)
            # 这里逻辑：更新为最新的预取信息
            self.active_prefetches[block_addr] = {'cycle': cycle, 'issuer': ip}
        
        # ===========================
        # B. LOAD 处理 (Demand)
        # ===========================
        elif 'LOAD' in acc_type:
            # 1. 基础统计
            self.load_stats[ip]['acc'] += 1
            self.global_stats['load_access'] += 1
            
            if result == 'MISS':
                self.load_stats[ip]['miss'] += 1
                self.global_stats['load_miss'] += 1
            
            # 2. 记录历史 (Stride)
            if len(self.pc_history[ip]) < HISTORY_LIMIT:
                self.pc_history[ip].append(block_addr)

            # 3. 验证预取效果
            if block_addr in self.active_prefetches:
                pf_info = self.active_prefetches[block_addr]
                issuer = pf_info['issuer'] # 获取发出这个预取的 PC
                latency_ahead = cycle - pf_info['cycle']
                
                if result == 'HIT':
                    # Useful: 预取了且命中
                    self.pf_stats[issuer]['useful'] += 1
                    self.pf_stats[issuer]['early_sum'] += latency_ahead
                else: 
                    # Late: 预取了但还是 Miss (来晚了)
                    self.pf_stats[issuer]['late'] += 1
                    # Late 的 prefetch 依然算作是对地址预测准确的，只是时机不对
                    # 我们这里不计入 early_sum，或者 latency 可能很小

                # 消费掉这个预取 (One-shot)
                del self.active_prefetches[block_addr]

    def _get_stride_breakdown(self, ip, top_k=3):
        """生成 Stride 分布的详细字符串"""
        history = self.pc_history[ip]
        if len(history) < 2:
            return "N/A"
        
        # 计算 CacheLine Stride
        deltas = [history[i] - history[i-1] for i in range(1, len(history))]
        total = len(deltas)
        if total == 0: return "N/A"
        
        c = Counter(deltas)
        
        # 获取前 K 个主要 Stride
        common = c.most_common(top_k)
        
        parts = []
        for stride, count in common:
            pct = count / total
            parts.append(f"{stride}({pct:.0%})")
            
        return " | ".join(parts)

    # ================= 输出报告 =================

    def print_global_summary(self):
        g = self.global_stats
        miss_rate = (g['load_miss'] / g['load_access'] * 100) if g['load_access'] > 0 else 0
        
        # 汇总所有 PC 的 prefetch 数据
        total_useful = sum(s['useful'] for s in self.pf_stats.values())
        total_late = sum(s['late'] for s in self.pf_stats.values())
        total_issued = g['pf_issued']
        total_useless = total_issued - (total_useful + total_late)
        if total_useless < 0: total_useless = 0

        print(f"\n{'='*40} 0. 全局统计汇总 {'='*40}")
        print(f"Total Load Accesses : {g['load_access']}")
        print(f"Total Load Misses   : {g['load_miss']} ({miss_rate:.2f}%)")
        print("-" * 40)
        print(f"Total PF Issued     : {total_issued}")
        print(f"  - Useful (Hit)    : {total_useful} ({(total_useful/total_issued*100 if total_issued else 0):.1f}%)")
        print(f"  - Late (Miss)     : {total_late} ({(total_late/total_issued*100 if total_issued else 0):.1f}%)")
        print(f"  - Useless         : {total_useless} ({(total_useless/total_issued*100 if total_issued else 0):.1f}%)")

    def print_load_analysis(self, top_n=15):
        print(f"\n{'='*40} 1. PC 级 Load Miss 与 Stride 分布 {'='*40}")
        print(f"注意: Stride 单位为 CacheLine (64B)。例如 '1' 代表连续访问。")
        print("-" * 130)
        print(f"{'Rank':<5} {'IP (Hex)':<14} {'Loads':<9} {'Misses':<8} {'Miss%':<8} {'Top Strides Distribution (Stride: %)'}")
        print("-" * 130)
        
        # 按 Miss 次数排序
        sorted_pcs = sorted(self.load_stats.items(), key=lambda x: x[1]['miss'], reverse=True)
        
        for i, (ip, s) in enumerate(sorted_pcs[:top_n]):
            miss_rate = (s['miss'] / s['acc'] * 100) if s['acc'] > 0 else 0
            stride_str = self._get_stride_breakdown(ip, top_k=4) # 显示前4种
            
            print(f"#{i+1:<4} {hex(ip):<14} {s['acc']:<9} {s['miss']:<8} {miss_rate:<7.1f}% {stride_str}")

    def print_prefetch_analysis(self):
        print(f"\n{'='*40} 2. PC 级 Prefetch 效果分析 {'='*40}")
        print(f"Useful: 预取后 Hit | Late: 预取后 Miss | Useless: 无后续访问")
        print("-" * 130)
        print(f"{'PF Issuer IP':<14} {'Issued':<10} | {'Useful':<8} {'Late':<8} {'Useless':<8} | {'Accuracy%':<10} {'AvgAhead'}")
        print("-" * 130)
        
        # 按发出数量排序
        sorted_pfs = sorted(self.pf_stats.items(), key=lambda x: x[1]['issued'], reverse=True)

        for ip, s in sorted_pfs:
            issued = s['issued']
            useful = s['useful']
            late = s['late']
            useless = issued - (useful + late)
            if useless < 0: useless = 0 # 边界保护
            
            # Accuracy: (Useful + Late) / Issued ? 或者只算 Useful? 
            # 通常定义 Accuracy = (Useful + Late) / Issued (即预测地址是对的)
            # 但这里我们更严格一点，Accuracy = Useful / Issued (既对又及时)
            # 或者列出 Useful% 比较直观
            useful_pct = (useful / issued * 100) if issued > 0 else 0
            
            avg_ahead = (s['early_sum'] / useful) if useful > 0 else 0
            
            # IP 0 通常代表 Hardware Prefetcher
            ip_label = "0 (HW PF)" if ip == 0 else hex(ip)
            
            print(f"{ip_label:<14} {issued:<10} | {useful:<8} {late:<8} {useless:<8} | {useful_pct:<9.1f}% {avg_ahead:<8.1f}")
            
        if not self.pf_stats:
            print("(Trace 中未发现 PREFETCH 记录)")

def main():
    parser = argparse.ArgumentParser(description="Trace Analysis: Per-PC Prefetch & Detailed Stride")
    parser.add_argument('input_file', help="Path to .csv or .csv.gz trace file")
    parser.add_argument('-s', '--skip', type=int, default=0, help="Rows to skip")
    parser.add_argument('-r', '--run', type=int, default=200000000, help="Rows to analyze")
    parser.add_argument('--top', type=int, default=15, help="Number of Top PCs to show")
    
    args = parser.parse_args()

    reader = CsvTraceReader(args.input_file)
    analyzer = TraceAnalyzer()

    # 处理数据
    for data in reader.parse_rows(skip=args.skip, run_limit=args.run):
        analyzer.process_row(data)

    # 输出报告
    analyzer.print_global_summary()
    analyzer.print_load_analysis(top_n=args.top)
    analyzer.print_prefetch_analysis()

if __name__ == "__main__":
    main()