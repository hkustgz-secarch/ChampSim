#ifndef HYBRID_BERTIM_CMC_H
#define HYBRID_BERTIM_CMC_H

#include <vector>
#include <deque>
#include <list>
#include <unordered_map>
#include <cstdint>
#include <cmath>
#include <cassert>

#include "modules.h"
#include "address.h"
// cache.h 通常在 ChampSim 编译环境中可用，用于 CACHE* 定义
struct CACHE;

// =============================================================================
// Hybrid Prefetcher Class
// =============================================================================
class hybrid_bertiM_cmc : public champsim::modules::prefetcher {
public:
    CACHE* cache;

    // --- Berti Logic Definitions (Encapsulated) ---
    struct BertiCore {
        CACHE* cache; // Pointer to parent cache

        // Berti Constants
        static constexpr int LOG2_BLOCK_SIZE = 6;
        static constexpr int LOG2_PAGE_SIZE = 12;
        static constexpr int L1D_PAGE_BLOCKS_BITS = (LOG2_PAGE_SIZE - LOG2_BLOCK_SIZE);
        static constexpr int L1D_PAGE_BLOCKS = (1 << L1D_PAGE_BLOCKS_BITS);
        static constexpr int L1D_PAGE_OFFSET_MASK = (L1D_PAGE_BLOCKS - 1);
        static constexpr int L1D_BERTI_THROTTLING = 1;
        static constexpr int L1D_BURST_THROTTLING = 7;
        static constexpr double L1D_BURST_THRESHOLD = 0.99;
        static constexpr int LONG_REUSE_LIMIT = 16;
        static constexpr int L1D_TIME_BITS = 16;
        static constexpr uint64_t L1D_TIME_OVERFLOW = ((uint64_t)1 << L1D_TIME_BITS);
        static constexpr uint64_t L1D_TIME_MASK = (L1D_TIME_OVERFLOW - 1);
        static constexpr int L1D_CURRENT_PAGES_TABLE_INDEX_BITS = 6;
        static constexpr int L1D_CURRENT_PAGES_TABLE_ENTRIES = ((1 << L1D_CURRENT_PAGES_TABLE_INDEX_BITS) - 1);
        static constexpr int L1D_CURRENT_PAGES_TABLE_NUM_BERTI = 8;
        static constexpr int L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS = 8;
        static constexpr int L1D_PREV_REQUESTS_TABLE_INDEX_BITS = 10;
        static constexpr int L1D_PREV_REQUESTS_TABLE_ENTRIES = (1 << L1D_PREV_REQUESTS_TABLE_INDEX_BITS);
        static constexpr int L1D_PREV_REQUESTS_TABLE_MASK = (L1D_PREV_REQUESTS_TABLE_ENTRIES - 1);
        static constexpr int L1D_PREV_REQUESTS_TABLE_NULL_POINTER = L1D_CURRENT_PAGES_TABLE_ENTRIES;
        static constexpr int L1D_LATENCIES_TABLE_INDEX_BITS = 10;
        static constexpr int L1D_LATENCIES_TABLE_ENTRIES = (1 << L1D_LATENCIES_TABLE_INDEX_BITS);
        static constexpr int L1D_LATENCIES_TABLE_MASK = (L1D_LATENCIES_TABLE_ENTRIES - 1);
        static constexpr int L1D_LATENCIES_TABLE_NULL_POINTER = L1D_CURRENT_PAGES_TABLE_ENTRIES;
        static constexpr int L1D_RECORD_PAGES_TABLE_INDEX_BITS = 14;
        static constexpr int L1D_RECORD_PAGES_TABLE_ENTRIES = ((1 << L1D_RECORD_PAGES_TABLE_INDEX_BITS) - 1);
        static constexpr int L1D_TRUNCATED_PAGE_ADDR_BITS = 32;
        static constexpr uint64_t L1D_TRUNCATED_PAGE_ADDR_MASK = (((uint64_t)1 << L1D_TRUNCATED_PAGE_ADDR_BITS) - 1);
        static constexpr int L1D_IP_TABLE_INDEX_BITS = 12;
        static constexpr int L1D_IP_TABLE_ENTRIES = (1 << L1D_IP_TABLE_INDEX_BITS);
        static constexpr int L1D_IP_TABLE_INDEX_MASK = (L1D_IP_TABLE_ENTRIES - 1);

        struct l1d_current_page_entry {
            uint64_t page_addr;
            uint64_t u_vector;
            int berti[L1D_CURRENT_PAGES_TABLE_NUM_BERTI];
            unsigned berti_score[L1D_CURRENT_PAGES_TABLE_NUM_BERTI];
            int current_berti;
            int stride;
            bool short_reuse;
            bool continue_burst;
            uint64_t lru;
        };
        struct l1d_prev_request_entry {
            uint64_t page_addr_pointer;
            uint64_t offset;
            uint64_t time;
        };
        struct l1d_latency_entry {
            uint64_t page_addr_pointer;
            uint64_t offset;
            uint64_t time_lat;
            bool completed;
        };
        struct l1d_record_page_entry {
            uint64_t page_addr;
            uint64_t linnea;
            uint64_t last_offset;
            bool short_reuse;
            uint64_t lru;
        };
        struct l1d_ip_entry {
            bool current;
            int berti_or_pointer;
            bool consecutive;
            bool short_reuse;
        };

        // Berti State
        l1d_current_page_entry current_pages_table[L1D_CURRENT_PAGES_TABLE_ENTRIES];
        l1d_prev_request_entry prev_requests_table[L1D_PREV_REQUESTS_TABLE_ENTRIES];
        uint64_t prev_requests_table_head = 0;
        l1d_latency_entry latencies_table[L1D_LATENCIES_TABLE_ENTRIES];
        uint64_t latencies_table_head = 0;
        l1d_record_page_entry record_pages_table[L1D_RECORD_PAGES_TABLE_ENTRIES];
        l1d_ip_entry ip_table[L1D_IP_TABLE_ENTRIES];

        // Berti Stats
        uint64_t cache_accesses = 0;
        uint64_t cache_misses = 0;

        // Berti Methods
        BertiCore(CACHE* c) : cache(c) {}
        void init();
        uint32_t operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, uint32_t metadata_in);
        uint32_t fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);

        // Internal Helpers
        uint64_t l1d_get_latency(uint64_t cycle, uint64_t cycle_prev);
        int l1d_calculate_stride(uint64_t prev_offset, uint64_t current_offset);
        uint64_t l1d_count_bit_vector(uint64_t vector);
        bool l1d_all_last_berti_accessed_bit_vector(uint64_t vector, int berti);
        void l1d_init_current_pages_table();
        uint64_t l1d_get_current_pages_entry(uint64_t page_addr);
        void l1d_update_lru_current_pages_table(uint64_t index);
        uint64_t l1d_get_lru_current_pages_entry();
        void l1d_add_current_pages_table(uint64_t index, uint64_t page_addr);
        void l1d_update_current_pages_table(uint64_t index, uint64_t offset);
        void l1d_add_berti_current_pages_table(uint64_t index, int *berti, unsigned *saved_cycles);
        int l1d_get_berti_current_pages_table(uint64_t index);
        bool l1d_offset_requested_current_pages_table(uint64_t index, uint64_t offset);
        uint64_t l1d_evict_lru_current_page_entry();
        void l1d_init_prev_requests_table();
        uint64_t l1d_find_prev_request_entry(uint64_t pointer, uint64_t offset);
        void l1d_add_prev_requests_table(uint64_t pointer, uint64_t offset, uint64_t cycle);
        void l1d_reset_pointer_prev_requests(uint64_t pointer);
        void l1d_get_berti_prev_requests_table(uint64_t pointer, uint64_t offset, uint64_t latency, int *berti, unsigned *saved_cycles, uint64_t req_time);
        void l1d_init_latencies_table();
        uint64_t l1d_find_latency_entry(uint64_t pointer, uint64_t offset);
        void l1d_add_latencies_table(uint64_t pointer, uint64_t offset, uint64_t cycle);
        void l1d_reset_pointer_latencies(uint64_t pointer);
        void l1d_reset_entry_latencies_table(uint64_t pointer, uint64_t offset);
        uint64_t l1d_get_and_set_latency_latencies_table(uint64_t pointer, uint64_t offset, uint64_t cycle);
        uint64_t l1d_get_latency_latencies_table(uint64_t pointer, uint64_t offset);
        bool l1d_ongoing_request(uint64_t pointer, uint64_t offset);
        bool l1d_init_record_pages_table(); // Corrected signature
        void l1d_init_record_pages_table_impl();
        uint64_t l1d_get_lru_record_pages_entry();
        void l1d_update_lru_record_pages_table(uint64_t index);
        uint64_t l1d_get_entry_record_pages_table(uint64_t page_addr);
        void l1d_add_record_pages_table(uint64_t page_addr, uint64_t new_page_addr, uint64_t last_offset, bool short_reuse);
        void l1d_init_ip_table();
        void l1d_update_ip_table(int pointer, int berti, int stride, bool short_reuse);
    };

    // --- CMC Logic Definitions (Encapsulated) ---
    struct CmcCore {
        CACHE* cache;
        static const int STORAGE_SETS = 64;
        static const int STORAGE_WAYS = 16;
        static const int MAX_DEGREE = 16;
        static const int STACK_SIZE = 4;
        static const int FILTER_SIZE = 32;

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
            Recorder(int d) : index(0), degree(d) {}
            bool entry_empty() { return entries.empty(); }
            bool train_entry(uint64_t addr, bool *finished);
            void reset() { index = 0; entries.clear(); }
        };

        Recorder recorder;
        uint64_t acc_id;
        uint64_t current_tick;
        std::deque<RecordEntry> trigger;
        std::vector<std::vector<StorageEntry>> storage;
        std::list<uint64_t> filter_lru_list;
        std::unordered_map<uint64_t, std::list<uint64_t>::iterator> filter_map;

        CmcCore(CACHE* c);
        uint32_t operate(uint64_t addr, uint64_t ip, bool cache_hit, bool useful_prefetch, uint32_t metadata_in);
        
        // Internal Helpers
        uint64_t hash_index(uint64_t block_addr, uint64_t pc);
        uint64_t block_address(uint64_t addr);
        bool filter_check_and_add(uint64_t addr);
        StorageEntry* find_entry(uint64_t key);
        StorageEntry* find_victim(uint64_t key);
        void insert_entry(uint64_t key, const std::vector<uint64_t>& data, uint64_t id);
        void update_lru_tick(StorageEntry* entry);
        void invalidate_entry(StorageEntry* entry);
        std::vector<uint64_t> calculate_prefetch(uint64_t pc, uint64_t vaddr, bool cache_hit);
    };

    // --- Member Objects ---
    BertiCore berti_core;
    CmcCore cmc_core;

    // --- Constructor & Interface ---
    explicit hybrid_bertiM_cmc(CACHE* cache_ptr);
    void bind(CACHE* cache_ptr); // Optional overrides if needed
    
    // Main Entry Points
    uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in);
    uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
};

#endif