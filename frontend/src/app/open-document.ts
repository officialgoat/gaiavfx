import { create } from "zustand";

/** Which document the track workspace shows (ADR 0017): a movie, or a film's recast. */
export type OpenDocument =
  { kind: "movie" } | { kind: "film"; recastId: string };

const KEY = "gaiavfx:document";

function saved(): OpenDocument {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return value?.kind === "film" &&
      typeof value.recastId === "string" &&
      value.recastId
      ? { kind: "film", recastId: value.recastId }
      : { kind: "movie" };
  } catch {
    return { kind: "movie" };
  }
}

function remember(document: OpenDocument) {
  try {
    localStorage.setItem(KEY, JSON.stringify(document));
  } catch {
    /* Without storage the workspace reopens on the movie. */
  }
}

export const useOpenDocument = create<{
  document: OpenDocument;
  openFilm: (recastId: string) => void;
  openMovie: () => void;
}>((set) => ({
  document: saved(),
  openFilm: (recastId) => {
    const document: OpenDocument = { kind: "film", recastId };
    remember(document);
    set({ document });
  },
  openMovie: () => {
    const document: OpenDocument = { kind: "movie" };
    remember(document);
    set({ document });
  },
}));
