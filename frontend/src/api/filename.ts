/** The file name a server suggests in Content-Disposition (``filename*=UTF-8''…`` wins over ``filename="…"``). */
export function filenameFrom(header: string | null): string | null {
  if (!header) return null;
  const star = header.match(/filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/);
  if (star) {
    try {
      return safeName(decodeURIComponent(star[1].trim()));
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = header.match(/filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)/);
  const name = plain ? (plain[1] ?? plain[2])?.trim() : null;
  return name ? safeName(name) : null;
}

/** No folders, no control characters: the browser saves into its download folder only. */
function safeName(name: string): string | null {
  const base = name.split(/[\\/]/).pop() ?? "";
  // eslint-disable-next-line no-control-regex
  const clean = base.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return clean && clean !== "." && clean !== ".." ? clean : null;
}
