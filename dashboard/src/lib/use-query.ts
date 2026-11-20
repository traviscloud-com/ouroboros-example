import { useCallback, useEffect, useRef, useState } from "react";

export interface QueryState<T> {
  data: T | null;
  error: Error | null;
  /** True while a fetch is in flight; `data` keeps the previous result (no skeleton flash on refetch). */
  loading: boolean;
  refetch: () => void;
}

/** Minimal fetch-on-deps hook: previous data stays visible while the next range loads. */
export function useQuery<T>(fetcher: () => Promise<T>, deps: readonly unknown[]): QueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const id = ++latest.current;
    setLoading(true);
    setError(null);
    fetcher()
      .then((result) => {
        if (latest.current !== id) return;
        setData(result);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (latest.current !== id) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setData(null);
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refetch = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, refetch };
}
