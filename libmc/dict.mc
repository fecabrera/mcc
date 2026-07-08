import "memory";
import "hash";
import "set";
import "libc/string";

// Slot states
enum dict_entry_state: uint8 {
    EMPTY = 0,
    OCCUPIED = 1,
    TOMBSTONE = 2,
}

/**
 * One slot in a dict's backing array. A specialization of
 * `set_entry<char*, V>` (string keys), so it inherits the pair's key/value and
 * the slot state field.
 *
 * @field key:   owned, heap-allocated copy of the NUL-terminated key string;
 *               null when state != OCCUPIED
 * @field value: associated value; valid only when state == OCCUPIED
 * @field state: slot lifecycle — EMPTY (0), OCCUPIED (1), or TOMBSTONE (2)
 */
struct dict_entry<V> extends set_entry<char*, V>;

/**
 * Open-addressing hash map from NUL-terminated string keys to V values.
 * Keys are content-hashed and compared by bytes; the dict owns private copies
 * (allocated on insert, freed on remove/destroy). Callers keep ownership of
 * the strings they pass in and may free or reuse them immediately.
 * Grows automatically when the load factor reaches 70%.
 *
 * @field entries:  heap-allocated slot array of length capacity
 * @field length:   number of live (OCCUPIED) entries
 * @field capacity: total number of allocated slots
 */
struct dict<V> {
    entries: dict_entry<V>*;
    length: uint64;
    capacity: uint64;
}

/**
 * Compares two NUL-terminated strings byte by byte.
 *
 * @param a: first string
 * @param b: second string
 *
 * @return true if the contents are equal
 */
@private
fn str_eq(@nonnull a: char*, @nonnull b: char*) -> bool {
    let i: uint64 = 0;
    while (a[i] == b[i]) {
        if (a[i] == 0)
            return true;
        i += 1;
    }
    return false;
}

/**
 * Heap-copies a NUL-terminated string, including the terminator.
 *
 * @param s: string to copy
 *
 * @return owned copy; release with dealloc
 */
@private
fn str_clone(@nonnull s: char*) -> char* {
    let n = strlen(s) + 1;
    let copy = alloc<char>(n);
    bytecopy(copy!, s, n);   // allocation assumed to succeed
    return copy;
}

/**
 * Allocates the backing slot array and initialises an empty dict.
 *
 * @param self:     dict to initialise
 * @param capacity: initial slot count; must be > 0
 */
fn dict_init<V>(mut self: dict<V>, capacity: uint64) {
    self.entries = alloc<struct dict_entry<V>>(capacity);
    self.length = 0;
    self.capacity = capacity;

    for i in range(capacity) {
        self.entries[i].state = dict_entry_state::EMPTY;
    }
}

/**
 * Frees the owned key copies and the backing slot array, and zeroes the
 * dict fields.
 *
 * @param self: dict to destroy
 */
fn dict_destroy<V>(mut self: dict<V>) {
    for i in range(self.capacity) {
        if (self.entries[i].state == dict_entry_state::OCCUPIED)
            dealloc(self.entries[i].key);
    }
    dealloc(self.entries);

    self.entries = null;
    self.length = 0;
    self.capacity = 0;
}

/**
 * Inserts or updates the entry for key. New keys are copied into the dict;
 * updating an existing key reuses its stored copy. Grows the backing array
 * if the load factor reaches 70%.
 *
 * @param self:  dict to insert into
 * @param key:   string key; the caller keeps ownership
 * @param value: value to associate with key
 */
fn dict_set<V>(mut self: dict<V>, @nonnull key: char*, value: V) {
    if (self.length * 10 >= self.capacity * 7)
        dict_grow(self);

    let slot = hash(key) % self.capacity;
    let tombstone_slot: uint64 = 0;
    let has_tombstone = false;

    while (self.entries[slot].state != dict_entry_state::EMPTY) {
        if (self.entries[slot].state == dict_entry_state::OCCUPIED) {
            // OCCUPIED slots always hold an owned key copy
            if (str_eq(self.entries[slot].key!, key)) {
                self.entries[slot].value = value;
                return;
            }
        } else if (!has_tombstone) {
            has_tombstone = true;
            tombstone_slot = slot;
        }
        slot = (slot + 1) % self.capacity;
    }

    if (has_tombstone)
        slot = tombstone_slot;

    self.entries[slot].key = str_clone(key);
    self.entries[slot].value = value;
    self.entries[slot].state = dict_entry_state::OCCUPIED;
    self.length += 1;
}

/**
 * Looks up key by content and writes the associated value into out if
 * found.
 *
 * @param self: dict to search
 * @param key:  string key to look up
 * @param out:  written with the found value; unchanged if key is absent
 *
 * @return true if key was found, false otherwise
 */
fn dict_get<V>(const self: dict<V>, @nonnull key: char*, mut out: V) -> bool {
    let slot = hash(key) % self.capacity;

    while (self.entries[slot].state != dict_entry_state::EMPTY) {
        if (self.entries[slot].state == dict_entry_state::OCCUPIED) {
            // OCCUPIED slots always hold an owned key copy
            if (str_eq(self.entries[slot].key!, key)) {
                out = self.entries[slot].value;
                return true;
            }
        }
        slot = (slot + 1) % self.capacity;
    }

    return false;
}

/**
 * Removes the entry for key, releasing the dict's copy of it. Does nothing
 * if key is not present.
 *
 * @param self: dict to remove from
 * @param key:  string key to remove
 */
fn dict_remove<V>(mut self: dict<V>, @nonnull key: char*) {
    let slot = hash(key) % self.capacity;

    while (self.entries[slot].state != dict_entry_state::EMPTY) {
        if (self.entries[slot].state == dict_entry_state::OCCUPIED) {
            // OCCUPIED slots always hold an owned key copy
            if (str_eq(self.entries[slot].key!, key)) {
                dealloc(self.entries[slot].key);
                self.entries[slot].key = null;
                self.entries[slot].state = dict_entry_state::TOMBSTONE;
                self.length -= 1;
                return;
            }
        }
        slot = (slot + 1) % self.capacity;
    }
}

/**
 * Doubles the slot array and rehashes the occupied entries into it. The
 * owned key copies move; they are not re-copied. Internal; called by
 * dict_set when the load factor reaches 70%.
 *
 * @param self: dict to grow
 */
@private
fn dict_grow<V>(mut self: dict<V>) {
    let old_capacity = self.capacity;
    let old_entries = self.entries;

    let new_capacity: uint64 = old_capacity * 2;
    let new_entries = alloc<struct dict_entry<V>>(new_capacity);

    let i: uint64 = 0;
    while (i < new_capacity) {
        new_entries[i].state = dict_entry_state::EMPTY;
        i += 1;
    }

    i = 0;
    while (i < old_capacity) {
        if (old_entries[i].state == dict_entry_state::OCCUPIED) {
            let slot = hash(old_entries[i].key) % new_capacity;
            while (new_entries[slot].state == dict_entry_state::OCCUPIED)
                slot = (slot + 1) % new_capacity;

            new_entries[slot] = old_entries[i];
        }
        i += 1;
    }

    dealloc(old_entries);
    self.entries = new_entries;
    self.capacity = new_capacity;
}

/***************************************
 * Iteration
 ***************************************/

/**
 * Begins an iteration over a dict's string-keyed entries, in unspecified
 * (hash-table slot) order. Part of the `dict_it`/`dict_next` protocol (used by
 * `for ... in`); pair it with `dict_next`.
 *
 * @param self: dict to iterate
 *
 * @return an iterator positioned before the first occupied entry
 */
fn dict_it<V>(self: dict<V>*) -> struct iterator<struct dict<V>> {
    return struct iterator<struct dict<V>> { obj = self, idx = 0 };
}

/**
 * Advances to the next occupied entry and writes its key/value into out. The
 * written `key` borrows the dict's own storage and stays valid only while the
 * dict is unmodified.
 *
 * @param it:  iterator to advance
 * @param out: pair the next entry is written to; untouched when the dict is
 *             exhausted
 *
 * @return true if a pair was produced, false once iteration is complete
 */
fn dict_next<V>(it: struct iterator<struct dict<V>>*, out: struct pair<char*, V>*) -> bool {
    while (it->idx < it->obj->capacity) {
        let entry = it->obj->entries[it->idx];
        defer it->idx += 1;

        if (entry.state == dict_entry_state::OCCUPIED) {
            *out = entry as struct pair<char*, V>;
            return true;
        }
    }

    return false;
}
