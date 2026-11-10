import { useEffect, useState } from "react";
import { getSession, initSession, onAuthChange } from "./session";

/** The signed-in attendee (or null) and whether the stored session is still being restored. */
export function useAuthSession() {
  const [session, setSession] = useState(() => getSession());
  const [restoring, setRestoring] = useState(true);

  useEffect(() => {
    let alive = true;
    const unsubscribe = onAuthChange((next) => alive && setSession(next));
    initSession().then((restored) => {
      if (alive) {
        setSession(restored || getSession());
        setRestoring(false);
      }
    });
    return () => {
      alive = false;
      unsubscribe();
    };
  }, []);

  return { session, signedIn: !!session, restoring };
}
