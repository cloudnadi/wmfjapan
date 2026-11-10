/** Storage wrappers that never throw (private mode / blocked storage) and fall back to memory. */

function makeStore(kind) {
  const memory = new Map();
  const backing = () => {
    try {
      return window[kind];
    } catch {
      return null;
    }
  };
  return {
    get(key) {
      try {
        const value = backing()?.getItem(key);
        return value === undefined || value === null ? (memory.get(key) ?? null) : value;
      } catch {
        return memory.get(key) ?? null;
      }
    },
    set(key, value) {
      memory.set(key, value);
      try {
        backing()?.setItem(key, value);
      } catch {
        // memory copy remains
      }
    },
    remove(key) {
      memory.delete(key);
      try {
        backing()?.removeItem(key);
      } catch {
        // nothing to do
      }
    },
  };
}

// Short-lived, per-tab data (OAuth state, checkout drafts).
export const tabStore = makeStore("sessionStorage");
// Survives closing the browser (the refresh token only).
export const deviceStore = makeStore("localStorage");
