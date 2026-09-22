/**
 * The catalogue's types.
 *
 * A namespace is one file under `messages/` exporting `defineMessages({en, tr})`.
 * `en` is authored freely; `tr` is typed as the SHAPE of `en` with every leaf
 * a string, so a key present in English and absent in Turkish does not
 * compile. That is the whole completeness guarantee, and it is why the two
 * languages live side by side in one file: the translator sees the source
 * string above the target string, and a reviewer sees both in one diff.
 */

export type MessageTree = { readonly [key: string]: string | MessageTree };

export type Shape<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : T[K] extends MessageTree ? Shape<T[K]> : never;
};

export interface Namespace<E extends MessageTree> {
  readonly en: E;
  readonly tr: Shape<E>;
}

export function defineMessages<const E extends MessageTree>(messages: { en: E; tr: Shape<E> }): Namespace<E> {
  return messages;
}

/** `"a.b.c"` for every string leaf of a tree. */
export type Paths<T> = {
  [K in keyof T & string]: T[K] extends string ? K : T[K] extends MessageTree ? `${K}.${Paths<T[K]>}` : never;
}[keyof T & string];

export function lookup(tree: MessageTree, path: string): string | undefined {
  let node: string | MessageTree | undefined = tree;
  for (const part of path.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = node[part];
  }
  return typeof node === "string" ? node : undefined;
}
