import { useEffect, useState } from "react";

const STORAGE_KEY = "wmf-theme";

export function getInitialTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") {
      return stored;
    }
  } catch (err) {
    // localStorage unavailable — fall through
  }
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "light" ? "light" : "dark";
}

/**
 * Theme hook aligned with beta.wmfjapan.com — dark by default, persisted
 * to localStorage, and reflected on <html data-theme>. Multiple mounts stay in
 * sync via a window event so any navbar toggle updates every other navbar.
 */
export function useTheme() {
  const [theme, setThemeState] = useState(getInitialTheme);

  useEffect(() => {
    const handleChange = (event) => {
      const next = event.detail;
      if (next === "light" || next === "dark") {
        setThemeState(next);
      }
    };
    window.addEventListener("wmf-theme-change", handleChange);
    return () => window.removeEventListener("wmf-theme-change", handleChange);
  }, []);

  const setTheme = (next) => {
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (err) {
      // ignore persistence failures
    }
    setThemeState(next);
    window.dispatchEvent(new CustomEvent("wmf-theme-change", { detail: next }));
  };

  const toggleTheme = () => setTheme(theme === "dark" ? "light" : "dark");

  return { theme, toggleTheme, setTheme };
}
