import { copyButtonClass } from "@/components/chips";
import { CAPTION, cx } from "@/components/classes";
import { useToast } from "@/components/Toast";

/** Ready-to-run commands with a copy button. RBAC Lens never runs them. */
export default function Commands({ commands, label = "Commands" }: { commands: string[]; label?: string }) {
  const { showToast } = useToast();
  if (commands.length === 0) return null;
  const text = commands.join("\n");
  function copy() {
    navigator.clipboard
      .writeText(text)
      .then(() => showToast(commands.length === 1 ? "Command copied" : `${commands.length} commands copied`, "success"))
      .catch(() => showToast("Could not copy", "error"));
  }
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between gap-2">
        <span className={cx(CAPTION, "text-quaternary")}>{label}</span>
        <button type="button" className={copyButtonClass()} onClick={copy}>
          Copy
        </button>
      </div>
      <pre
        aria-label={label}
        className="mx-0 mt-1 mb-0 max-h-[240px] overflow-auto rounded-[4px] border border-secondary bg-(--bg-input) px-4 py-2 text-[0.75rem] whitespace-pre-wrap [overflow-wrap:anywhere]"
      >{text}</pre>
    </div>
  );
}
