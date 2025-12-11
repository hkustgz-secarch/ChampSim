#ifndef CMC_PREFETCHER_H
#define CMC_PREFETCHER_H

#include <vector>
#include <deque>
#include <list>
#include <unordered_map>
#include <cstdint>

// 必须包含 cache.h 以识别 CACHE* 类型
#include "cache.h"

class cmc {
public:
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

    // --- ChampSim 必需成员与接口 ---
    CACHE* cache; 

    // 构造函数
    explicit cmc(CACHE* cache_ptr);
    
    // Bind 函数
    void bind(CACHE* cache_ptr);

    // 标准操作接口
    uint32_t operate(uint64_t addr, uint64_t ip, bool cache_hit, bool useful_prefetch, uint8_t type, uint32_t metadata_in);
    void initialize();
    void cycle_operate();
    void final_stats();
    void branch_operate(uint64_t ip, uint8_t branch_type, uint64_t branch_target);

private:
    // --- CMC 逻辑参数 ---
    static const int STORAGE_SETS = 64;   
    static const int STORAGE_WAYS = 16;   
    static const int MAX_DEGREE = 16;     
    static const int STACK_SIZE = 4;      
    static const int FILTER_SIZE = 32;    

    // --- 内部状态变量 ---
    Recorder recorder;
    uint64_t acc_id;
    uint64_t current_tick; 
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

    // 核心计算函数
    std::vector<uint64_t> calculate_prefetch(uint64_t pc, uint64_t vaddr, bool cache_hit);
};

#endif // CMC_PREFETCHER_H