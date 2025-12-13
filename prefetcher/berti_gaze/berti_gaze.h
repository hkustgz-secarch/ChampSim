#ifndef BERTI_GAZE_H
#define BERTI_GAZE_H

#include "modules.h"
#include "berti_parameters.h"
#include <vector>
#include <map>
#include <queue>
#include <tuple>
#include <cstdint>
#include <memory>
#include <algorithm>
#include <iostream>

class CACHE;

// 保持 Berti 原有的参数宏定义，或者确保 berti_parameters.h 存在
#ifndef NUM_BLOCKS
#define NUM_BLOCKS 64
#endif

namespace berti_space {
    
    // --- 辅助结构体定义保持不变，但为了封装性，之后会作为成员变量 ---

    typedef struct Delta {
        uint64_t conf;
        int64_t delta;
        uint8_t rpl;
        Delta() : conf(0), delta(0), rpl(BERTI_R) {};
    } delta_t;

    // LatencyTable
    class LatencyTable {
    private:
        struct latency_table {
            uint64_t addr = 0; 
            uint64_t tag = 0;  
            uint64_t time = 0; 
            bool pf = false;   
        };
        int size;
        latency_table* latencyt;

    public:
        LatencyTable(const int p_size) : size(size) {
            latencyt = new latency_table[size];
        }
        ~LatencyTable() { delete[] latencyt; }
        uint8_t add(uint64_t addr, uint64_t tag, bool pf, uint64_t cycle);
        uint64_t get(uint64_t addr);
        uint64_t del(uint64_t addr);
        uint64_t get_tag(uint64_t addr);
    };

    // ShadowCache
    class ShadowCache {
    private:
        struct shadow_cache {
            uint64_t addr = 0; 
            uint64_t lat = 0;  
            bool pf = false;   
        }; 
        int sets;
        int ways;
        std::vector<std::vector<shadow_cache>> scache;

    public:
        ShadowCache(const int sets, const int ways);
        ~ShadowCache() {}
        bool add(uint32_t set, uint32_t way, uint64_t addr, bool pf, uint64_t lat);
        bool get(uint64_t addr);
        void set_pf(uint64_t addr, bool pf);
        bool is_pf(uint64_t addr);
        uint64_t get_latency(uint64_t addr);
    };

    // HistoryTable
    class HistoryTable {
    private:
        struct history_table {
            uint64_t tag = 0;  
            uint64_t addr = 0; 
            uint64_t time = 0; 
        };
        const int sets = HISTORY_TABLE_SETS;
        const int ways = HISTORY_TABLE_WAYS;

        history_table** historyt;
        history_table** history_pointers;
        uint16_t get_aux(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle);

    public:
        HistoryTable();
        ~HistoryTable();
        void add(uint64_t tag, uint64_t addr, uint64_t cycle);
        uint16_t get(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle);
    };

    // Berti Core Logic
    class Berti {
    private:
        struct berti {
            std::vector<delta_t> deltas;
            uint64_t conf;
        };
        std::map<uint64_t, berti*> bertit;
        std::queue<uint64_t> bertit_queue;
        uint64_t size = 0;

        static bool compare_greater_delta(delta_t a, delta_t b);
        static bool compare_rpl(delta_t a, delta_t b);
        void increase_conf_tag(uint64_t tag);
        void add(uint64_t tag, int64_t delta);

    public:
        // HistoryTable 需要在这个类里被访问，为了简化，通过引用传递或者外部协调
        // 这里为了适配原逻辑，保持原样，但在外部调用时传入必要信息
        Berti(uint64_t p_size) : size(p_size) {};
        // 注意：find_and_update 需要 HistoryTable 的引用，因为原代码是通过全局数组访问的
        void find_and_update(HistoryTable& history_table, uint64_t latency, uint64_t tag, uint64_t cycle, uint64_t line_addr);
        uint8_t get(uint64_t tag, std::vector<delta_t>& res);
        uint64_t ip_hash(uint64_t ip);
    };
}

// Stats struct
typedef struct welford {
    uint64_t num = 0;
    float average = 0.0;
} welford_t;

// --- 核心修改：定义符合 ChampSim v3 标准的类 ---
class berti_gaze : public champsim::modules::prefetcher {
public:
    // 成员变量替代原有的全局 vector
    // 使用指针或直接对象，这里使用指针以便在构造函数中初始化
    std::unique_ptr<berti_space::LatencyTable> latency_table;
    std::unique_ptr<berti_space::ShadowCache> shadow_cache;
    std::unique_ptr<berti_space::HistoryTable> history_table;
    std::unique_ptr<berti_space::Berti> berti_core;

    // 统计数据 (每个核心独立一份)
    welford_t average_latency;
    uint64_t pf_to_l1 = 0;
    uint64_t pf_to_l2 = 0;
    uint64_t pf_to_l2_bc_mshr = 0;
    uint64_t cant_track_latency = 0;
    uint64_t cross_page = 0;
    uint64_t no_cross_page = 0;
    uint64_t cross_page_issued = 0;
    uint64_t no_cross_page_issued = 0;
    uint64_t no_found_berti = 0;
    uint64_t found_berti = 0;
    uint64_t average_issued = 0;
    uint64_t average_num = 0;
    
    // 用于 delta 统计
    std::vector<int> delta_counter;

    // 构造函数
    berti_gaze(CACHE* cache);
    ~berti_gaze(); // 析构函数清理内存

    berti_gaze(berti_gaze&&) = default;

    // 标准接口函数
    uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in);
    uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
    void prefetcher_cycle_operate();
    void prefetcher_final_stats();
};

#endif