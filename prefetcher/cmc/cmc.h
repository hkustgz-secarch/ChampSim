#ifndef CMC_PREFETCHER_H
#define CMC_PREFETCHER_H

#include <vector>
#include <deque>
#include <list>
#include <unordered_map>
#include <cstdint>

#include "modules.h"
#include "address.h"

class cmc : public champsim::modules::prefetcher {
public:
    // 使用基类构造函数
    using prefetcher::prefetcher;

    // --- 内部结构定义 ---
    struct RecordEntry {
        uint64_t pc;
        uint64_t addr;
        RecordEntry(uint64_t p, uint64_t a) : pc(p), addr(a) {}
        RecordEntry() : pc(0), addr(0) {}
    };

    struct StorageEntry {
        bool valid;
        uint64_t tag;
        uint64_t lru_tick;
        std::vector<uint64_t> addresses;
        int refcnt;
        uint64_t id;
        StorageEntry() : valid(false), tag(0), lru_tick(0), refcnt(0), id(0) {}
    };

    class Recorder {
    public:
        std::vector<uint64_t> entries;
        int index;
        const int degree;

        Recorder(int d);
        bool entry_empty();
        bool train_entry(uint64_t addr, bool *finished);
        void reset();
    };

    // --- ChampSim 必需成员与接口重写 ---

    // 初始化
    void prefetcher_initialize();

    // 核心操作
    uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in);

    // 填充时的回调 (本算法暂未使用，直接透传)
    uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);

    // 其他生命周期钩子
    void prefetcher_cycle_operate();
    void prefetcher_final_stats();
    void prefetcher_branch_operate(champsim::address ip, uint8_t branch_type, champsim::address branch_target);

private:
    // --- CMC 逻辑参数 ---
    static const int STORAGE_SETS = 64;
    static const int STORAGE_WAYS = 16;
    static const int MAX_DEGREE = 16;
    static const int STACK_SIZE = 4;
    static const int FILTER_SIZE = 32;

    // --- 内部状态变量 ---
    // 使用成员初始化列表初始化 recorder
    Recorder recorder{MAX_DEGREE};
    uint64_t acc_id = 1;
    uint64_t current_tick = 0;
    
    std::deque<RecordEntry> trigger;
    std::vector<std::vector<StorageEntry>> storage;

    // 过滤器状态
    std::list<uint64_t> filter_lru_list;
    std::unordered_map<uint64_t, std::list<uint64_t>::iterator> filter_map;

    // --- 内部辅助函数 ---
    uint64_t hash_index(uint64_t block_addr, uint64_t pc);
    uint64_t block_address(uint64_t addr);

    // 过滤器逻辑
    bool filter_check_and_add(uint64_t addr);

    // Storage 逻辑
    StorageEntry* find_entry(uint64_t key);
    StorageEntry* find_victim(uint64_t key);
    void insert_entry(uint64_t key, const std::vector<uint64_t>& data, uint64_t id);
    void update_lru_tick(StorageEntry* entry);
    void invalidate_entry(StorageEntry* entry);

    // 核心计算函数 (内部仍使用 uint64_t 以保持逻辑一致)
    std::vector<uint64_t> calculate_prefetch(uint64_t pc, uint64_t vaddr, bool cache_hit);
};

#endif // CMC_PREFETCHER_H