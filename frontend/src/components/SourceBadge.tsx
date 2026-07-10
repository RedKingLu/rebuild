// SourceBadge (R15-4-C11, used by C6/C7 community/knowledge cards).
export type SourceKind = "official" | "community" | "local" | "user" | "internal";

const LABELS: Record<SourceKind, string> = {
  official: "官方",
  community: "社区",
  local: "本地",
  user: "用户",
  internal: "内部",
};

export function SourceBadge({ source, verified }: { source?: string; verified?: boolean }) {
  const kind: SourceKind = (["official", "community", "local", "user", "internal"].includes(source || "")
    ? source!
    : "community") as SourceKind;
  const cls = "badge badge-" + (kind === "official" ? "official" : kind === "community" ? "community" : "user");
  const label = verified ? `${LABELS[kind]} ✓` : LABELS[kind];
  return <span className={cls}>{label}</span>;
}
