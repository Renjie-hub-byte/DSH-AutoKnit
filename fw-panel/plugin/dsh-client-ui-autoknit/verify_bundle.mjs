#!/usr/bin/env node
/**
 * dsh-client-ui-autoknit — bundle self-check (verify_bundle).
 *
 * Runs every acceptance-oriented check for this plugin package and prints a
 * per-group PASS/FAIL summary. Exits 0 only when ALL checks pass.
 *
 * Coverage:
 *   1. node-half structure  (empty apply/inject, source has no `ctx.layout`)
 *   2. render payload       (run_id/stage/module_states/consumption)
 *   3. archive / switch logic
 *   4. data-bridge HTTP client (list / detail / archive / archived)
 *   5. browser registration (details slot via __ModuleLoader__.load)
 *   6. package.json dsh.client declaration
 *
 * Usage:
 *   node verify_bundle.mjs
 *   node verify_bundle.mjs --quiet     # only the final verdict line
 */
import { runAllChecks } from './test/helpers.mjs';

const quiet = process.argv.includes('--quiet');

const results = await runAllChecks();

let failed = 0;
for (const r of results) {
  if (r.ok) {
    if (!quiet) console.log(`PASS  ${r.name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${r.name}`);
    for (const e of r.errors) console.log(`      - ${e}`);
  }
}

console.log(`\n${results.length - failed}/${results.length} checks passed.`);
if (failed > 0) {
  console.log('verify_bundle: NOT ALL PASS');
  process.exit(1);
} else {
  console.log('verify_bundle: ALL PASS');
  process.exit(0);
}
