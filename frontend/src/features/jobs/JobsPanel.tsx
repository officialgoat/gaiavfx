import { OperationList } from "../generation/WorkspaceUtilities";
export function JobsPanel({ blind = false }: { blind?: boolean }) {
  return (
    <div className="dock-scroll">
      <OperationList blind={blind} />
    </div>
  );
}
