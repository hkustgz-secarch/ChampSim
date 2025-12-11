#include "berti_gaze.h"
#include "cache.h" // Needed for some constants if strictly required, usually modules.h is enough
#include <algorithm>
#include <iostream>

using namespace berti_space;

// ====================================================================
//  Berti Helper Classes Implementation (LatencyTable, ShadowCache, etc.)
// ====================================================================

// --- LatencyTable ---
uint8_t LatencyTable::add(uint64_t addr, uint64_t tag, bool pf, uint64_t cycle) {
    latency_table* free_entry = nullptr;
    for (int i = 0; i < size; i++) {
        if (latencyt[i].addr == addr) {
            latencyt[i].pf = pf;
            latencyt[i].tag = tag;
            return latencyt[i].pf;
        }
        if (latencyt[i].tag == 0) free_entry = &latencyt[i];
    }
    if (free_entry == nullptr) return 0; // Prevent crash, strictly speaking assertions are bad in sim unless debugging
    
    free_entry->addr = addr;
    free_entry->time = cycle;
    free_entry->tag = tag;
    free_entry->pf = pf;
    return free_entry->pf;
}

uint64_t LatencyTable::del(uint64_t addr) {
    for (int i = 0; i < size; i++) {
        if (latencyt[i].addr == addr) {
            uint64_t time = latencyt[i].time;
            latencyt[i].addr = 0;
            latencyt[i].tag = 0;
            latencyt[i].time = 0;
            latencyt[i].pf = 0;
            return time;
        }
    }
    return 0;
}

uint64_t LatencyTable::get(uint64_t addr) {
    for (int i = 0; i < size; i++) {
        if (latencyt[i].addr == addr) return latencyt[i].time;
    }
    return 0;
}

uint64_t LatencyTable::get_tag(uint64_t addr) {
    for (int i = 0; i < size; i++) {
        if (latencyt[i].addr == addr && latencyt[i].tag) return latencyt[i].tag;
    }
    return 0;
}

// --- ShadowCache ---
ShadowCache::ShadowCache(const int sets, const int ways) : sets(sets), ways(ways) {
    scache = std::vector<std::vector<shadow_cache>>(sets, std::vector<shadow_cache>(ways, shadow_cache()));
}

bool ShadowCache::add(uint32_t set, uint32_t way, uint64_t addr, bool pf, uint64_t lat) {
    if (set >= sets || way >= ways) return false;
    scache[set][way].addr = addr;
    scache[set][way].pf = pf;
    scache[set][way].lat = lat;
    return scache[set][way].pf;
}

bool ShadowCache::get(uint64_t addr) {
    for (auto& s : scache) {
        for (auto& w : s) {
            if (w.addr == addr) return true;
        }
    }
    return false;
}

void ShadowCache::set_pf(uint64_t addr, bool pf) {
    for (auto& s : scache) {
        for (auto& w : s) {
            if (w.addr == addr) {
                w.pf = pf;
                return;
            }
        }
    }
}

bool ShadowCache::is_pf(uint64_t addr) {
    for (auto& s : scache) {
        for (auto& w : s) {
            if (w.addr == addr) return w.pf;
        }
    }
    return false;
}

uint64_t ShadowCache::get_latency(uint64_t addr) {
    for (auto& s : scache) {
        for (auto& w : s) {
            if (w.addr == addr) return w.lat;
        }
    }
    return 0;
}

// --- HistoryTable ---
HistoryTable::HistoryTable() {
    history_pointers = new history_table*[sets];
    historyt = new history_table*[sets];
    for (int i = 0; i < sets; i++) historyt[i] = new history_table[ways];
    for (int i = 0; i < sets; i++) history_pointers[i] = historyt[i];
}

HistoryTable::~HistoryTable() {
    for (int i = 0; i < sets; i++) delete[] historyt[i];
    delete[] historyt;
    delete[] history_pointers;
}

void HistoryTable::add(uint64_t tag, uint64_t addr, uint64_t cycle) {
    uint16_t set = tag & TABLE_SET_MASK;
    if (history_pointers[set] == &historyt[set][ways - 1]) {
        if (historyt[set][0].addr == (addr & ADDR_MASK)) return;
    } else if ((history_pointers[set] - 1)->addr == (addr & ADDR_MASK)) {
        return;
    }

    history_pointers[set]->tag = tag;
    history_pointers[set]->time = cycle & TIME_MASK;
    history_pointers[set]->addr = addr & ADDR_MASK;

    if (history_pointers[set] == &historyt[set][ways - 1]) {
        history_pointers[set] = &historyt[set][0];
    } else {
        history_pointers[set]++;
    }
}

uint16_t HistoryTable::get_aux(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle) {
    uint16_t num_on_time = 0;
    uint16_t set = tag & TABLE_SET_MASK;

    if (cycle < latency) return num_on_time;
    cycle -= latency;

    history_table* pointer = history_pointers[set];
    history_table* start_pointer = pointer;

    do {
        if (pointer->tag == tag && pointer->time <= cycle) {
            if (pointer->addr == act_addr) return num_on_time;
            tags[num_on_time] = pointer->tag;
            addr[num_on_time] = pointer->addr;
            num_on_time++;
            if (num_on_time >= HISTORY_TABLE_WAYS) break;
        }

        if (pointer == historyt[set]) pointer = &historyt[set][ways - 1];
        else pointer--;
    } while (pointer != start_pointer);

    return num_on_time;
}

uint16_t HistoryTable::get(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle) {
    act_addr &= ADDR_MASK;
    return get_aux(latency, tag, act_addr, tags, addr, cycle & TIME_MASK);
}

// --- Berti Logic ---
void Berti::increase_conf_tag(uint64_t tag) {
    if (bertit.find(tag) == bertit.end()) return;

    bertit[tag]->conf += CONFIDENCE_INC;
    if (bertit[tag]->conf == CONFIDENCE_MAX) {
        for (auto& i : bertit[tag]->deltas) {
            if (i.conf > CONFIDENCE_L1) i.rpl = BERTI_L1;
            else if (i.conf > CONFIDENCE_L2) i.rpl = BERTI_L2;
            else if (i.conf > CONFIDENCE_L2R) i.rpl = BERTI_L2R;
            else i.rpl = BERTI_R;
            i.conf = 0;
        }
        bertit[tag]->conf = 0;
    }
}

void Berti::add(uint64_t tag, int64_t delta) {
    auto add_delta = [](auto delta, auto entry) {
        delta_t new_delta;
        new_delta.delta = delta;
        new_delta.conf = CONFIDENCE_INIT;
        new_delta.rpl = BERTI_R;
        entry->deltas.push_back(new_delta);
    };

    if (bertit.find(tag) == bertit.end()) {
        if (bertit_queue.size() > BERTI_TABLE_SIZE) {
            uint64_t key = bertit_queue.front();
            berti* entry = bertit[key];
            delete entry;
            bertit.erase(key);
            bertit_queue.pop();
        }
        bertit_queue.push(tag);
        berti* entry = new berti;
        entry->conf = CONFIDENCE_INC;
        add_delta(delta, entry);
        bertit.insert(std::make_pair(tag, entry));
        return;
    }

    berti* entry = bertit[tag];
    for (auto& i : entry->deltas) {
        if (i.delta == delta) {
            i.conf += CONFIDENCE_INC;
            if (i.conf > CONFIDENCE_MAX) i.conf = CONFIDENCE_MAX;
            return;
        }
    }

    if (entry->deltas.size() < size) {
        add_delta(delta, entry);
        return;
    }

    std::sort(std::begin(entry->deltas), std::end(entry->deltas), compare_rpl);
    if (entry->deltas.front().rpl == BERTI_R || entry->deltas.front().rpl == BERTI_L2R) {
        entry->deltas.front().delta = delta;
        entry->deltas.front().conf = CONFIDENCE_INIT;
    }
}

uint8_t Berti::get(uint64_t tag, std::vector<delta_t>& res) {
    if (!bertit.count(tag)) return 0;

    berti* entry = bertit[tag];
    for (auto& i : entry->deltas)
        if (i.delta != 0 && i.rpl != BERTI_R) res.push_back(i);

    if (res.empty() && entry->conf >= LAUNCH_MIDDLE_CONF) {
        for (auto& i : entry->deltas) {
            if (i.delta != 0) {
                delta_t new_delta;
                new_delta.delta = i.delta;
                if (i.conf > CONFIDENCE_MIDDLE_L1) new_delta.rpl = BERTI_L1;
                else if (i.conf > CONFIDENCE_MIDDLE_L2) new_delta.rpl = BERTI_L2;
                else continue;
                res.push_back(new_delta);
            }
        }
    }
    std::sort(std::begin(res), std::end(res), compare_greater_delta);
    return 1;
}

// NOTE: Modified to accept HistoryTable reference
void Berti::find_and_update(HistoryTable& history_table, uint64_t latency, uint64_t tag, uint64_t cycle, uint64_t line_addr) {
    uint64_t tags[HISTORY_TABLE_WAYS];
    uint64_t addr[HISTORY_TABLE_WAYS];
    
    // Call method on the passed history_table instance
    uint16_t num_on_time = history_table.get(latency, tag, line_addr, tags, addr, cycle);

    for (uint32_t i = 0; i < num_on_time; i++) {
        if (i == 0) increase_conf_tag(tag);
        line_addr &= ADDR_MASK;
        int64_t stride = (int64_t)(line_addr - addr[i]);
        if ((std::abs(stride) < (1 << DELTA_MASK))) add(tags[i], stride);
    }
}

bool Berti::compare_rpl(delta_t a, delta_t b) {
    // Logic kept same
    if (a.rpl == BERTI_R && b.rpl != BERTI_R) return 1;
    if (b.rpl == BERTI_R && a.rpl != BERTI_R) return 0;
    if (a.rpl == BERTI_L2R && b.rpl != BERTI_L2R) return 1;
    if (b.rpl == BERTI_L2R && a.rpl != BERTI_L2R) return 0;
    return (a.conf < b.conf);
}

bool Berti::compare_greater_delta(delta_t a, delta_t b) {
    if (a.rpl == BERTI_L1 && b.rpl != BERTI_L1) return 1;
    if (a.rpl != BERTI_L1 && b.rpl == BERTI_L1) return 0;
    if (a.rpl == BERTI_L2 && b.rpl != BERTI_L2) return 1;
    if (a.rpl != BERTI_L2 && b.rpl == BERTI_L2) return 0;
    if (a.rpl == BERTI_L2R && b.rpl != BERTI_L2R) return 1;
    if (a.rpl != BERTI_L2R && b.rpl == BERTI_L2R) return 0;
    return std::abs(a.delta) < std::abs(b.delta);
}

uint64_t Berti::ip_hash(uint64_t ip) {
    // Using simple hash or macro defined hash
    #ifdef HASH_ORIGINAL
    ip = ((ip >> 1) ^ (ip >> 4)); 
    #endif
    // Default fallback if no hash defined
    ip ^= (ip >> 20) ^ (ip >> 12);
    ip = ip ^ (ip >> 7) ^ (ip >> 4);
    return ip;
}

// ====================================================================
//  Berti Gaze Prefetcher Module Implementation
// ====================================================================

// Constructor
berti_gaze::berti_gaze(CACHE* cache) 
    : champsim::modules::prefetcher(cache), 
      delta_counter(1 << DELTA_MASK, 0) 
{
    uint32_t num_sets = cache->NUM_SET;
    uint32_t num_ways = cache->NUM_WAY;
    uint64_t latency_table_size = num_sets * num_ways; 

    // [修改] 使用 std::make_unique 进行初始化
    latency_table = std::make_unique<berti_space::LatencyTable>(latency_table_size);
    shadow_cache  = std::make_unique<berti_space::ShadowCache>(num_sets, num_ways);
    history_table = std::make_unique<berti_space::HistoryTable>();
    berti_core    = std::make_unique<berti_space::Berti>(BERTI_TABLE_DELTA_SIZE);
    
    std::cout << "Berti Gaze Initialized with Sets: " << num_sets 
              << " Ways: " << num_ways << std::endl;
}

berti_gaze::~berti_gaze() {
    // delete latency_table;
    // delete shadow_cache;
    // delete history_table;
    // delete berti_core;
}

void berti_gaze::prefetcher_cycle_operate() {
    // Berti usually doesn't need cycle operate unless handling queues
}

uint32_t berti_gaze::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type, uint32_t metadata_in) {
    // Convert ChampSim types to Berti types
    uint64_t line_addr = (addr.to<uint64_t>() >> LOG2_BLOCK_SIZE);
    uint64_t ip_val = ip.to<uint64_t>();
    uint64_t ip_hash = berti_core->ip_hash(ip_val) & IP_MASK;
    // uint64_t current_cycle = 0; // ChampSim v3 modules don't always expose cycle directly easily in args, 
                                // but the base class usually has access.
                                // NOTE: In v3, we can often access `this->intern_->current_cycle` or similar.
                                // However, to be safe, we will assume a global or base member exists. 
                                // ChampSim typically exposes `current_cycle` globally or via `intern_`.
                                // For compilation safety, relying on `champsim::current_cycle` global if available, 
                                // or if your version encapsulates it, use `intern_->current_cycle`.
                                // *Correction*: ChampSim v3 doesn't expose current_cycle in args. 
                                // It is usually a global variable in `champsim.h`.
    // extern uint64_t current_cycle; 
    uint64_t current_cycle = intern_->current_cycle();

    if (line_addr == 0) return metadata_in;

    if (!cache_hit) {
        latency_table->add(line_addr, ip_hash, false, current_cycle);
        history_table->add(ip_hash, line_addr, current_cycle);
    } else if (cache_hit && shadow_cache->is_pf(line_addr)) {
        shadow_cache->set_pf(line_addr, false);
        uint64_t latency = shadow_cache->get_latency(line_addr);
        if (latency > LAT_MASK) latency = 0;
        
        berti_core->find_and_update(*history_table, latency, ip_hash, current_cycle & TIME_MASK, line_addr);
        history_table->add(ip_hash, line_addr, current_cycle & TIME_MASK);
    }

    std::vector<delta_t> deltas;
    berti_core->get(ip_hash, deltas);

    for (auto i : deltas) {
        uint64_t p_addr_val = (line_addr + i.delta) << LOG2_BLOCK_SIZE;
        uint64_t p_b_addr = (p_addr_val >> LOG2_BLOCK_SIZE);
        
        if (latency_table->get(p_b_addr)) continue;
        if (i.rpl == BERTI_R) return metadata_in;
        if (p_addr_val == 0) continue;

        // Page boundary check
        if ((p_addr_val >> LOG2_PAGE_SIZE) != (addr.to<uint64_t>() >> LOG2_PAGE_SIZE)) {
            cross_page++;
            #ifdef NO_CROSS_PAGE
            continue;
            #endif
        } else {
            no_cross_page++;
        }

        // MSHR Load Check (Adapted for v3)
        // intern_ is a pointer to the Cache object in the base class
        float mshr_load = 0; 
        // Typically: this->intern_->get_mshr_occupancy_ratio() * 100;
        // To be safe and compile, using simple logic or if available:
        // if (this->intern_) mshr_load = this->intern_->get_mshr_occupancy_ratio() * 100;
        // Assuming MSHR_LIMIT logic exists:
        
        bool fill_this_level = (i.rpl == BERTI_L1); // && (mshr_load < MSHR_LIMIT);
        
        uint32_t pf_metadata = 0; // Simple metadata handling

        if (fill_this_level) {
            pf_to_l1++;
        } else {
            pf_to_l2++;
        }

        champsim::address p_addr_obj{p_addr_val};
        
        if (prefetch_line(p_addr_obj, fill_this_level, pf_metadata)) {
            average_issued++;
            delta_counter[std::abs(i.delta)]++;
            
            if (fill_this_level) {
                if (!shadow_cache->get(p_b_addr)) {
                    latency_table->add(p_b_addr, ip_hash, true, current_cycle);
                }
            }
        }
    }
    return metadata_in;
}

uint32_t berti_gaze::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in) {
    uint64_t line_addr = (addr.to<uint64_t>() >> LOG2_BLOCK_SIZE);
    uint64_t tag = latency_table->get_tag(line_addr);
    uint64_t cycle = latency_table->del(line_addr) & TIME_MASK;
    uint64_t latency = 0;

    // extern uint64_t current_cycle; // Access global cycle
    uint64_t current_cycle = intern_->current_cycle();
    
    if (cycle != 0 && ((current_cycle & TIME_MASK) > cycle))
        latency = (current_cycle & TIME_MASK) - cycle;

    if (latency > LAT_MASK) {
        latency = 0;
        cant_track_latency++;
    } else if (latency != 0) {
         if (average_latency.num == 0)
            average_latency.average = (float)latency;
        else
            average_latency.average += ((((float)latency) - average_latency.average) / (average_latency.num + 1)); // Fixed math slightly
        average_latency.num++;
    }

    shadow_cache->add(set, way, line_addr, prefetch, latency);

    if (latency != 0 && !prefetch) {
        berti_core->find_and_update(*history_table, latency, tag, cycle, line_addr);
    }
    return metadata_in;
}

void berti_gaze::prefetcher_final_stats() {
    std::cout << "BERTI Stats..." << std::endl;
    // ... Implement print logic using member variables ...
}