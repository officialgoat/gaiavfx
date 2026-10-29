import { Film } from "lucide-react";
import { Dock, PanelTabs } from "./Dock";
import { CommandButton } from "./Menus";
import { usePreferences, workspaceDock } from "../../shared/preferences";
import type { DirectController } from "../project/use-direction";
import type { ChatController } from "../chat/use-chat";
import type { Operation } from "../generation/client";
import { directPreviews } from "../project/client";
import { ProjectPanel } from "../project/ProjectPanel";
import { MovieTimeline } from "../timeline/movie-doc";
import { PropertiesPanel } from "../properties/PropertiesPanel";
import { ChatPanel } from "../chat/ChatPanel";
import { JobsPanel } from "../jobs/JobsPanel";
import { Monitors } from "../monitors/Monitors";
import { PreparePanel } from "../prepare/PreparePanel";
import { SynchronizedMonitors } from "../review/SynchronizedMonitors";
import { ReviewInspector } from "../review/ReviewInspector";
import type { ReviewController } from "../review/use-review";

export function DirectWorkspace({
  workspace,
  d,
  chat,
  operations,
  review,
}: {
  workspace: string;
  d: DirectController;
  chat: ChatController;
  operations: Operation[];
  review: ReviewController;
}) {
  const preferences = usePreferences();
  const layout = preferences.layouts[workspace] ?? workspaceDock(workspace);
  const setLayout = (patch: Parameters<typeof preferences.setLayout>[1]) =>
    preferences.setLayout(workspace, patch);
  return (
    <Dock
      workspace={workspace}
      project={
        <>
          <PanelTabs
            name="project"
            tabs={["Project", "Library"]}
            active={layout.leftTab === "library" ? "Library" : "Project"}
            onChange={(tab) =>
              setLayout({
                leftTab: tab.toLowerCase() as "project" | "library",
              })
            }
          />
          <div
            id="project-view"
            role="tabpanel"
            aria-labelledby={`project-${layout.leftTab === "library" ? "Library" : "Project"}-tab`}
            className="dock-tab-body"
          >
            {layout.leftTab === "library" ? (
              <div className="project-library-link">
                <p>Browse and organize your complete media library.</p>
                <CommandButton id="library" className="secondary-button">
                  Open Library
                </CommandButton>
              </div>
            ) : (
              <ProjectPanel
                d={d}
                hiddenAssets={
                  workspace === "review" && !review.revealed
                    ? new Set([
                        ...operations.flatMap((o) =>
                          o.candidates.map((c) => c.asset_id),
                        ),
                        ...(review.value?.members.map((m) => m.asset_id) ?? []),
                      ])
                    : undefined
                }
              />
            )}
          </div>
        </>
      }
      monitors={
        workspace === "prepare" ? (
          <PreparePanel d={d} r={review} operations={operations} />
        ) : workspace === "review" ? (
          <SynchronizedMonitors key={review.id} r={review} />
        ) : (
          <Monitors
            d={d}
            candidates={directPreviews(
              operations,
              d.getSegment()?.cuts.map((c) => c.id) ?? [],
              d.getSegment()?.id,
            )}
          />
        )
      }
      timeline={
        d.movie.data ? (
          <>
            {["prepare", "review"].includes(workspace) && (
              <>
                {review.error && (
                  <div className="studio-error" role="alert">
                    {review.error}
                    <button onClick={() => void review.refresh()}>
                      Refresh review
                    </button>
                  </div>
                )}
                {review.notice && (
                  <p className="review-notice" role="status">
                    {review.notice}
                  </p>
                )}
              </>
            )}
            <MovieTimeline d={d} />
          </>
        ) : (
          <div className="direct-empty">
            <Film size={28} strokeWidth={1.25} />
            <h1>Start directing.</h1>
            <p>Create a movie, then shape its segments, phases and cuts.</p>
            <CommandButton id="movie.new" className="primary-button">
              New movie
            </CommandButton>
            {!!d.movies.data?.length && (
              <CommandButton id="movie.open" className="secondary-button" />
            )}
            <CommandButton id="film.open" className="secondary-button" />
          </div>
        )
      }
      inspector={
        <>
          <PanelTabs
            name="inspector"
            tabs={["Chat", "Properties", "Jobs"]}
            active={layout.rightTab[0].toUpperCase() + layout.rightTab.slice(1)}
            onChange={(tab) =>
              setLayout({
                rightTab: tab.toLowerCase() as "chat" | "properties" | "jobs",
              })
            }
          />
          <div
            id="inspector-view"
            role="tabpanel"
            aria-labelledby={`inspector-${layout.rightTab[0].toUpperCase() + layout.rightTab.slice(1)}-tab`}
            className="dock-tab-body"
          >
            {layout.rightTab === "chat" ? (
              <ChatPanel
                key={d.editor.movieId}
                chat={chat}
                d={d}
                compact={layout.mode === "full"}
              />
            ) : layout.rightTab === "properties" ? (
              workspace === "review" ? (
                <ReviewInspector r={review} />
              ) : (
                <PropertiesPanel d={d} />
              )
            ) : (
              <JobsPanel />
            )}
          </div>
        </>
      }
    />
  );
}
