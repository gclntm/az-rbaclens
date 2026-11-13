import type { Todo, TodoStatus } from "@/api/client";

export const STATUS_LABEL: Record<TodoStatus, string> = {
  open: "Open",
  done: "Done",
  dismissed: "Dismissed",
};

/** "due in 5 days" · "3 days overdue" · "done 2 Nov" · "dismissed". */
export function dueWords(t: Pick<Todo, "status" | "due_at" | "done_at">, now: Date = new Date()): string {
  if (t.status === "done") {
    return t.done_at ? `done ${new Date(t.done_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "done";
  }
  if (t.status === "dismissed") return "dismissed";
  const days = Math.ceil((new Date(t.due_at).getTime() - now.getTime()) / 86_400_000);
  if (days < 0) return `${-days} ${days === -1 ? "day" : "days"} overdue`;
  if (days === 0) return "due today";
  return `due in ${days} ${days === 1 ? "day" : "days"}`;
}
