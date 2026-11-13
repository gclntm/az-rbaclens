import NavIcon from "@/components/NavIcon";
import { useTheme } from "@/theme/ThemeContext";

const LABELS: Record<string, string> = { system: "Auto", light: "Light", dark: "Dark" };
const ICONS = { system: "auto", light: "sun", dark: "moon" } as const;

/** Small icon button cycling Auto → Light → Dark; the label is in the tooltip. 44px touch target. */
export function ThemeToggle() {
  const { preference, toggle } = useTheme();
  const label = `Theme: ${LABELS[preference]} (click to change)`;
  return (
    <button
      type="button"
      className="flex size-11 flex-none cursor-pointer items-center justify-center rounded-lg text-fg-quaternary outline-focus-ring hover:bg-primary_hover hover:text-fg-quaternary_hover focus-visible:outline-2 focus-visible:outline-offset-2"
      onClick={toggle}
      aria-label={label}
      title={label}
    >
      <NavIcon name={ICONS[preference as keyof typeof ICONS] ?? "auto"} size={20} />
    </button>
  );
}
