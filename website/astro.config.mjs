import { defineConfig } from "astro/config";
import { unified } from "@astrojs/markdown-remark";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import process from "node:process";
import { remarkDocLinks } from "./src/lib/remark-doc-links";
import { remarkCallouts } from "./src/lib/remark-callouts";
import { remarkMermaid } from "./src/lib/remark-mermaid";
import { rehypeClassNames } from "./src/lib/rehype-class-names";

const site = "https://neo-pragnya.github.io";
const base = process.env.SITE_BASE ?? "/";

// Inline styles are allowed only in the shape Shiki writes (colour custom properties and a few typography
// keywords). There are no parentheses, so there's no url() and no expression.
const SHIKI_STYLE = /^(?:\s*(?:--shiki-[a-z-]+|color|background-color|overflow-x|font-style|font-weight|text-decoration)\s*:\s*[#a-z0-9 .,%-]+;?)*\s*$/i;

// Markdown is sanitised. What survives:
// - heading ids keep their slugs (no "user-content-" prefix), so tables of contents work;
// - callouts keep their `callout callout-<type>` class;
// - Shiki syntax highlighting (class, data-language and the restricted inline style above);
// - Mermaid sources marked at build time by remark-mermaid (`pre.mermaid-src`).
const sanitizeSchema = {
  ...defaultSchema,
  clobberPrefix: "",
  attributes: {
    ...defaultSchema.attributes,
    blockquote: [...(defaultSchema.attributes?.blockquote ?? []), ["className", /^callout(-[a-z]+)?$/]],
    pre: [
      ...(defaultSchema.attributes?.pre ?? []),
      ["className", /^(astro-code|astro-code-themes|mermaid-src|github-(light|dark)|has-diff)$/],
      ["style", SHIKI_STYLE],
      "dataLanguage",
      "tabIndex",
    ],
    code: [...(defaultSchema.attributes?.code ?? []), ["className", /^language-[\w-]+$/]],
    span: [...(defaultSchema.attributes?.span ?? []), ["className", /^line$/], ["style", SHIKI_STYLE]],
  },
};

export default defineConfig({
  site,
  base,
  trailingSlash: "always",
  markdown: {
    // Light and dark code themes; global.css switches between them with the site theme.
    shikiConfig: { themes: { light: "github-light", dark: "github-dark" }, defaultColor: false },
    processor: unified({
      remarkPlugins: [remarkDocLinks, remarkCallouts, remarkMermaid],
      rehypePlugins: [rehypeClassNames, [rehypeSanitize, sanitizeSchema]],
    }),
  },
});
