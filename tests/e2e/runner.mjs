#!/usr/bin/env node
/**
 * Opaque-Box E2E Test Runner for Maunting Pontis
 * Executes all 4 tiers of test suites and reports individual/aggregate results.
 */
import { suites } from './harness.mjs';

// Load all test suite definitions
import './r1_lesemodus.test.mjs';
import './r2_search.test.mjs';
import './r3_ai_followup.test.mjs';
import './r4_workspaces.test.mjs';
import './r5_dns_rebinding.test.mjs';
import './r6_webextension_cdp.test.mjs';
import './tier3_cross_feature.test.mjs';
import './tier4_real_world.test.mjs';

async function runRunner() {
  console.log('======================================================================');
  console.log('   MAUNTING PONTIS — OPAQUE-BOX E2E TEST RUNNER');
  console.log('   4-Tier Test Framework (R1-R6, Boundaries, Cross-Feature, Scenarios)');
  console.log('======================================================================\n');

  const startTime = performance.now();
  let totalTests = 0;
  let passedTests = 0;
  let failedTests = 0;

  const tierStats = {
    1: { name: 'Tier 1: Feature Coverage', passed: 0, failed: 0, total: 0 },
    2: { name: 'Tier 2: Boundary & Corner Cases', passed: 0, failed: 0, total: 0 },
    3: { name: 'Tier 3: Cross-Feature Combinations', passed: 0, failed: 0, total: 0 },
    4: { name: 'Tier 4: Real-World Scenarios', passed: 0, failed: 0, total: 0 },
  };

  const featureStats = {
    R1: { passed: 0, failed: 0 },
    R2: { passed: 0, failed: 0 },
    R3: { passed: 0, failed: 0 },
    R4: { passed: 0, failed: 0 },
    R5: { passed: 0, failed: 0 },
    R6: { passed: 0, failed: 0 },
  };

  const failures = [];

  for (const suite of suites) {
    console.log(`\n▸ Suite: ${suite.name}`);
    for (const test of suite.tests) {
      totalTests++;
      const tierObj = tierStats[test.tier] || tierStats[1];
      tierObj.total++;

      const t0 = performance.now();
      try {
        await test.fn();
        const duration = (performance.now() - t0).toFixed(2);
        passedTests++;
        tierObj.passed++;
        if (featureStats[test.feature]) {
          featureStats[test.feature].passed++;
        }
        console.log(`  ✓ [${test.id}] ${test.name} (${duration}ms)`);
      } catch (err) {
        const duration = (performance.now() - t0).toFixed(2);
        failedTests++;
        tierObj.failed++;
        if (featureStats[test.feature]) {
          featureStats[test.feature].failed++;
        }
        failures.push({
          suite: suite.name,
          test: test.name,
          id: test.id,
          tier: test.tier,
          error: err,
        });
        console.log(`  ✗ [${test.id}] ${test.name} (${duration}ms)`);
        console.log(`     Error: ${err.message}`);
      }
    }
  }

  const totalDuration = ((performance.now() - startTime) / 1000).toFixed(2);

  console.log('\n======================================================================');
  console.log('   E2E TEST RUN SUMMARY');
  console.log('======================================================================');
  console.log(`Total Executed: ${totalTests} | Passed: ${passedTests} | Failed: ${failedTests} | Duration: ${totalDuration}s\n`);

  console.log('--- Coverage by Tier ---');
  for (const [tier, stat] of Object.entries(tierStats)) {
    const rate = stat.total > 0 ? ((stat.passed / stat.total) * 100).toFixed(1) : '100.0';
    console.log(`  ${stat.name}: ${stat.passed}/${stat.total} passed (${rate}%)`);
  }

  console.log('\n--- Feature Coverage Breakdown (Tiers 1 & 2) ---');
  for (const [feat, stat] of Object.entries(featureStats)) {
    const featTotal = stat.passed + stat.failed;
    console.log(`  Feature ${feat}: ${stat.passed}/${featTotal} passed`);
  }

  if (failures.length > 0) {
    console.log('\n--- Failures Details ---');
    for (const f of failures) {
      console.log(`\n[${f.id}] ${f.suite} > ${f.test} (Tier ${f.tier})`);
      console.log(f.error.stack || f.error.message);
    }
    console.log('\n======================================================================');
    console.log('   TEST RUN FAILED');
    console.log('======================================================================\n');
    process.exit(1);
  } else {
    console.log('\n======================================================================');
    console.log('   ALL E2E TESTS PASSED (100% SUCCESS)');
    console.log('======================================================================\n');
    process.exit(0);
  }
}

runRunner().catch((err) => {
  console.error('Fatal Test Runner Error:', err);
  process.exit(1);
});
