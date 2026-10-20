import type { Root } from "mdast";
import type { Plugin } from "unified";

// ```mermaid fences become <pre class="mermaid-src">source</pre> at build time. Shiki then leaves them alone and
// the client renderer (components/Mermaid.astro) finds exactly these blocks. It doesn't guess from the text, so
// a ```markdown example that merely *contains* a Mermaid fence stays a code sample.
interface Node {
  type: string;
  value?: string;
  lang?: string | null;
  children?: Node[];
  data?: { hName?: string; hProperties?: Record<string, unknown>; hChildren?: unknown[] };
}

export const remarkMermaid: Plugin<[], Root> = () => (tree) => {
  const walk = (node: Node) => {
    if (node.type === "code" && node.lang?.trim().toLowerCase() === "mermaid") {
      // A custom node type so no code handler (or highlighter) touches it; mdast-util-to-hast builds it from data.
      node.type = "mermaid";
      node.data = {
        hName: "pre",
        hProperties: { className: ["mermaid-src"] },
        hChildren: [{ type: "text", value: node.value ?? "" }],
      };
      return;
    }
    node.children?.forEach(walk);
  };
  walk(tree as unknown as Node);
};

/** True when a Markdown body has at least one Mermaid fence (pages include the renderer only then). */
export const hasMermaid = (body: string | undefined) => /^\s*(```|~~~)\s*mermaid\b/im.test(body ?? "");
