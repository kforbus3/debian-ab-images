// Where a picked folder's files land in the image.
//
// Extracted from the component and kept free of React so it can be tested: this
// is the one place in the app where paths chosen in a browser are turned into
// destinations for a write endpoint, and a mis-join here would put files
// somewhere nobody asked for. The server refuses anything that escapes
// overlay.d regardless (orchestrator.overlay_resolve), so this is about being
// correct rather than about being the last line of defence.

// A browser hands over every file beneath the chosen folder, so a mis-picked
// home directory is a real hazard rather than a theoretical one.
export const MAX_DIR_FILES = 500;
export const MAX_DIR_BYTES = 256 * 1024 * 1024;

// Editor and OS droppings nobody means to ship. Skipped, but counted and shown
// -- a file quietly missing from an image is found months later by whoever
// wonders why a config never applied.
const DIR_SKIP = /(^|\/)(\.DS_Store|Thumbs\.db|\.gitkeep)$|(^|\/)\.git(\/|$)/;

export type DirItem = { file: File; path: string };
export type DirPlan = { send: DirItem[]; skipped: string[]; bytes: number };

/** Join a destination prefix and a relative path into one absolute image path. */
export function joinImagePath(prefix: string, rel: string): string {
  const p = (prefix || "/").trim().replace(/\\/g, "/");
  const joined = `${p}/${rel}`.replace(/\/+/g, "/");
  return joined.startsWith("/") ? joined : `/${joined}`;
}

/** What a picked directory maps to, so the UI can show it before uploading. */
export function planDirUpload(
  files: File[], prefix: string, includeTop: boolean,
): DirPlan {
  const send: DirItem[] = [];
  const skipped: string[] = [];
  let bytes = 0;
  for (const f of files) {
    // webkitRelativePath is "<chosen folder>/a/b.txt"; fall back to the bare
    // name if a browser does not provide it.
    const relFull = (f as unknown as { webkitRelativePath?: string }).webkitRelativePath || f.name;
    if (DIR_SKIP.test(relFull)) { skipped.push(relFull); continue; }
    const rel = includeTop ? relFull : relFull.split("/").slice(1).join("/");
    if (!rel) { skipped.push(relFull); continue; }
    send.push({ file: f, path: joinImagePath(prefix, rel) });
    bytes += f.size;
  }
  return { send, skipped, bytes };
}
