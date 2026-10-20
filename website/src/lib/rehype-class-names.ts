import type { Root } from "hast";
import type { Plugin } from "unified";

// Astro's Shiki step writes `properties.class` (a string) instead of hast's `className` (an array).
// rehype-sanitize only knows `className`, so without this step highlighted code loses its classes.
interface El {
  type: string;
  properties?: Record<string, unknown>;
  children?: El[];
}

export const rehypeClassNames: Plugin<[], Root> = () => (tree) => {
  const walk = (node: El) => {
    const props = node.properties;
    if (props && typeof props.class === "string") {
      const extra = props.class.split(/\s+/).filter(Boolean);
      const current = Array.isArray(props.className) ? (props.className as string[]) : [];
      props.className = [...current, ...extra];
      delete props.class;
    }
    node.children?.forEach(walk);
  };
  walk(tree as unknown as El);
};
