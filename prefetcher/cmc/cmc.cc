#include "cmc.h"
#include <iostream>
#include <algorithm>

// =============================================================
// Recorder Implementation
// =============================================================

cmc::Recorder::Recorder(int d) : index(0), degree(d) {}

bool cmc::Recorder::entry_empty() { 
    return entries.empty(); 
}

bool cmc::Recorder::train_entry(uint64_t addr, bool *finished) {
    if (index == 0) {
        entries.push_back(addr);
        index++;
        return true; 
    }
    // 达到最大录制长度，标记完成
    if (index >= degree) {
        entries.push_back(addr);
        index++;
        *finished = true;
    } else {
        entries.push_back(addr);
        index++;
    }
    return true;
}

void cmc::Recorder::reset() { 
    index = 0; 
    entries.clear(); 
}

// =============================================================
// CMC Class Implementation
// =============================================================

// 构造函数
cmc::cmc(CACHE* cache_ptr) 
    : cache(cache_ptr), 
      recorder(MAX_DEGREE), 
      acc_id(1), 
      current_tick(0) 
{
    // 初始化 Storage
    storage.resize(STORAGE_SETS);
    for (int i = 0; i < STORAGE_SETS; i++) {
        storage[i].resize(STORAGE_WAYS);
    }
}

// Bind 函数
void cmc::bind(CACHE* cache_ptr) {
    this->cache = cache_ptr;
}

// 核心操作接口
uint32_t cmc::operate(uint64_t addr, uint64_t ip, bool cache_hit, bool useful_prefetch, uint8_t type, uint32_t metadata_in) {
    // 调用内部计算逻辑
    std::vector<uint64_t> pfs = calculate_prefetch(ip, addr, cache_hit);

    // 发送预取请求
    for (auto pf_addr : pfs) {
        // 参数: ip, base_addr, prefetch_addr, fill_this_level, metadata
        // cache->prefetch_line(ip, addr, pf_addr, true, metadata_in);
        cache->prefetch_line(champsim::address(pf_addr), true, metadata_in);
    }

    return metadata_in;
}

void cmc::initialize() {}
void cmc::cycle_operate() {}
void cmc::final_stats() {}
void cmc::branch_operate(uint64_t ip, uint8_t branch_type, uint64_t branch_target) {}

// =============================================================
// Helper Functions
// =============================================================

uint64_t cmc::hash_index(uint64_t block_addr, uint64_t pc) {
    return block_addr ^ (pc << 8);
}

uint64_t cmc::block_address(uint64_t addr) {
    return (addr >> 6) << 6; 
}

bool cmc::filter_check_and_add(uint64_t addr) {
    auto it = filter_map.find(addr);
    if (it != filter_map.end()) {
        // Hit: 移到 LRU 头部
        filter_lru_list.splice(filter_lru_list.begin(), filter_lru_list, it->second);
        return true; // 已被过滤
    }

    // Miss: 检查容量
    if (filter_map.size() >= FILTER_SIZE) {
        uint64_t last = filter_lru_list.back();
        filter_lru_list.pop_back();
        filter_map.erase(last);
    }
    // 插入新元素
    filter_lru_list.push_front(addr);
    filter_map[addr] = filter_lru_list.begin();
    return false; 
}

cmc::StorageEntry* cmc::find_entry(uint64_t key) {
    uint64_t set_idx = key % STORAGE_SETS;
    uint64_t tag = key / STORAGE_SETS;
    for (auto &way : storage[set_idx]) {
        if (way.valid && way.tag == tag) return &way;
    }
    return nullptr;
}

void cmc::update_lru_tick(StorageEntry* entry) { 
    entry->lru_tick = current_tick; 
}

void cmc::invalidate_entry(StorageEntry* entry) { 
    if (entry->valid) entry->valid = false; 
}

cmc::StorageEntry* cmc::find_victim(uint64_t key) {
    uint64_t set_idx = key % STORAGE_SETS;
    // 1. 找无效块
    for (auto &way : storage[set_idx]) {
        if (!way.valid) return &way;
    }
    // 2. 找 LRU
    StorageEntry* victim = &storage[set_idx][0];
    for (auto &way : storage[set_idx]) {
        if (way.lru_tick < victim->lru_tick) victim = &way;
    }
    return victim;
}

void cmc::insert_entry(uint64_t key, const std::vector<uint64_t>& data, uint64_t id) {
    StorageEntry* entry = find_victim(key);
    uint64_t tag = key / STORAGE_SETS;
    entry->valid = true;
    entry->tag = tag;
    entry->addresses = data; 
    entry->refcnt = 0;
    entry->id = id;
    entry->lru_tick = current_tick;
}

// =============================================================
// Core Logic: calculate_prefetch
// =============================================================

std::vector<uint64_t> cmc::calculate_prefetch(uint64_t pc, uint64_t vaddr, bool cache_hit) {
    current_tick++; 
    std::vector<uint64_t> prefetches;
    uint64_t block_addr = block_address(vaddr);
    
    // 简化逻辑：Cache Miss 视为未覆盖
    bool nocovered = !cache_hit; 
    
    // 关键 Hash 计算
    uint64_t lookup_key = hash_index(block_addr >> 6, pc);
    StorageEntry *match_entry = find_entry(lookup_key);

    // 1. 预取重放 (Replay)
    if (nocovered && match_entry) {
        update_lru_tick(match_entry);
        match_entry->refcnt++;
        for (auto addr : match_entry->addresses) {
            if (filter_check_and_add(addr)) continue;
            prefetches.push_back(addr);
        }
    } else if (match_entry) {
        // 如果预测命中但 Cache 也命中了 (说明不需要预取)，使条目失效
        invalidate_entry(match_entry);
    }

    // 2. 训练逻辑 (Training)
    bool finished = false;
    
    // 训练触发条件：(Trigger 栈为空 或 正在追踪当前流) 且 栈未满
    bool train_trigger = (trigger.empty() || match_entry) && (trigger.size() < STACK_SIZE);
    
    // 实际录制条件：不是刚触发那一刻 && 栈不为空 && 发生了 Miss
    bool do_training = !train_trigger && !trigger.empty() && nocovered;

    if (train_trigger) {
        trigger.push_back(RecordEntry(pc, block_addr));
    }

    if (do_training) {
        bool trained = recorder.train_entry(block_addr, &finished);
        
        if (finished) {
            RecordEntry &trigger_head = trigger.front();
            
            // 计算 Head 的 Key，用于存入 Storage
            uint64_t head_key = hash_index(trigger_head.addr >> 6, trigger_head.pc);
            
            StorageEntry *entry = find_entry(head_key);
            if (entry) {
                // 更新现有
                entry->addresses = recorder.entries;
                entry->refcnt++;
                entry->id = acc_id;
                update_lru_tick(entry);
            } else {
                // 插入新项
                insert_entry(head_key, recorder.entries, acc_id);
            }

            // 清理状态
            trigger.pop_front();
            recorder.reset();
            acc_id++;
        }
    }

    return prefetches;
}