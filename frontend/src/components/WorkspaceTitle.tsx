/** A shared overview heading; module actions remain beside it in the existing page header. */
export function WorkspaceTitle({ title, area, description }: { title: string; area: string; description?: string }) {
  return <div className="workspace-title spacer"><p className="workspace-area">{area}</p><h1>{title}</h1>{description && <p className="workspace-description">{description}</p>}</div>;
}
