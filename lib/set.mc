import "memory";
import "splitmix64";

/**
 * Open-addressing hash table with linear probing; maps K keys to V values.
 * K must be an integer or pointer type (keys are hashed through a uint64
 * cast). Grows automatically when the load factor reaches 70%.
 *
 * Slot states: 0 = empty, 1 = occupied, 2 = tombstone.
 */
struct set_entry<K, V> {
    key: K;
    value: V;
    state: uint8;
}

struct set<K, V> {
    entries: struct set_entry<K, V>*;  // heap-allocated slot array
    length: uint64;                    // number of live entries
    capacity: uint64;                  // total allocated slots
}

/**
 * Allocates the backing slot array and initialises an empty set.
 *
 * @param self:     set to initialise
 * @param capacity: initial slot count; must be > 0
 */
fn set_init<K, V>(self: struct set<K, V>*, capacity: uint64) {
    self->entries = alloc<struct set_entry<K, V>>(capacity);
    self->length = 0;
    self->capacity = capacity;

    let i: uint64 = 0;
    while (i < capacity) {
        self->entries[i].state = 0;
        i = i + 1;
    }
}

/**
 * Frees the backing slot array and zeroes the set fields.
 *
 * @param self: set to destroy
 */
fn set_destroy<K, V>(self: struct set<K, V>*) {
    dealloc(self->entries);

    self->entries = null;
    self->length = 0;
    self->capacity = 0;
}

/**
 * Inserts or updates the entry for key. Grows the backing array if the
 * load factor reaches 70%.
 *
 * @param self:  set to insert into
 * @param key:   key to insert or update
 * @param value: value to associate with key
 */
fn set_set<K, V>(self: struct set<K, V>*, key: K, value: V) {
    if (self->length * 10 >= self->capacity * 7)
        _set_grow(self);

    let slot = splitmix64(key as uint64) % self->capacity;
    let tombstone_slot: uint64 = 0;
    let has_tombstone = false;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (self->entries[slot].key == key) {
                self->entries[slot].value = value;
                return;
            }
        } else if (!has_tombstone) {
            has_tombstone = true;
            tombstone_slot = slot;
        }
        slot = (slot + 1) % self->capacity;
    }

    if (has_tombstone)
        slot = tombstone_slot;

    self->entries[slot].key = key;
    self->entries[slot].value = value;
    self->entries[slot].state = 1;
    self->length = self->length + 1;
}

/**
 * Looks up key and writes the associated value into *out if found.
 *
 * @param self: set to search
 * @param key:  key to look up
 * @param out:  written with the found value; unchanged if key is absent
 *
 * @return true if key was found, false otherwise
 */
fn set_get<K, V>(self: struct set<K, V>*, key: K, out: V*) -> bool {
    let slot = splitmix64(key as uint64) % self->capacity;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (self->entries[slot].key == key) {
                *out = self->entries[slot].value;
                return true;
            }
        }
        slot = (slot + 1) % self->capacity;
    }

    return false;
}

/**
 * Removes the entry for key. Does nothing if key is not present.
 *
 * @param self: set to remove from
 * @param key:  key to remove
 */
fn set_remove<K, V>(self: struct set<K, V>*, key: K) {
    let slot = splitmix64(key as uint64) % self->capacity;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (self->entries[slot].key == key) {
                self->entries[slot].state = 2;
                self->length = self->length - 1;
                return;
            }
        }
        slot = (slot + 1) % self->capacity;
    }
}

/**
 * Doubles the slot array and rehashes the occupied entries into it.
 * Internal; called by set_set when the load factor reaches 70%.
 *
 * @param self: set to grow
 */
fn _set_grow<K, V>(self: struct set<K, V>*) {
    let old_capacity = self->capacity;
    let old_entries = self->entries;

    let new_capacity: uint64 = old_capacity * 2;
    let new_entries = alloc<struct set_entry<K, V>>(new_capacity);

    let i: uint64 = 0;
    while (i < new_capacity) {
        new_entries[i].state = 0;
        i = i + 1;
    }

    i = 0;
    while (i < old_capacity) {
        if (old_entries[i].state == 1) {
            let slot = splitmix64(old_entries[i].key as uint64) % new_capacity;
            while (new_entries[slot].state == 1)
                slot = (slot + 1) % new_capacity;

            new_entries[slot] = old_entries[i];
        }
        i = i + 1;
    }

    dealloc(old_entries);
    self->entries = new_entries;
    self->capacity = new_capacity;
}
