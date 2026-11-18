import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type Preference = "light" | "dark" | "system";
type Theme = "light" | "dark";

type ThemeContextValue = {
  preference: Preference;
  theme: Theme;
  setMode: (mode: Preference) => void;
  toggle: () => void;
};

const STORAGE_KEY = "azure-dash-theme";
const ThemeContext = createContext<ThemeContextValue | null>(null);

function readPreference(): Preference {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
}

function resolveTheme(pref: Preference): Theme {
  if (pref !== "system") return pref;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<Preference>(readPreference);
  const [theme, setTheme] = useState<Theme>(() => resolveTheme(preference));

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, preference);
    setTheme(resolveTheme(preference));
  }, [preference]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    // Untitled UI's components switch on this class (docs/design.md).
    document.documentElement.classList.toggle("dark-mode", theme === "dark");
  }, [theme]);

  useEffect(() => {
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => setTheme(media.matches ? "dark" : "light");
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, [preference]);

  const setMode = useCallback((mode: Preference) => setPreference(mode), []);
  const toggle = useCallback(() => {
    setPreference((p) => (p === "system" ? "light" : p === "light" ? "dark" : "system"));
  }, []);

  const value = useMemo(
    () => ({ preference, theme, setMode, toggle }),
    [preference, theme, setMode, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
