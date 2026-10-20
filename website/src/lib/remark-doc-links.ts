import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Root } from "mdast";
import type { Plugin } from "unified";

// Rewrites relative links between Markdown files in the docs folder (e.g. `../guides/release.md#3-what`) to site
// routes (`/docs/guides/release/#3-what`), so the same Markdown works on GitHub and on the site.
const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../docs");
const siteBase = process.env.SITE_BASE ?? "/";
const basePrefix = siteBase === "/" ? "" : `/${siteBase.replace(/^\/+|\/+$/g, "")}`;

interface LinkNode {
  type: string;
  url?: string;
  children?: unknown[];
}

export function docRoute(filePath: string): string | undefined {
  const relative = path.relative(docsRoot, filePath);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) return undefined;
  if (!relative.toLowerCase().endsWith(".md")) return undefined;
  const route = relative.replace(/\\/g, "/").replace(/(^|\/)index\.md$/i, "$1").replace(/\.md$/i, "");
  return `/docs/${route}${route && !route.endsWith("/") ? "/" : ""}`;
}

export const remarkDocLinks: Plugin<[], Root> = () => (tree, file) => {
  const source = file.history[0];
  if (!source || !path.isAbsolute(source) || !docRoute(source)) return;

  const walk = (node: LinkNode) => {
    if (!Array.isArray(node.children)) return;
    for (const child of node.children as LinkNode[]) {
      if (
        (child.type === "link" || child.type === "definition") &&
        typeof child.url === "string" &&
        !/^(?:[a-z][a-z\d+.-]*:|\/|#)/i.test(child.url)
      ) {
        const [pathname, ...fragment] = child.url.split("#");
        const route = docRoute(path.resolve(path.dirname(source), pathname ?? ""));
        if (route) child.url = `${basePrefix}${route}${fragment.length ? `#${fragment.join("#")}` : ""}`;
      }
      walk(child);
    }
  };
  walk(tree as unknown as LinkNode);
};
