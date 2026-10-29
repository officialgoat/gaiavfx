import { useEffect, useState } from "react";
import { usePreferences } from "./preferences";

function useBreakpoint(query: string) {
  const [matches, setMatches] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const media = matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}

// Narrow windows start with an unobstructed viewer. Opening a drawer is temporary;
// returning to a wide window restores the user's persisted docked-panel choices.
export function usePanels() {
  const preferences = usePreferences();
  const narrow = useBreakpoint("(max-width: 1100px)");
  const compact = useBreakpoint("(max-width: 780px)");
  const [drawer, setDrawer] = useState<"media" | "inspector" | null>(null);
  useEffect(() => {
    setDrawer(null);
  }, [narrow, compact]);
  const closeDrawer = () => setDrawer(null);
  return {
    showMedia: narrow ? drawer === "media" : preferences.showMedia,
    showInspector: compact ? drawer === "inspector" : preferences.showInspector,
    overlay: drawer !== null && narrow,
    drawer: narrow ? drawer : null,
    closeDrawer,
    toggleMedia: () =>
      narrow
        ? setDrawer((value) => (value === "media" ? null : "media"))
        : preferences.toggleMedia(),
    toggleInspector: () =>
      compact
        ? setDrawer((value) => (value === "inspector" ? null : "inspector"))
        : preferences.toggleInspector(),
  };
}
