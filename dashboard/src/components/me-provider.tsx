import { createContext, useContext, useEffect, useState } from "react";
import { api, isApiError } from "@/api";
import type { Me, Role } from "@/api/types";

export type MeState =
  | { status: "loading"; me: null }
  | { status: "ok"; me: Me }
  | { status: "unauthenticated"; me: null }
  | { status: "forbidden"; me: null; detail: string }
  | { status: "error"; me: null; detail: string };

const MeContext = createContext<MeState>({ status: "loading", me: null });

/** Resolves `/v1/me` once; routes and nav read the role from here (D4: persona is server-driven). */
export function MeProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<MeState>({ status: "loading", me: null });
  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => !cancelled && setState({ status: "ok", me }))
      .catch((e: unknown) => {
        if (cancelled) return;
        if (isApiError(e, 401)) setState({ status: "unauthenticated", me: null });
        else if (isApiError(e, 403)) setState({ status: "forbidden", me: null, detail: e.detail });
        else setState({ status: "error", me: null, detail: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return <MeContext.Provider value={state}>{children}</MeContext.Provider>;
}

export const useMe = () => useContext(MeContext);

const RANK: Record<Role, number> = { leadership: 0, engineer: 1, admin: 2 };
export const atLeast = (role: Role | undefined, minimum: Role) => role !== undefined && RANK[role] >= RANK[minimum];
