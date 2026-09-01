/**
 * dsh-client-ui-autoknit — node half (ESM, DSH-official shape).
 *
 * Intentional no-op: `apply()` / `inject()` never access the DSH layout
 * object (per the boundary contract) and never register panels on the
 * backend side — the details-slot registration is done entirely by the
 * browser half (`lib/client.js` → bundled `dist/client.js`) via
 * `window.__ModuleLoader__.load({ id, factory })`.
 *
 * Why ESM (.mjs): DSH's cordis Loader loads the node half through the Node
 * ESM loader (`internal.import`). The official client plugins (e.g.
 * `@deepseek-ai/dsh-client-ui-deliverables`) are ESM and export `apply` /
 * `inject` at the top level. A CommonJS `module.exports` would arrive as
 * `{ default: {...} }` and can be misread, surfacing as
 * `invalid plugin ... received undefined` at boot.
 *
 * Keeping the node half empty also makes the plugin safe to load in any host
 * context (CLI / server / tests).
 */
export function apply() {
  // intentionally empty — do not touch the DSH layout here
}

export function inject() {
  // intentionally empty
}
