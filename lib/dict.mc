import "memory";
import "hash";

#include <string.h>

/**
 * Hash map from NUL-terminated string keys (uint8*) to V values, with the
 * same open-addressing layout as lib/set.mc but content-keyed: keys hash
 * and compare by their bytes, and the dict owns private copies of them
 * (copied on insert, freed on remove/destroy). Callers keep ownership of
 * the strings they pass in and may free or reuse them immediately.
 *
 * Slot states: 0 = empty, 1 = occupied, 2 = tombstone.
 */
struct dict_entry<V> {
    key: uint8*;  // owned copy
    value: V;
    state: uint8;
}

struct dict<V> {
    entries: struct dict_entry<V>*;  // heap-allocated slot array
    length: uint64;                  // number of live entries
    capacity: uint64;                // total allocated slots
}

/**
 * Compares two NUL-terminated strings byte by byte.
 *
 * @param a: first string
 * @param b: second string
 *
 * @return true if the contents are equal
 */
@static
fn str_eq(a: uint8*, b: uint8*) -> bool {
    let i: uint64 = 0;
    while (a[i] == b[i]) {
        if (a[i] == 0)
            return true;
        i = i + 1;
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
@static
fn str_clone(s: uint8*) -> uint8* {
    let n = strlen(s) + 1;
    let copy = alloc<uint8>(n);
    copy_bytes(copy, s, n);
    return copy;
}

/**
 * Allocates the backing slot array and initialises an empty dict.
 *
 * @param self:     dict to initialise
 * @param capacity: initial slot count; must be > 0
 */
fn dict_init<V>(self: struct dict<V>*, capacity: uint64) {
    self->entries = alloc<struct dict_entry<V>>(capacity);
    self->length = 0;
    self->capacity = capacity;

    let i: uint64 = 0;
    while (i < capacity) {
        self->entries[i].state = 0;
        i = i + 1;
    }
}

/**
 * Frees the owned key copies and the backing slot array, and zeroes the
 * dict fields.
 *
 * @param self: dict to destroy
 */
fn dict_destroy<V>(self: struct dict<V>*) {
    let i: uint64 = 0;
    while (i < self->capacity) {
        if (self->entries[i].state == 1)
            dealloc(self->entries[i].key);
        i = i + 1;
    }
    dealloc(self->entries);

    self->entries = null;
    self->length = 0;
    self->capacity = 0;
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
fn dict_set<V>(self: struct dict<V>*, key: uint8*, value: V) {
    if (self->length * 10 >= self->capacity * 7)
        dict_grow(self);

    let slot = hash(key) % self->capacity;
    let tombstone_slot: uint64 = 0;
    let has_tombstone = false;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (str_eq(self->entries[slot].key, key)) {
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

    self->entries[slot].key = str_clone(key);
    self->entries[slot].value = value;
    self->entries[slot].state = 1;
    self->length = self->length + 1;
}

/**
 * Looks up key by content and writes the associated value into *out if
 * found.
 *
 * @param self: dict to search
 * @param key:  string key to look up
 * @param out:  written with the found value; unchanged if key is absent
 *
 * @return true if key was found, false otherwise
 */
fn dict_get<V>(self: struct dict<V>*, key: uint8*, out: V*) -> bool {
    let slot = hash(key) % self->capacity;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (str_eq(self->entries[slot].key, key)) {
                *out = self->entries[slot].value;
                return true;
            }
        }
        slot = (slot + 1) % self->capacity;
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
fn dict_remove<V>(self: struct dict<V>*, key: uint8*) {
    let slot = hash(key) % self->capacity;

    while (self->entries[slot].state != 0) {
        if (self->entries[slot].state == 1) {
            if (str_eq(self->entries[slot].key, key)) {
                dealloc(self->entries[slot].key);
                self->entries[slot].key = null;
                self->entries[slot].state = 2;
                self->length = self->length - 1;
                return;
            }
        }
        slot = (slot + 1) % self->capacity;
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
fn dict_grow<V>(self: struct dict<V>*) {
    let old_capacity = self->capacity;
    let old_entries = self->entries;

    let new_capacity: uint64 = old_capacity * 2;
    let new_entries = alloc<struct dict_entry<V>>(new_capacity);

    let i: uint64 = 0;
    while (i < new_capacity) {
        new_entries[i].state = 0;
        i = i + 1;
    }

    i = 0;
    while (i < old_capacity) {
        if (old_entries[i].state == 1) {
            let slot = hash(old_entries[i].key) % new_capacity;
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
