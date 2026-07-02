import "memory";
import "hash";

// Slot states
enum set_entry_state: uint8 {
    EMPTY = 0,
    OCCUPIED = 1,
    TOMBSTONE = 2,
}

/**
 * One slot in a set's backing array. Extends `pair<K, V>` with a slot state,
 * so it inherits the entry's key/value and upcasts to a `struct pair<K, V>*`
 * when yielded during iteration.
 *
 * @field key:   the entry's key; valid only when state == OCCUPIED
 * @field value: associated value; valid only when state == OCCUPIED
 * @field state: slot lifecycle — EMPTY (0), OCCUPIED (1), or TOMBSTONE (2)
 */
struct set_entry<K, V> extends pair<K, V> {
    state: uint8;
}

/**
 * Open-addressing hash table with linear probing; maps K keys to V values.
 * Integer keys hash by value (splitmix64); pointer keys hash by content as
 * NUL-terminated buffers (fnv1a) but still compare by address. Grows
 * automatically when the load factor reaches 70%.
 *
 * @field entries:  heap-allocated slot array
 * @field length:   number of live entries
 * @field capacity: total allocated slots
 */
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

    for i in range(capacity) {
        self->entries[i].state = set_entry_state::EMPTY;
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
        set_grow(self);

    let slot = hash(key) % self->capacity;
    let tombstone_slot: uint64 = 0;
    let has_tombstone = false;

    while (self->entries[slot].state != set_entry_state::EMPTY) {
        if (self->entries[slot].state == set_entry_state::OCCUPIED) {
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
    self->entries[slot].state = set_entry_state::OCCUPIED;
    self->length += 1;
}

/**
 * Looks up key and writes the associated value into out if found.
 *
 * @param self: set to search
 * @param key:  key to look up
 * @param out:  written with the found value; unchanged if key is absent
 *
 * @return true if key was found, false otherwise
 */
fn set_get<K, V>(self: struct set<K, V>*, key: K, mut out: V) -> bool {
    let slot = hash(key) % self->capacity;

    while (self->entries[slot].state != set_entry_state::EMPTY) {
        if (self->entries[slot].state == set_entry_state::OCCUPIED) {
            if (self->entries[slot].key == key) {
                out = self->entries[slot].value;
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
    let slot = hash(key) % self->capacity;

    while (self->entries[slot].state != set_entry_state::EMPTY) {
        if (self->entries[slot].state == set_entry_state::OCCUPIED) {
            if (self->entries[slot].key == key) {
                self->entries[slot].state = set_entry_state::TOMBSTONE;
                self->length -= 1;
                return;
            }
        }
        slot += 1;
        slot %= self->capacity;
    }
}

/**
 * Doubles the slot array and rehashes the occupied entries into it.
 * Internal; called by set_set when the load factor reaches 70%.
 *
 * @param self: set to grow
 */
@private
fn set_grow<K, V>(self: struct set<K, V>*) {
    let old_capacity = self->capacity;
    let old_entries = self->entries;

    let new_capacity: uint64 = old_capacity * 2;
    let new_entries = alloc<struct set_entry<K, V>>(new_capacity);

    let i: uint64 = 0;
    while (i < new_capacity) {
        new_entries[i].state = set_entry_state::EMPTY;
        i += 1;
    }

    i = 0;
    while (i < old_capacity) {
        if (old_entries[i].state == set_entry_state::OCCUPIED) {
            let slot = hash(old_entries[i].key) % new_capacity;
            while (new_entries[slot].state == set_entry_state::OCCUPIED)
                slot = (slot + 1) % new_capacity;

            new_entries[slot] = old_entries[i];
        }
        i += 1;
    }

    dealloc(old_entries);
    self->entries = new_entries;
    self->capacity = new_capacity;
}

/***************************************
 * Iteration
 ***************************************/

/**
 * Begins an iteration over a set's key/value pairs, in unspecified
 * (hash-table slot) order. Part of the `set_it`/`set_next` protocol (used by
 * `for ... in`); pair it with `set_next`.
 *
 * @param self: set to iterate
 *
 * @return an iterator positioned before the first occupied entry
 */
fn set_it<K, V>(self: struct set<K, V>*) -> struct iterator<struct set<K, V>> {
    return struct iterator<struct set<K, V>> { obj = self, idx = 0 };
}

/**
 * Advances to the next occupied entry and writes its key/value into out.
 *
 * @param it:  iterator to advance
 * @param out: pair the next entry is written to; untouched when the set is
 *             exhausted
 *
 * @return true if a pair was produced, false once iteration is complete
 */
fn set_next<K, V>(it: struct iterator<struct set<K, V>>*, out: struct pair<K, V>*) -> bool {
    while (it->idx < it->obj->capacity) {
        let entry = it->obj->entries[it->idx];
        defer it->idx += 1;

        if (entry.state == set_entry_state::OCCUPIED) {
            *out = entry as struct pair<K, V>;
            return true;
        }
    }

    return false;
}
