#include "smart_stride.h"
#include "cache.h" // Needed for some constants if strictly required, usually modules.h is enough

// PC 哈希函数：将高位异或到低位，减少冲突
uint64_t smart_stride::strideHashPc(uint64_t pc) {
    uint64_t pc_high_1 = (pc >> 20) & (0x1f);
    uint64_t pc_high_2 = (pc >> 15) & (0x1f);
    uint64_t pc_high_3 = (pc >> 10) & (0x1f);
    uint64_t pc_high = pc_high_1 ^ pc_high_2 ^ pc_high_3;
    uint64_t pc_low = pc & (0x1ff);
    return (pc_high << 10) | pc_low;
}

// 记录预取地址到循环队列
void smart_stride::addRecentPrefetch(uint64_t addr) {
    recent_prefetches[recent_head] = addr;
    recent_head = (recent_head + 1) % RECENT_WINDOW_SIZE;
}

// 线性扫描检查最近是否预取过该地址
bool smart_stride::hasRecentlyPrefetched(uint64_t addr) {
    // 由于窗口较小 (256) 且都在 Cache 中，线性扫描开销可忽略
    for (int i = 0; i < RECENT_WINDOW_SIZE; i++) {
        if (recent_prefetches[i] == addr) return true;
    }
    return false;
}

uint32_t smart_stride::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in)
{
    uint64_t current_addr_val = addr.to<uint64_t>();
    uint64_t ip_val = ip.to<uint64_t>();
    
    // 1. 计算 Index 和 Tag
    uint64_t hash = strideHashPc(ip_val);
    int set_idx = hash % NUM_SETS;
    uint64_t tag = hash / NUM_SETS;

    // 2. 查找 Entry
    int way_idx = -1;
    for (int i = 0; i < NUM_WAYS; i++) {
        if (table[set_idx][i].valid && table[set_idx][i].tag == tag) {
            way_idx = i;
            break;
        }
    }

    if (way_idx != -1) {
        // --- HIT: 在表中找到了该 PC ---
        SmartStrideEntry& entry = table[set_idx][way_idx];
        // entry.lru_cycle = intern_->current_cycle(); // 更新 LRU
        entry.lru_cycle = intern_->current_time.time_since_epoch() / intern_->clock_period;

        int64_t new_stride = (int64_t)current_addr_val - (int64_t)entry.last_addr;
        bool stride_match = false;

        // 过滤掉同一个 Cache Line 内的重复访问
        if (new_stride == 0 || std::abs(new_stride) < BLOCK_SIZE) {
            return metadata_in;
        }

        // --- 步幅匹配逻辑 ---
        // 1. 精确匹配
        if (new_stride == entry.stride) {
            stride_match = true;
        } 
        // 2. 模糊匹配 (Fuzzy Matching): 新步幅是旧步幅的整数倍
        else if (entry.stride > BLOCK_SIZE && std::abs(new_stride) % std::abs(entry.stride) == 0) {
            stride_match = true;
        }

        if (stride_match) {
            // --- 匹配成功 ---
            if (entry.conf < MAX_CONF) entry.conf++;
            
            // --- 及时性 (Timeliness) 自适应深度调整 ---
            // 判断是否 Late: 发生了 Miss，但地址在我们最近发出的预取列表中
            bool is_miss = (cache_hit == 0);
            bool is_late = is_miss && hasRecentlyPrefetched(current_addr_val);
            
            // 判断是否 Timely: 发生了 Hit 且是有用预取
            bool is_timely = (cache_hit == 1) && useful_prefetch;

            if (is_timely) {
                // 预取很及时（甚至可能太早），减少迟滞计数
                if (entry.late_conf > 0) entry.late_conf--;
            } else if (is_late) {
                // 预取太晚了，没赶上 CPU 请求，大幅增加迟滞计数
                entry.late_conf += 3;
            }

            // 边界检查
            if (entry.late_conf > 15) entry.late_conf = 15;

            // 根据迟滞计数调整深度
            if (entry.late_conf >= 12) { 
                // 经常 Late -> 增加深度
                if (entry.depth < MAX_DEPTH) entry.depth++;
                entry.late_conf = 7; // 重置到中点
            } else if (entry.late_conf <= 3) {
                // 非常 Timely -> 尝试减小深度以节省带宽
                if (entry.depth > 1) entry.depth--;
                entry.late_conf = 7; // 重置到中点
            }

            // 更新上次访问地址
            entry.last_addr = current_addr_val;

        } else {
            // --- 匹配失败 ---
            entry.conf--;
            entry.last_addr = current_addr_val;

            if (entry.conf <= 0) {
                // 模式改变，重置 Stride
                entry.stride = new_stride;
                entry.depth = 1;
                entry.conf = 0;
                entry.late_conf = 7; 
            }
        }

        // --- 发送预取请求 ---
        if (entry.conf >= 2) {
            // 如果是 Cache Miss，说明我们落后了，从当前位置更远一点开始发 (depth - 4)
            // 否则从下一个 stride 开始发
            int start_depth = (cache_hit == 0) ? std::max(1, entry.depth - 4) : 1;

            for (int d = start_depth; d <= entry.depth; d++) {
                uint64_t pf_addr_val = current_addr_val + (entry.stride * d);
                
                // 记录发出的请求用于 Late 检测
                addRecentPrefetch(pf_addr_val);
                
                // 将地址转换为 block 对齐并发送
                prefetch_line(champsim::address{pf_addr_val}, true, metadata_in);
            }
        }

    } else {
        // --- MISS: 表中无此 PC，需要插入 ---
        
        // 寻找 Victim (LRU 策略)
        int victim_way = 0;
        uint64_t min_lru = UINT64_MAX;
        
        for (int i = 0; i < NUM_WAYS; i++) {
            // 优先找无效行
            if (!table[set_idx][i].valid) {
                victim_way = i;
                break;
            }
            // 否则找最久未使用的
            if (table[set_idx][i].lru_cycle < min_lru) {
                min_lru = table[set_idx][i].lru_cycle;
                victim_way = i;
            }
        }

        // 初始化新条目
        SmartStrideEntry& entry = table[set_idx][victim_way];
        entry.valid = true;
        entry.tag = tag;
        entry.last_addr = current_addr_val;
        entry.stride = 0;     // 初始未知
        entry.conf = 0;       // 初始置信度低
        entry.depth = 1;      // 初始深度 1
        entry.late_conf = 7;  // 初始 Late 计数中等
        // entry.lru_cycle = intern_->current_cycle();
        entry.lru_cycle = intern_->current_time.time_since_epoch() / intern_->clock_period;

    }

    return metadata_in;
}

uint32_t smart_stride::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in)
{
    // Stride 预取器通常不需要处理 cache fill 事件
    // 除非需要根据 metadata 更新表项，但在本实现中 operate 阶段已经处理完毕
    return metadata_in;
}