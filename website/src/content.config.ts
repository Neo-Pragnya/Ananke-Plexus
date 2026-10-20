import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { z } from "astro/zod";

// The documentation is read straight from the repository's docs/ folder: one source of truth for GitHub and the site.
const docs = defineCollection({
  loader: glob({
    base: "../docs",
    pattern: "**/*.md",
    generateId: ({ entry }) => entry.replace(/\\/g, "/").replace(/\.md$/i, ""),
  }),
  schema: z.object({
    title: z.string().optional(),
    description: z.string().optional(),
  }),
});

export const collections = { docs };
