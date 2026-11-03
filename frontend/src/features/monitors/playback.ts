import { create } from "zustand";

/** The timeline's play/pause request (the `play` command, Space). The open document's main monitor answers it: the
 * movie's Program, a film's Source. A counter, so each press is one request whichever monitor is mounted. */
export const usePlaybackRequest = create<{ count: number; toggle: () => void }>(
  (set) => ({
    count: 0,
    toggle: () => set((s) => ({ count: s.count + 1 })),
  }),
);
