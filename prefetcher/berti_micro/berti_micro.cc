#include "berti_micro.h"
#include "cache.h"
#include <cstdlib>
#include <cmath>
#include <vector>

// Define features from legacy code
#define CONTINUE_BURST
#define PREFETCH_FOR_LONG_REUSE
#define LINNEA
#define WARMUP_NEW_PAGES

// -------------------------------------------------------------------------
// Constructor Implementation
// -------------------------------------------------------------------------
berti_micro::berti_micro(CACHE* cache_ptr) 
    : champsim::modules::prefetcher(nullptr), 
      cache(cache_ptr)
{
  l1d_init_current_pages_table();
  l1d_init_prev_requests_table();
  l1d_init_latencies_table();
  l1d_init_record_pages_table();
  l1d_init_ip_table();

  // Initialize stats explicitly
  cache_accesses = 0;
  cache_misses = 0;
}

void berti_micro::bind(CACHE* cache_ptr) {
    cache = cache_ptr;
}

// -------------------------------------------------------------------------
// Helper Functions Implementation
// -------------------------------------------------------------------------

uint64_t berti_micro::l1d_get_latency(uint64_t cycle, uint64_t cycle_prev) {
  uint64_t cycle_masked = cycle & L1D_TIME_MASK;
  uint64_t cycle_prev_masked = cycle_prev & L1D_TIME_MASK;
  if (cycle_prev_masked > cycle_masked) {
    return (cycle_masked + L1D_TIME_OVERFLOW) - cycle_prev_masked;
  }
  return cycle_masked - cycle_prev_masked;
}

int berti_micro::l1d_calculate_stride(uint64_t prev_offset, uint64_t current_offset) {
  int stride;
  if (current_offset > prev_offset) {
    stride = static_cast<int>(current_offset - prev_offset);
  } else {
    stride = static_cast<int>(prev_offset - current_offset);
    stride *= -1;
  }
  return stride;
}

uint64_t berti_micro::l1d_count_bit_vector(uint64_t vector) {
  uint64_t count = 0;
  for (int i = 0; i < L1D_PAGE_BLOCKS; i++) {
    if (vector & ((uint64_t)1 << i)) {
      count++;
    }
  }
  return count;
}

bool berti_micro::l1d_all_last_berti_accessed_bit_vector(uint64_t vector, int berti) {
  unsigned count_yes = 0;
  unsigned count_no = 0;
  if (berti < 0) {
    for (int i = 0; i < (0 - berti); i++) {
      (vector & ((uint64_t)1 << i)) ? count_yes++ : count_no++;
    }
  } else if (berti > 0) {
    for (int i = L1D_PAGE_OFFSET_MASK; i > L1D_PAGE_OFFSET_MASK - berti; i--) {
      (vector & ((uint64_t)1 << i)) ? count_yes++ : count_no++;
    }
  } else return true;

  if (count_yes == 0) return false;
  return ((double)count_yes / (double)(count_yes + count_no)) > L1D_BURST_THRESHOLD;
}

// -------------------------------------------------------------------------
// Current Pages Table
// -------------------------------------------------------------------------

void berti_micro::l1d_init_current_pages_table() {
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_ENTRIES; i++) {
    current_pages_table[i].page_addr = 0;
    current_pages_table[i].u_vector = 0;
    for (int j = 0; j < L1D_CURRENT_PAGES_TABLE_NUM_BERTI; j++) {
      current_pages_table[i].berti[j] = 0;
      current_pages_table[i].berti_score[j] = 0;
    }
    current_pages_table[i].current_berti = 0;
    current_pages_table[i].stride = 0;
    current_pages_table[i].short_reuse = true;
    current_pages_table[i].continue_burst = false;
    current_pages_table[i].lru = i;
  }
}

uint64_t berti_micro::l1d_get_current_pages_entry(uint64_t page_addr) {
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_ENTRIES; i++) {
    if (current_pages_table[i].page_addr == page_addr) return i;
  }
  return L1D_CURRENT_PAGES_TABLE_ENTRIES;
}

void berti_micro::l1d_update_lru_current_pages_table(uint64_t index) {
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_ENTRIES; i++) {
    if (current_pages_table[i].lru < current_pages_table[index].lru) {
      current_pages_table[i].lru++;
    }
  }
  current_pages_table[index].lru = 0;
}

uint64_t berti_micro::l1d_get_lru_current_pages_entry() {
  uint64_t lru = L1D_CURRENT_PAGES_TABLE_ENTRIES;
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_ENTRIES; i++) {
    current_pages_table[i].lru++;
    if (current_pages_table[i].lru == L1D_CURRENT_PAGES_TABLE_ENTRIES) {
      current_pages_table[i].lru = 0;
      lru = i;
    }
  }
  return lru;
}

void berti_micro::l1d_add_current_pages_table(uint64_t index, uint64_t page_addr) {
  current_pages_table[index].page_addr = page_addr;
  current_pages_table[index].u_vector = 0;
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_NUM_BERTI; i++) {
    current_pages_table[index].berti[i] = 0;
    current_pages_table[index].berti_score[i] = 0;
  }
  current_pages_table[index].continue_burst = false;
}

void berti_micro::l1d_update_current_pages_table(uint64_t index, uint64_t offset) {
  current_pages_table[index].u_vector |= (uint64_t)1 << offset;
  l1d_update_lru_current_pages_table(index);
}

void berti_micro::l1d_add_berti_current_pages_table(uint64_t index, int *berti, unsigned *saved_cycles) {
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS; i++) {
    if (berti[i] == 0) break;
    
    for (int j = 0; j < L1D_CURRENT_PAGES_TABLE_NUM_BERTI; j++) {
      if (current_pages_table[index].berti[j] == 0) {
        current_pages_table[index].berti[j] = berti[i];
        current_pages_table[index].berti_score[j] = 1;
        break;
      } else if (current_pages_table[index].berti[j] == berti[i]) {
        current_pages_table[index].berti_score[j]++;
#ifdef WARMUP_NEW_PAGES
        if (current_pages_table[index].current_berti == 0 && current_pages_table[index].berti_score[j] > 2) {
          current_pages_table[index].current_berti = berti[i];
        }
#endif
        break;
      }
    }
  }
  l1d_update_lru_current_pages_table(index);
}

int berti_micro::l1d_get_berti_current_pages_table(uint64_t index) {
  int max_score = 0;
  uint64_t berti = 0;
  for (int i = 0; i < L1D_CURRENT_PAGES_TABLE_NUM_BERTI; i++) {
    int curr_berti = current_pages_table[index].berti[i];
    if (curr_berti != 0) {
      int score = current_pages_table[index].berti_score[i];
      int neg_score = 0 - std::abs(curr_berti);
      if (score < neg_score) {
        score = 0;
      } else {
        score -= neg_score;
      }
      if (score >= max_score) {
        berti = curr_berti;
        max_score = score;
      }
    }
  }
  return static_cast<int>(berti);
}

bool berti_micro::l1d_offset_requested_current_pages_table(uint64_t index, uint64_t offset) {
  return current_pages_table[index].u_vector & ((uint64_t)1 << offset);
}

uint64_t berti_micro::l1d_evict_lru_current_page_entry() {
  uint64_t victim_index = l1d_get_lru_current_pages_entry();
  
  if (current_pages_table[victim_index].u_vector) {
    l1d_update_ip_table(static_cast<int>(victim_index),
                        l1d_get_berti_current_pages_table(victim_index),
                        current_pages_table[victim_index].stride,
                        current_pages_table[victim_index].short_reuse);
  }
  
  l1d_reset_pointer_prev_requests(victim_index);
  l1d_reset_pointer_latencies(victim_index);
  return victim_index;
}

// -------------------------------------------------------------------------
// Prev Requests Table
// -------------------------------------------------------------------------

void berti_micro::l1d_init_prev_requests_table() {
  prev_requests_table_head = 0;
  for (int i = 0; i < L1D_PREV_REQUESTS_TABLE_ENTRIES; i++) {
    prev_requests_table[i].page_addr_pointer = L1D_PREV_REQUESTS_TABLE_NULL_POINTER;
  }
}

uint64_t berti_micro::l1d_find_prev_request_entry(uint64_t pointer, uint64_t offset) {
  for (int i = 0; i < L1D_PREV_REQUESTS_TABLE_ENTRIES; i++) {
    if (prev_requests_table[i].page_addr_pointer == pointer && prev_requests_table[i].offset == offset)
      return i;
  }
  return L1D_PREV_REQUESTS_TABLE_ENTRIES;
}

void berti_micro::l1d_add_prev_requests_table(uint64_t pointer, uint64_t offset, uint64_t cycle) {
  if (l1d_find_prev_request_entry(pointer, offset) != L1D_PREV_REQUESTS_TABLE_ENTRIES) return;

  prev_requests_table[prev_requests_table_head].page_addr_pointer = pointer;
  prev_requests_table[prev_requests_table_head].offset = offset;
  prev_requests_table[prev_requests_table_head].time = cycle & L1D_TIME_MASK;
  prev_requests_table_head = (prev_requests_table_head + 1) & L1D_PREV_REQUESTS_TABLE_MASK;
}

void berti_micro::l1d_reset_pointer_prev_requests(uint64_t pointer) {
  for (int i = 0; i < L1D_PREV_REQUESTS_TABLE_ENTRIES; i++) {
    if (prev_requests_table[i].page_addr_pointer == pointer) {
      prev_requests_table[i].page_addr_pointer = L1D_PREV_REQUESTS_TABLE_NULL_POINTER;
    }
  }
}

void berti_micro::l1d_get_berti_prev_requests_table(uint64_t pointer, uint64_t offset, uint64_t latency, int *berti, unsigned *saved_cycles, uint64_t req_time) {
  int my_pos = 0;
  uint64_t extra_time = 0;
  uint64_t last_time = prev_requests_table[(prev_requests_table_head + L1D_PREV_REQUESTS_TABLE_MASK) & L1D_PREV_REQUESTS_TABLE_MASK].time;

  for (uint64_t i = (prev_requests_table_head + L1D_PREV_REQUESTS_TABLE_MASK) & L1D_PREV_REQUESTS_TABLE_MASK; i != prev_requests_table_head; i = (i + L1D_PREV_REQUESTS_TABLE_MASK) & L1D_PREV_REQUESTS_TABLE_MASK) {
    if (last_time < prev_requests_table[i].time) {
      extra_time = L1D_TIME_OVERFLOW;
    }
    last_time = prev_requests_table[i].time;
    if (prev_requests_table[i].page_addr_pointer == pointer) {
      if (prev_requests_table[i].offset == offset) {
        req_time = prev_requests_table[i].time;
      } else if (req_time) {
        if (prev_requests_table[i].time <= req_time + extra_time - latency) {
          berti[my_pos] = l1d_calculate_stride(prev_requests_table[i].offset, offset);
          saved_cycles[my_pos] = static_cast<unsigned>(latency);
          my_pos++;
        } else if (req_time + extra_time - prev_requests_table[i].time > 0) {
          // Could enable Berti latencies logic here
        }
        if (my_pos == L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS) {
          berti[my_pos] = 0;
          return;
        }
      }
    }
  }
  berti[my_pos] = 0;
}

// -------------------------------------------------------------------------
// Latencies Table
// -------------------------------------------------------------------------

void berti_micro::l1d_init_latencies_table() {
  latencies_table_head = 0;
  for (int i = 0; i < L1D_LATENCIES_TABLE_ENTRIES; i++) {
    latencies_table[i].page_addr_pointer = L1D_LATENCIES_TABLE_NULL_POINTER;
  }
}

uint64_t berti_micro::l1d_find_latency_entry(uint64_t pointer, uint64_t offset) {
  for (int i = 0; i < L1D_LATENCIES_TABLE_ENTRIES; i++) {
    if (latencies_table[i].page_addr_pointer == pointer && latencies_table[i].offset == offset) return i;
  }
  return L1D_LATENCIES_TABLE_ENTRIES;
}

void berti_micro::l1d_add_latencies_table(uint64_t pointer, uint64_t offset, uint64_t cycle) {
  if (l1d_find_latency_entry(pointer, offset) != L1D_LATENCIES_TABLE_ENTRIES) return;

  latencies_table[latencies_table_head].page_addr_pointer = pointer;
  latencies_table[latencies_table_head].offset = offset;
  latencies_table[latencies_table_head].time_lat = cycle & L1D_TIME_MASK;
  latencies_table[latencies_table_head].completed = false;
  latencies_table_head = (latencies_table_head + 1) & L1D_LATENCIES_TABLE_MASK;
}

void berti_micro::l1d_reset_pointer_latencies(uint64_t pointer) {
  for (int i = 0; i < L1D_LATENCIES_TABLE_ENTRIES; i++) {
    if (latencies_table[i].page_addr_pointer == pointer) {
      latencies_table[i].page_addr_pointer = L1D_LATENCIES_TABLE_NULL_POINTER;
    }
  }
}

void berti_micro::l1d_reset_entry_latencies_table(uint64_t pointer, uint64_t offset) {
  uint64_t index = l1d_find_latency_entry(pointer, offset);
  if (index != L1D_LATENCIES_TABLE_ENTRIES) {
    latencies_table[index].page_addr_pointer = L1D_LATENCIES_TABLE_NULL_POINTER;
  }
}

uint64_t berti_micro::l1d_get_and_set_latency_latencies_table(uint64_t pointer, uint64_t offset, uint64_t cycle) {
  uint64_t index = l1d_find_latency_entry(pointer, offset);
  if (index == L1D_LATENCIES_TABLE_ENTRIES) return 0;
  if (!latencies_table[index].completed) {
    latencies_table[index].time_lat = l1d_get_latency(cycle, latencies_table[index].time_lat);
    latencies_table[index].completed = true;
  }
  return latencies_table[index].time_lat;
}

uint64_t berti_micro::l1d_get_latency_latencies_table(uint64_t pointer, uint64_t offset) {
  uint64_t index = l1d_find_latency_entry(pointer, offset);
  if (index == L1D_LATENCIES_TABLE_ENTRIES) return 0;
  if (!latencies_table[index].completed) return 0;
  return latencies_table[index].time_lat;
}

bool berti_micro::l1d_ongoing_request(uint64_t pointer, uint64_t offset) {
  uint64_t index = l1d_find_latency_entry(pointer, offset);
  if (index == L1D_LATENCIES_TABLE_ENTRIES) return false;
  if (latencies_table[index].completed) return false;
  return true;
}

bool berti_micro::l1d_is_request(uint64_t pointer, uint64_t offset) {
  uint64_t index = l1d_find_latency_entry(pointer, offset);
  if (index == L1D_LATENCIES_TABLE_ENTRIES) return false;
  return true;
}

// -------------------------------------------------------------------------
// Record Pages Table
// -------------------------------------------------------------------------

void berti_micro::l1d_init_record_pages_table() {
  for (int i = 0; i < L1D_RECORD_PAGES_TABLE_ENTRIES; i++) {
    record_pages_table[i].page_addr = 0;
    record_pages_table[i].linnea = 0;
    record_pages_table[i].last_offset = 0;
    record_pages_table[i].short_reuse = true;
    record_pages_table[i].lru = i;
  }
}

uint64_t berti_micro::l1d_get_lru_record_pages_entry() {
  uint64_t lru = L1D_RECORD_PAGES_TABLE_ENTRIES;
  for (int i = 0; i < L1D_RECORD_PAGES_TABLE_ENTRIES; i++) {
    record_pages_table[i].lru++;
    if (record_pages_table[i].lru == L1D_RECORD_PAGES_TABLE_ENTRIES) {
      record_pages_table[i].lru = 0;
      lru = i;
    }
  }
  return lru;
}

void berti_micro::l1d_update_lru_record_pages_table(uint64_t index) {
  for (int i = 0; i < L1D_RECORD_PAGES_TABLE_ENTRIES; i++) {
    if (record_pages_table[i].lru < record_pages_table[index].lru) {
      record_pages_table[i].lru++;
    }
  }
  record_pages_table[index].lru = 0;
}

uint64_t berti_micro::l1d_get_entry_record_pages_table(uint64_t page_addr) {
  uint64_t trunc_page_addr = page_addr & L1D_TRUNCATED_PAGE_ADDR_MASK;
  for (int i = 0; i < L1D_RECORD_PAGES_TABLE_ENTRIES; i++) {
    if (record_pages_table[i].page_addr == trunc_page_addr) {
      return i;
    }
  }
  return L1D_RECORD_PAGES_TABLE_ENTRIES;
}

void berti_micro::l1d_add_record_pages_table(uint64_t page_addr, uint64_t new_page_addr, uint64_t last_offset, bool short_reuse) {
  uint64_t index = l1d_get_entry_record_pages_table(page_addr);
  if (index < L1D_RECORD_PAGES_TABLE_ENTRIES) {
    l1d_update_lru_record_pages_table(index);
  } else {
    index = l1d_get_lru_record_pages_entry();
    record_pages_table[index].page_addr = page_addr & L1D_TRUNCATED_PAGE_ADDR_MASK;
  }
  record_pages_table[index].linnea = new_page_addr;
  record_pages_table[index].last_offset = last_offset;
  record_pages_table[index].short_reuse = short_reuse;
}

// -------------------------------------------------------------------------
// IP Table
// -------------------------------------------------------------------------

void berti_micro::l1d_init_ip_table() {
  for (int i = 0; i < L1D_IP_TABLE_ENTRIES; i++) {
    ip_table[i].current = false;
    ip_table[i].berti_or_pointer = 0;
    ip_table[i].consecutive = false;
    ip_table[i].short_reuse = true;
  }
}

void berti_micro::l1d_update_ip_table(int pointer, int berti, int stride, bool short_reuse) {
  for (int i = 0; i < L1D_IP_TABLE_ENTRIES; i++) {
    if (ip_table[i].current && ip_table[i].berti_or_pointer == pointer) {
      ip_table[i].current = false;
      if (short_reuse) {
        ip_table[i].berti_or_pointer = berti;
      } else {
        ip_table[i].berti_or_pointer = stride;
      }
      ip_table[i].short_reuse = short_reuse;
    }
  }
}


// -------------------------------------------------------------------------
// Main Interface Implementation
// -------------------------------------------------------------------------

uint32_t berti_micro::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in)
{
  cache_accesses++;
  if (!cache_hit) cache_misses++;
  
  // Address Decomposition
  uint64_t full_addr = addr.to<uint64_t>();
  uint64_t ip_val = ip.to<uint64_t>();
  
  uint64_t line_addr = full_addr >> LOG2_BLOCK_SIZE;
  uint64_t page_addr = line_addr >> L1D_PAGE_BLOCKS_BITS;
  uint64_t offset = line_addr & L1D_PAGE_OFFSET_MASK;
  uint64_t ip_index = ip_val & L1D_IP_TABLE_INDEX_MASK;
  
  int last_berti = 0;
  int berti_val = 0;
  bool linnea_hits = false;
  bool first_access = false;
  bool full_access = false;
  int stride = 0;
  bool short_reuse = true;
  uint64_t count_reuse = 0;
  
  // IMPORTANT: Use cache->current_cycle() (function call)
  uint64_t now = cache->current_cycle(); 

  // Find entry in current page table
  uint64_t index = l1d_get_current_pages_entry(page_addr);
  
  bool recently_accessed = false;
  if (index < L1D_CURRENT_PAGES_TABLE_ENTRIES) {
    recently_accessed = l1d_offset_requested_current_pages_table(index, offset);
  }
  
  if (index < L1D_CURRENT_PAGES_TABLE_ENTRIES && current_pages_table[index].u_vector != 0) {
    // Page is known and used
    last_berti = current_pages_table[index].current_berti;
    berti_val = last_berti;
    
    l1d_update_current_pages_table(index, offset);
    
    if (cache_hit) {
      uint64_t latency = l1d_get_latency_latencies_table(index, offset);
      if (latency != 0) {
        int berti_arr[L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS];
        unsigned saved_cycles[L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS];
        l1d_get_berti_prev_requests_table(index, offset, latency, berti_arr, saved_cycles, now);
        
        if (!recently_accessed) {
          l1d_add_berti_current_pages_table(index, berti_arr, saved_cycles);
        }
      }
    }
  } else {
    // First access to a new page
    first_access = true;
    
    if (ip_table[ip_index].current) {
      int ip_pointer = ip_table[ip_index].berti_or_pointer;
      
      last_berti = current_pages_table[ip_pointer].current_berti;
      berti_val = l1d_get_berti_current_pages_table(ip_pointer);
      
      full_access = l1d_all_last_berti_accessed_bit_vector(current_pages_table[ip_pointer].u_vector, berti_val);
      
      uint64_t last_page_addr = current_pages_table[ip_pointer].page_addr;
      count_reuse = l1d_count_bit_vector(current_pages_table[ip_pointer].u_vector);
      short_reuse = (count_reuse > LONG_REUSE_LIMIT);
      
      if (short_reuse) {
        if (berti_val > 0 && last_page_addr + 1 == page_addr) {
          ip_table[ip_index].consecutive = true;
        } else if (berti_val < 0 && last_page_addr == page_addr + 1) {
          ip_table[ip_index].consecutive = true;
        } else {
          ip_table[ip_index].consecutive = false;
          l1d_add_record_pages_table(last_page_addr, page_addr, 0, true);
        }
      } else {
        if (current_pages_table[ip_pointer].short_reuse) {
          current_pages_table[ip_pointer].short_reuse = false;
        }
        uint64_t record_index = l1d_get_entry_record_pages_table(last_page_addr);
        if (record_index < L1D_RECORD_PAGES_TABLE_ENTRIES && !record_pages_table[record_index].short_reuse && record_pages_table[record_index].linnea == page_addr) {
          stride = l1d_calculate_stride(record_pages_table[record_index].last_offset, offset);
        }
        if (!recently_accessed) {
          l1d_add_record_pages_table(last_page_addr, page_addr, offset, short_reuse);
        }
      }
    } else {
      berti_val = ip_table[ip_index].berti_or_pointer;
    }
    
    if (index == L1D_CURRENT_PAGES_TABLE_ENTRIES) {
      index = l1d_evict_lru_current_page_entry();
      l1d_add_current_pages_table(index, page_addr);
    } else {
      linnea_hits = true;
    }
    l1d_update_current_pages_table(index, offset);
  }
  
  if (!recently_accessed) {
    if (short_reuse) {
      current_pages_table[index].current_berti = berti_val;
    } else {
      current_pages_table[index].stride = stride;
    }
    current_pages_table[index].short_reuse = short_reuse;
    
    ip_table[ip_index].current = true;
    ip_table[ip_index].berti_or_pointer = static_cast<int>(index);
  }
  
  // History Buffer
  if (l1d_find_prev_request_entry(index, offset) == L1D_PREV_REQUESTS_TABLE_ENTRIES) {
    l1d_add_prev_requests_table(index, offset, now);
  } else {
    if (!cache_hit && !l1d_ongoing_request(index, offset)) {
       l1d_add_prev_requests_table(index, offset, now);
    }
  }
  
  // Latency Table
  if (!recently_accessed && !cache_hit) {
    l1d_add_latencies_table(index, offset, now);
  }
  
  // --- Prefetch Generation ---
  
  if (berti_val != 0) {
    // Burst Mode
    if ((first_access && full_access) || current_pages_table[index].continue_burst) {
      int burst_init = 0;
      int burst_end = 0;
      int burst_it = 0;
      
      if (!linnea_hits || current_pages_table[index].continue_burst) {
        current_pages_table[index].continue_burst = false;
        if (berti_val > 0) {
          burst_init = static_cast<int>(offset + 1);
          burst_end = static_cast<int>(offset + berti_val);
          burst_it = 1;
        } else {
          burst_init = static_cast<int>(offset - 1);
          burst_end = static_cast<int>(offset + berti_val);
          burst_it = -1;
        }
      } else if (last_berti > 0 && berti_val > 0 && berti_val > last_berti) {
         burst_init = last_berti;
         burst_end = berti_val;
         burst_it = 1;
      } else if (last_berti < 0 && berti_val < 0 && berti_val < last_berti) {
         burst_init = L1D_PAGE_OFFSET_MASK + last_berti;
         burst_end = L1D_PAGE_OFFSET_MASK + berti_val;
         burst_it = -1;
      }
      
      int bursts = 0;
      for (int i = burst_init; i != burst_end; i+= burst_it) {
        if (i >= 0 && i < L1D_PAGE_BLOCKS) {
           uint64_t pf_line_addr = (page_addr << L1D_PAGE_BLOCKS_BITS) | i;
           uint64_t pf_addr = pf_line_addr << LOG2_BLOCK_SIZE;
           uint64_t pf_offset = pf_line_addr & L1D_PAGE_OFFSET_MASK;
           
           if (bursts < L1D_BURST_THROTTLING) {
             // IMPORTANT: Use cache->prefetch_line to avoid SEGFAULT
             bool prefetched = cache->prefetch_line(champsim::address{pf_addr}, true, metadata_in);
             if (prefetched) {
               l1d_add_latencies_table(index, pf_offset, now);
               bursts++;
             }
           } else {
#ifdef CONTINUE_BURST
             if (!recently_accessed) current_pages_table[index].continue_burst = true;
#endif
             break;
           }
        }
      }
    }
    
    // Berti Mode
    for (int i = 1; i <= L1D_BERTI_THROTTLING; i++) {
       uint64_t pf_line_addr = line_addr + (berti_val * i);
       uint64_t pf_addr = pf_line_addr << LOG2_BLOCK_SIZE;
       uint64_t pf_page_addr = pf_line_addr >> L1D_PAGE_BLOCKS_BITS;
       uint64_t pf_offset = pf_line_addr & L1D_PAGE_OFFSET_MASK;
       
       if (pf_page_addr == page_addr) {
         // Use cache->prefetch_line
         bool prefetched = cache->prefetch_line(champsim::address{pf_addr}, true, metadata_in);
         if (prefetched) l1d_add_latencies_table(index, pf_offset, now);
       } else if (ip_table[ip_index].consecutive && berti_val != 0) {
         // Consecutive Page
         uint64_t new_page;
         if (berti_val < 0) new_page = page_addr - 1;
         else new_page = page_addr + 1;
         
         uint64_t new_index = l1d_get_current_pages_entry(new_page);
         if (new_index == L1D_CURRENT_PAGES_TABLE_ENTRIES) {
           new_index = l1d_evict_lru_current_page_entry();
           l1d_add_current_pages_table(new_index, new_page);
         }
         
         uint64_t consecutive_pf_offset = (offset + berti_val + L1D_PAGE_BLOCKS) & L1D_PAGE_OFFSET_MASK;
         uint64_t new_line = (new_page << L1D_PAGE_BLOCKS_BITS) | consecutive_pf_offset;
         uint64_t new_addr = new_line << LOG2_BLOCK_SIZE;
         
         // Use cache->prefetch_line
         bool prefetched = cache->prefetch_line(champsim::address{new_addr}, true, metadata_in);
         if (prefetched) l1d_add_latencies_table(new_index, consecutive_pf_offset, now);
       } else {
#ifdef LINNEA
         uint64_t index_record = l1d_get_entry_record_pages_table(page_addr);
         if (index_record < L1D_RECORD_PAGES_TABLE_ENTRIES) {
           uint64_t new_page = record_pages_table[index_record].linnea;
           uint64_t new_index = l1d_get_current_pages_entry(new_page);
           if (new_index == L1D_CURRENT_PAGES_TABLE_ENTRIES) {
             new_index = l1d_evict_lru_current_page_entry();
             l1d_add_current_pages_table(new_index, new_page);
           }
           uint64_t linnea_pf_offset = (offset + berti_val + L1D_PAGE_BLOCKS) & L1D_PAGE_OFFSET_MASK;
           uint64_t new_line = (new_page << L1D_PAGE_BLOCKS_BITS) | linnea_pf_offset;
           uint64_t new_addr = new_line << LOG2_BLOCK_SIZE;
           // Use cache->prefetch_line
           bool prefetched = cache->prefetch_line(champsim::address{new_addr}, true, metadata_in);
           if (prefetched) l1d_add_latencies_table(new_index, linnea_pf_offset, now);
         }
#endif
       }
    }
  }

#ifdef PREFETCH_FOR_LONG_REUSE
  if (!short_reuse) {
    uint64_t index_record = l1d_get_entry_record_pages_table(page_addr);
    if (index_record < L1D_RECORD_PAGES_TABLE_ENTRIES) {
      uint64_t new_page = record_pages_table[index_record].linnea;
      uint64_t new_offset = record_pages_table[index_record].last_offset;
      int new_stride;
      
      if (!current_pages_table[index].short_reuse) {
        new_stride = current_pages_table[index].stride;
      } else {
        new_stride = ip_table[ip_index].berti_or_pointer;
      }
      
      uint64_t new_index = l1d_get_current_pages_entry(new_page);
      if (new_index == L1D_CURRENT_PAGES_TABLE_ENTRIES) {
        new_index = l1d_evict_lru_current_page_entry();
        l1d_add_current_pages_table(new_index, new_page);
      }
      
      int64_t pf_offset_signed = (int64_t)new_offset + new_stride;
      if (pf_offset_signed >= 0 && pf_offset_signed < L1D_PAGE_BLOCKS) {
         uint64_t pf_offset = (uint64_t)pf_offset_signed;
         uint64_t new_line = (new_page << L1D_PAGE_BLOCKS_BITS) | pf_offset;
         uint64_t new_addr = new_line << LOG2_BLOCK_SIZE;
         // Use cache->prefetch_line
         bool prefetched = cache->prefetch_line(champsim::address{new_addr}, true, metadata_in);
         if (prefetched) l1d_add_latencies_table(new_index, pf_offset, now);
      }
    }
  }
#endif

  return metadata_in;
}

uint32_t berti_micro::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in)
{
  uint64_t line_addr = addr.to<uint64_t>() >> LOG2_BLOCK_SIZE;
  uint64_t page_addr = line_addr >> L1D_PAGE_BLOCKS_BITS;
  uint64_t offset = line_addr & L1D_PAGE_OFFSET_MASK;

  uint64_t pointer_prev = l1d_get_current_pages_entry(page_addr);

  if (pointer_prev < L1D_CURRENT_PAGES_TABLE_ENTRIES) {
    // IMPORTANT: Use cache->current_cycle() (function call)
    uint64_t latency = l1d_get_and_set_latency_latencies_table(pointer_prev, offset, cache->current_cycle());
    if (latency != 0) {
      int berti_arr[L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS];
      unsigned saved_cycles[L1D_CURRENT_PAGES_TABLE_NUM_BERTI_PER_ACCESS];
      l1d_get_berti_prev_requests_table(pointer_prev, offset, latency, berti_arr, saved_cycles, 0);
      l1d_add_berti_current_pages_table(pointer_prev, berti_arr, saved_cycles);
    }
  }

  uint64_t evicted_page = (evicted_addr.to<uint64_t>() >> LOG2_BLOCK_SIZE) >> L1D_PAGE_BLOCKS_BITS;
  uint64_t evicted_index = l1d_get_current_pages_entry(evicted_page);
  if (evicted_index < L1D_CURRENT_PAGES_TABLE_ENTRIES) {
    uint64_t evicted_offset = (evicted_addr.to<uint64_t>() >> LOG2_BLOCK_SIZE) & L1D_PAGE_OFFSET_MASK;
    l1d_reset_entry_latencies_table(evicted_index, evicted_offset);
  }
  return metadata_in;
}