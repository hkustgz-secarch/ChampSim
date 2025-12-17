#ifndef PREFETCHER_SMART_STRIDE_H
#define PREFETCHER_SMART_STRIDE_H

#include <cstdint>
#include <vector>
#include <cmath>
#include <algorithm>
#include "modules.h"

// 预取表项定义
struct SmartStrideEntry {
    uint64_t tag = 0;          // PC Tag
    uint64_t last_addr = 0;    // 上次访问地址
    int64_t stride = 0;        // 学习到的步幅
    int conf = 0;              // 置信度 (0-3)
    int depth = 1;             // 动态预取深度
    
    // 迟滞计数器 (用于判断预取是太早还是太晚)
    // 范围 0-15，初始值 7。
    // >12 表示太晚(Late) -> 需要增加深度
    // <3  表示太早/准确(Timely) -> 可以减小深度
    int late_conf = 7;         
    
    uint64_t lru_cycle = 0;    // 用于替换策略
    bool valid = false;
};

class smart_stride : public champsim::modules::prefetcher {
public:
    using prefetcher::prefetcher;

    // --- 参数配置 ---
    static const int NUM_SETS = 64;       // 组数
    static const int NUM_WAYS = 4;        // 路数
    static const int MAX_CONF = 3;        // 最大置信度
    static const int MAX_DEPTH = 16;      // 最大预取深度限制
    static const int BLOCK_SIZE = 64;     // Cache行大小
    
    // --- 及时性追踪窗口 ---
    // 用于记录最近发出的预取地址，以检测 Cache Miss 是否是因为预取太晚导致的
    static const int RECENT_WINDOW_SIZE = 256; 

    // --- 数据结构 ---
    SmartStrideEntry table[NUM_SETS][NUM_WAYS];
    
    // 循环队列实现 Recent Prefetches
    uint64_t recent_prefetches[RECENT_WINDOW_SIZE];
    int recent_head = 0;

    // --- 接口函数 ---
    uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in);
    uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);

private:
    // --- 辅助函数 ---
    uint64_t strideHashPc(uint64_t pc);
    
    // 记录发出的预取
    void addRecentPrefetch(uint64_t addr);
    // 检查地址是否最近被预取过
    bool hasRecentlyPrefetched(uint64_t addr);
};

#endif