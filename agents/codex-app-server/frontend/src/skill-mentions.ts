import type { Obj } from "./native";

export type SkillReference = {
  type: "skill";
  name: string;
  path: string;
  label: string;
  description: string;
};

export function skillReferences(result: Obj): SkillReference[] {
  const rows = (result.data || []).flatMap((entry: Obj) => entry.skills || []);
  return [...new Map<string, SkillReference>(rows
    .filter((row: Obj) => row.enabled && typeof row.name === "string" && typeof row.path === "string")
    .map((row: Obj) => [row.path, {
      type: "skill", name: row.name, path: row.path,
      label: row.interface?.displayName || row.name,
      description: row.interface?.shortDescription || row.shortDescription || row.description || "",
    }] as const)).values()];
}

// Resolve only the token under the caret. Shell expansions and escaped dollars
// remain ordinary text; choosing a result explicitly binds its SKILL.md path.
export function skillMentionAt(text: string, start: number, end = start) {
  if (start !== end || start < 0 || start > text.length) return null;
  const match = /(?:^|[\s([{（【，。；：、!?！？])\$([\p{L}\p{N}_:.-]*)$/u.exec(text.slice(0, start));
  if (!match) return null;
  const from = start - match[1].length - 1;
  const suffix = /^[\p{L}\p{N}_:.-]*/u.exec(text.slice(start))![0];
  const to = start + suffix.length;
  // Do not complete a path or a shell/math expression as a skill.
  if (/^[/$\\]/.test(text.slice(to))) return null;
  return { start: from, end: to, query: match[1] };
}

export function filterSkills(rows: SkillReference[], query: string) {
  const term = query.toLocaleLowerCase();
  return rows.filter((row) => `${row.name} ${row.label} ${row.description}`.toLocaleLowerCase().includes(term));
}
