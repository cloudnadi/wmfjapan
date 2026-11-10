import { useEffect, useRef } from "react";
import { t } from "./i18n";

const TOOLS = [
  { cmd: "bold", label: "events.rte.bold", glyph: "B" },
  { cmd: "italic", label: "events.rte.italic", glyph: "I" },
  { cmd: "formatBlock:h3", label: "events.rte.heading", glyph: "H" },
  { cmd: "insertUnorderedList", label: "events.rte.bullets", glyph: "•" },
  { cmd: "insertOrderedList", label: "events.rte.numbers", glyph: "1." },
  { cmd: "createLink", label: "events.rte.link", glyph: "🔗" },
  { cmd: "removeFormat", label: "events.rte.clear", glyph: "✕" },
];

/**
 * Small contentEditable rich-text field. The server re-sanitises the HTML with
 * an allow-list, so this only needs to be convenient, not a security boundary.
 */
export default function RichTextEditor({ id, label, value, onChange }) {
  const ref = useRef(null);

  // Sync from props only when the field is not being edited (e.g. after load).
  useEffect(() => {
    const el = ref.current;
    if (el && document.activeElement !== el && el.innerHTML !== (value || "")) {
      el.innerHTML = value || "";
    }
  }, [value]);

  const run = (cmd) => {
    ref.current?.focus();
    if (cmd === "createLink") {
      const url = window.prompt(t("events.rte.linkPrompt"), "https://");
      if (url && /^(https?:\/\/|mailto:)/i.test(url)) document.execCommand("createLink", false, url);
    } else if (cmd.startsWith("formatBlock:")) {
      document.execCommand("formatBlock", false, cmd.split(":")[1]);
    } else {
      document.execCommand(cmd, false, null);
    }
    onChange(ref.current.innerHTML);
  };

  return (
    <div className="field ep-rte">
      <label id={`${id}-label`} htmlFor={id}>
        {label}
      </label>
      <div className="ep-rte-toolbar" role="toolbar" aria-label={t("events.rte.toolbar")}>
        {TOOLS.map((tool) => (
          <button
            key={tool.cmd}
            type="button"
            className="button secondary"
            title={t(tool.label)}
            aria-label={t(tool.label)}
            onClick={() => run(tool.cmd)}
          >
            {tool.glyph}
          </button>
        ))}
      </div>
      <div
        id={id}
        ref={ref}
        className="ep-rte-body"
        contentEditable
        role="textbox"
        aria-multiline="true"
        aria-labelledby={`${id}-label`}
        suppressContentEditableWarning
        onInput={(e) => onChange(e.currentTarget.innerHTML)}
        onPaste={(e) => {
          // Paste as plain text so foreign styling/markup never enters the field.
          e.preventDefault();
          document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
        }}
      />
    </div>
  );
}
