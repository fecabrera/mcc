fn fnv1a<T>(key: T*) -> uint64 {
    let hash: uint64 = 14695981039346656037;
    let i: uint64 = 0;
    while (key[i]) {
        hash = (hash ^ key[i] as uint64) * 1099511628211;
        i = i + 1;
    }
    return hash;
}