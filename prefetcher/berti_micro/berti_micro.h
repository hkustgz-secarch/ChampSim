#ifndef PREFETCHER_BERTI_MICRO_H
#define PREFETCHER_BERTI_MICRO_H

#include <cstdint>
#include <vector>
#include <iostream>
#include <cmath>
#include <cassert>

#include "modules.h"
#include "address.h"

// Forward declaration needed because we use CACHE* in the constructor
struct CACHE;

// Constants definition
#define LOG2_BLOCK_SIZE 6
#define LOG2_PAGE_SIZE 12
#define L1D_PAGE_BLOCKS_BITS (LOG2_PAGE_SIZE - LOG2_BLOCK_SIZE)
#define L1D_PAGE_BLOCKS (1 << L1D_PAGE_BLOCKS_BITS)
#define L1D_PAGE_OFFSET_MASK (L1D_PAGE_BLOCKS - 1)

// Algorithm parameters
#define L1D_BERTI_THROTTLING 1
#define L1D_BURST_THROTTLING 7
#define L1D_BURST_THRESHOLD 0.99
#define LONG_REUSE_LIMIT 16

#define L1D_TIME_BITS 16
#define L1D_TIME_OVERFLOW ((uint64_t)1 << L1D_TIME_BITS)
#define L1D_TIME_MASK (L1D_TIME_OVERFLOW - 1)

// Table Sizes
#define L1D_CURRENT_PAGES_TABLE_INDEX_BITS 6
#define L1D_CURRENT_PAGES_TABLE_ENTRIES ((1 << L1D_CURRENT_PAGES_TABLE_INDEX_BITS) - 1)
#define L1D_CURRENT_PAGES_TABLE_NUM_BERTI 8
#define L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS 8 

#define L1D_PREV_REQUESTS_TABLE_INDEX_BITS 10
#define L1D_PREV_REQUESTS_TABLE_ENTRIES (1 << L1D_PREV_REQUESTS_TABLE_INDEX_BITS)
#define L1D_PREV_REQUESTS_TABLE_MASK (L1D_PREV_REQUESTS_TABLE_ENTRIES - 1)
#define L1D_PREV_REQUESTS_TABLE_NULL_POINTER L1D_CURRENT_PAGES_TABLE_ENTRIES

#define L1D_LATENCIES_TABLE_INDEX_BITS 10
#define L1D_LATENCIES_TABLE_ENTRIES (1 << L1D_LATENCIES_TABLE_INDEX_BITS)
#define L1D_LATENCIES_TABLE_MASK (L1D_LATENCIES_TABLE_ENTRIES - 1)
#define L1D_LATENCIES_TABLE_NULL_POINTER L1D_CURRENT_PAGES_TABLE_ENTRIES

#define L1D_RECORD_PAGES_TABLE_INDEX_BITS 14
#define L1D_RECORD_PAGES_TABLE_ENTRIES ((1 << L1D_RECORD_PAGES_TABLE_INDEX_BITS) - 1)
#define L1D_TRUNCATED_PAGE_ADDR_BITS 32 
#define L1D_TRUNCATED_PAGE_ADDR_MASK (((uint64_t)1 << L1D_TRUNCATED_PAGE_ADDR_BITS) -1)

#define L1D_IP_TABLE_INDEX_BITS 12
#define L1D_IP_TABLE_ENTRIES (1 << L1D_IP_TABLE_INDEX_BITS)
#define L1D_IP_TABLE_INDEX_MASK (L1D_IP_TABLE_ENTRIES - 1)

class berti_micro : public champsim::modules::prefetcher {
public:
  // --- ChampSim Interface Members ---
  CACHE* cache; 

  // --- Internal Structures ---
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

  // --- Member Variables (Tables) ---
  l1d_current_page_entry current_pages_table[L1D_CURRENT_PAGES_TABLE_ENTRIES];
  l1d_prev_request_entry prev_requests_table[L1D_PREV_REQUESTS_TABLE_ENTRIES];
  uint64_t prev_requests_table_head = 0;
  l1d_latency_entry latencies_table[L1D_LATENCIES_TABLE_ENTRIES];
  uint64_t latencies_table_head = 0;
  l1d_record_page_entry record_pages_table[L1D_RECORD_PAGES_TABLE_ENTRIES];
  l1d_ip_entry ip_table[L1D_IP_TABLE_ENTRIES];

  // Stats
  uint64_t cache_accesses = 0;
  uint64_t cache_misses = 0;

  // --- Helper Functions ---
  uint64_t l1d_get_latency(uint64_t cycle, uint64_t cycle_prev);
  int l1d_calculate_stride(uint64_t prev_offset, uint64_t current_offset);
  uint64_t l1d_count_bit_vector(uint64_t vector);
  bool l1d_all_last_berti_accessed_bit_vector(uint64_t vector, int berti);

  // Table Helpers
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
  bool l1d_is_request(uint64_t pointer, uint64_t offset);

  void l1d_init_record_pages_table();
  uint64_t l1d_get_lru_record_pages_entry();
  void l1d_update_lru_record_pages_table(uint64_t index);
  uint64_t l1d_get_entry_record_pages_table(uint64_t page_addr);
  void l1d_add_record_pages_table(uint64_t page_addr, uint64_t new_page_addr, uint64_t last_offset, bool short_reuse);

  void l1d_init_ip_table();
  void l1d_update_ip_table(int pointer, int berti, int stride, bool short_reuse);

  // --- Interface ---
  // Constructor matching your environment (accepts CACHE*)
  explicit berti_micro(CACHE* cache_ptr);
  
  // Bind function (if needed by your environment, otherwise optional but good to have)
  void bind(CACHE* cache_ptr);

  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
};

#endif