import { useEffect, useId, useState } from "react";
import { useOpenDocument } from "../../app/open-document";
import { useQuery } from "@tanstack/react-query";
import { ContactSheet } from "../prepare/ContactSheet";
import { peopleReport } from "../prepare/people";
import { shotPoster } from "../prepare/shot-poster";
import { production } from "../review/client";
import { useEditor } from "../timeline/editor-state";
import type { ChatController } from "./use-chat";

export async function readPeople(id: string) {
  const operation = await production.operation(id);
  if (operation.kind !== "person_analysis") return null;
  const source = String(operation.request.source_asset ?? "");
  const rows = await production.people(source);
  const report = peopleReport(
    rows.find((row) => row.operation_id === id)?.report ?? null,
  );
  return report ? { ...report, source } : null;
}

/** Resolve both immediate analysis receipts and ask_human references through the public API. */
export function PeopleReceipt({
  id,
  chat,
}: {
  id: string;
  chat: ChatController;
}) {
  const owner = useId();
  const [selected, setSelected] = useState<number[]>([]);
  const disabled = chat.busy || chat.running || chat.readOnly;
  const report = useQuery({
    queryKey: ["people-report", id],
    queryFn: () => readPeople(id),
  });
  const data = report.data;
  const poster = useQuery({
    queryKey: ["shot-poster", data?.source, data?.frame, data?.fps],
    queryFn: () => shotPoster(data!.source, data!.frame / data!.fps),
    enabled: !!data,
    staleTime: Infinity,
  });
  useEffect(
    () => () => {
      const editor = useEditor.getState();
      editor.highlightSource(owner, "", undefined, "focus");
      editor.highlightSource(owner, "", undefined, "hover");
    },
    [owner],
  );
  if (report.isError || poster.isError)
    return (
      <p role="alert">
        {report.error?.message ?? poster.error?.message}
        <button
          onClick={() => {
            void report.refetch();
            void poster.refetch();
          }}
        >
          Retry contact sheet
        </button>
      </p>
    );
  if (!data)
    return (
      <p>
        {report.isPending ? "Loading people…" : "People analysis is not ready."}
      </p>
    );
  return (
    <>
      <ContactSheet
        label={`People · frames ${data.frame}–${data.end - 1}`}
        tracks={data.tracks}
        objects={data.objects}
        native={data.native}
        posterUrl={poster.data}
        hint="Choose up to two people by number, then send your answer. Point to or focus a person to locate them in Source."
        selected={selected}
        disabled={disabled}
        onSelect={(index) =>
          setSelected((old) =>
            old.includes(index)
              ? old.filter((value) => value !== index)
              : old.length < 2
                ? [...old, index]
                : old,
          )
        }
        onHighlight={(highlight, interaction) =>
          // With a film open the movie's Source monitor is not on screen; leave the movie's editor alone.
          useOpenDocument.getState().document.kind === "movie" &&
          useEditor
            .getState()
            .highlightSource(
              owner,
              data.source,
              highlight
                ? { url: poster.data, native: data.native, highlight }
                : undefined,
              interaction,
            )
        }
      />
      <button
        type="button"
        disabled={disabled || !selected.length}
        onClick={async () => {
          const answer = [...selected]
            .sort((a, b) => a - b)
            .map((index, i) => `${i ? "person" : "Person"} ${index}`)
            .join(" and ");
          if (await chat.send(answer, [])) setSelected([]);
        }}
      >
        Send {selected.length === 2 ? "pair" : "person"} answer
      </button>
    </>
  );
}
