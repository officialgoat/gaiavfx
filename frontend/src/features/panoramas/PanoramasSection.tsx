import { useQuery } from "@tanstack/react-query";
import { useNavigator } from "../../app/navigator";
import { panoramas, unsettled } from "./client";
import { panoramaTitle, stateWords } from "./PanoramaReview";
import "./panoramas.css";

/** Library › Stages' panorama attempts: each shows its two candidates (or what it is drawing) and opens for review. */
export function PanoramasSection() {
  const nav = useNavigator();
  const list = useQuery({
    queryKey: ["panoramas", "list"],
    queryFn: () => panoramas.list(),
    refetchInterval: (q) => (q.state.data?.some(unsettled) ? 3000 : false),
  });
  if (list.isPending || list.error || !list.data?.length) return null;
  return (
    <section className="panorama-section" aria-labelledby="panoramas-title">
      <h3 id="panoramas-title">Panoramas</h3>
      <ul className="panorama-rows">
        {list.data.map((panorama) => (
          <li key={panorama.id}>
            <button
              className="panorama-row"
              onClick={() => nav.openPanoramas(panorama.id)}
            >
              <span className="panorama-row-media">
                {panorama.candidates.length ? (
                  panorama.candidates.map((candidate) => (
                    <span
                      key={candidate.candidate_id}
                      className="panorama-row-thumb"
                      data-chosen={candidate.chosen || undefined}
                    >
                      <img
                        src={candidate.image.thumbnail}
                        alt=""
                        loading="lazy"
                      />
                      <span className="panorama-letter">
                        {candidate.letter}
                      </span>
                    </span>
                  ))
                ) : (
                  <span
                    className="panorama-row-thumb panorama-pending"
                    aria-hidden
                  >
                    <span className="panorama-pending-band" />
                  </span>
                )}
              </span>
              <span className="panorama-row-text">
                <span className="panorama-row-name">
                  {panoramaTitle(panorama)}
                </span>
                <span className="muted small">{panorama.message}</span>
              </span>
              <span className="set-chip" data-panorama={panorama.state}>
                {stateWords(panorama)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
