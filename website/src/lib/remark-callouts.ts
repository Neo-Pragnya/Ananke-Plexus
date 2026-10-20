import type { Root } from "mdast";
import type { Plugin } from "unified";

// Callouts from two common dialects, rendered as <blockquote class="callout callout-<type>">:
//   GitHub alerts:     > [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION]
//   MkDocs admonitions: !!! warning "Optional title"   (followed by an indented block; straight or smart quotes)
interface Node {
  type: string;
  value?: string;
  lang?: string | null;
  children?: Node[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

const TYPES = ["note", "tip", "important", "warning", "caution", "danger", "info", "success", "example", "question"];

function mark(node: Node, type: string) {
  const kind = TYPES.includes(type) ? type : "note";
  node.data = { ...(node.data ?? {}), hProperties: { className: ["callout", `callout-${kind}`] } };
}

const text = (value: string): Node => ({ type: "text", value });
const strong = (value: string): Node => ({ type: "strong", children: [text(value)] });

export const remarkCallouts: Plugin<[], Root> = () => (tree) => {
  const walk = (parent: Node) => {
    const kids = parent.children;
    if (!kids) return;
    for (let i = 0; i < kids.length; i++) {
      const node = kids[i]!;
      // GitHub alert
      if (node.type === "blockquote") {
        const first = node.children?.[0];
        const lead = first?.type === "paragraph" ? first.children?.[0] : undefined;
        const m = lead?.type === "text" ? /^\[!([A-Za-z]+)\]\s*/.exec(lead.value ?? "") : null;
        if (m && lead) {
          const type = m[1]!.toLowerCase();
          lead.value = (lead.value ?? "").slice(m[0].length);
          first!.children!.unshift(strong(type.charAt(0).toUpperCase() + type.slice(1)), text(" "));
          mark(node, type);
        }
      }
      // MkDocs admonition: a paragraph whose first line is `!!! type "Title"`. The indented body is either a lazy
      // continuation of that same paragraph (no blank line, the usual case) or a separate indented code block.
      if (node.type === "paragraph" && node.children?.[0]?.type === "text") {
        const lead = node.children[0];
        const m = /^(?:!!!|\?\?\?\+?)\s+([a-z]+)(?:\s+["“”]([^"“”]*)["“”])?[ \t]*(?:\n|$)/.exec(lead.value ?? "");
        if (m) {
          const type = m[1]!;
          const title = m[2] ?? type.charAt(0).toUpperCase() + type.slice(1);
          lead.value = (lead.value ?? "").slice(m[0].length).replace(/^\s+/, "");
          const body: Node[] = [];
          const rest = node.children.filter((c) => !(c === lead && !c.value));
          if (rest.length) body.push({ type: "paragraph", children: rest });
          const next = kids[i + 1];
          const indented = next?.type === "code" && !next.lang;
          if (indented) {
            for (const para of (next.value ?? "").split(/\n{2,}/)) {
              body.push({ type: "paragraph", children: [text(para.replace(/\n/g, " "))] });
            }
          }
          const quote: Node = { type: "blockquote", children: [{ type: "paragraph", children: [strong(title)] }, ...body] };
          mark(quote, type);
          kids.splice(i, indented ? 2 : 1, quote);
          continue;
        }
      }
      walk(node);
    }
  };
  walk(tree as unknown as Node);
};
