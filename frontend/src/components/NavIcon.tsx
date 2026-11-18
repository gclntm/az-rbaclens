import {
  Activity,
  AlertTriangle,
  Award01,
  Bell01,
  BookOpen01,
  Calendar,
  CheckSquare,
  ClipboardCheck,
  ClockRewind,
  Compass03,
  Contrast01,
  Database01,
  FileCheck02,
  HelpCircle,
  Home02,
  LayersThree01,
  Lightbulb02,
  Lock01,
  Moon01,
  Repeat04,
  Rocket02,
  Settings01,
  Shield01,
  Sun,
  Terminal,
  UserCheck01,
  Users01,
} from "@untitledui/icons";

/**
 * The app's icons, by name, drawn from Untitled UI Icons (@untitledui/icons)
 * (docs/design.md). Stroked with currentColor so they
 * follow the text colour in both themes. Decorative only (the label carries the
 * meaning), hence aria-hidden.
 */
export type NavIconName =
  | "home"
  | "reviews"
  | "todo"
  | "report"
  | "look"
  | "changes"
  | "places"
  | "people"
  | "roles"
  | "data"
  | "settings"
  | "shield"
  | "calendar"
  | "history"
  | "sun"
  | "moon"
  | "auto"
  | "help"
  | "rocket"
  | "idea"
  | "lock"
  | "repeat"
  | "self"
  | "award"
  | "compass"
  | "bell"
  | "terminal";

type IconComponent = typeof Home02;

/** The icon components themselves, for Untitled UI parts that take an icon (nav items). */
export const NAV_ICONS: Record<NavIconName, IconComponent> = {
  home: Home02,
  reviews: ClipboardCheck,
  todo: CheckSquare,
  report: FileCheck02,
  look: AlertTriangle,
  changes: Activity,
  places: LayersThree01,
  people: Users01,
  roles: BookOpen01,
  data: Database01,
  settings: Settings01,
  shield: Shield01,
  calendar: Calendar,
  history: ClockRewind,
  sun: Sun,
  moon: Moon01,
  auto: Contrast01,
  help: HelpCircle,
  rocket: Rocket02,
  idea: Lightbulb02,
  lock: Lock01,
  repeat: Repeat04,
  self: UserCheck01,
  award: Award01,
  compass: Compass03,
  bell: Bell01,
  terminal: Terminal,
};

export default function NavIcon({ name, size = 18 }: { name: NavIconName; size?: number }) {
  const Icon = NAV_ICONS[name];
  return <Icon className="nav-icon" size={size} strokeWidth={1.75} aria-hidden="true" focusable="false" />;
}
