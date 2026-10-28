import { appendFileSync } from 'node:fs';
import { Releaser, validateRunner } from './calver.mjs';
import { GitHub } from './github.mjs';

async function main() {
  const raw = process.env.CALVER_REQUEST || '';
  if (!raw || Buffer.byteLength(raw) > 1024 * 1024) throw new Error('bounded JSON request required');
  const request = JSON.parse(raw);
  if (!request || !['plan', 'publish'].includes(request.phase)) throw new Error('phase must be plan or publish');
  if (request.dryRun !== undefined && typeof request.dryRun !== 'boolean') throw new Error('dryRun must be a boolean');
  if (request.repair !== undefined && typeof request.repair !== 'boolean') throw new Error('repair must be a boolean');
  const readOnly = request.phase === 'plan' || request.dryRun === true;
  const api = new GitHub({ token: process.env.CALVER_TOKEN, readOnly });
  const repository = process.env.GITHUB_REPOSITORY;
  const releaser = new Releaser({ api, repository });
  const metadata = await api.request('GET', `/repos/${repository}`);
  if (metadata.full_name !== repository || typeof metadata.private !== 'boolean') throw new Error('repository metadata mismatch');
  validateRunner(process.env.CALVER_RUNNER_JSON, metadata.private ? 'private' : 'public', process.env.CALVER_PRIVATE_RUNNER_JSON);
  const result = request.phase === 'plan'
    ? await releaser.plan(request.input)
    : await releaser.publish(request.plan, { dryRun: request.dryRun === true, repair: request.repair === true, evidence: request.evidence });
  const { created, releaseUrl, dryRun, ...frozenPlan } = result;
  const values = {
    plan: JSON.stringify(frozenPlan), version: result.version, tag: result.tag,
    sha: result.sha, date: result.date, 'release-date': result.date,
    'previous-stable-tag': result.previousStableTag || '',
    created: String(result.created === true), 'release-url': result.releaseUrl || '',
    provenance: result.id, 'provenance-id': result.id,
  };
  const output = Object.entries(values).map(([key, value]) => `${key}=${value}\n`).join('');
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, output);
  process.stdout.write(output);
  if (process.env.GITHUB_STEP_SUMMARY) {
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `## CalVer ${request.phase}${request.dryRun ? ' (zero-write dry run)' : ''}\n\n- Version: \`${result.version}\`\n- Commit: \`${result.sha}\`\n- Provenance: \`${result.id}\`\n- Source run: ${result.evidence ? `https://github.com/${repository}/actions/runs/${result.evidence.runId}/attempts/${result.evidence.attempt}` : 'explicit repository exception'}\n`);
  }
}

main().catch(error => {
  if (['quiet-day', 'rollback', 'digest-already-published'].includes(error.code)) {
    const output = `created=false\nskipped=true\nreason=${error.code}\n`;
    if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, output);
    process.stdout.write(output);
    return;
  }
  process.stderr.write(`CalVer refused: ${error.message}\n`);
  process.exitCode = 1;
});
