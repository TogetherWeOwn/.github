import { createHash } from 'node:crypto';

const SHA = /^[0-9a-f]{40}$/;
const COUNTER = '(0|[1-9][0-9]*)';
const VERSION = new RegExp(`^([1-9][0-9]{3})\\.([1-9][0-9]{2,3})\\.${COUNTER}(?:-canary\\.${COUNTER})?$`);
const MAX_HISTORY_BYTES = 256 * 1024 * 1024;
const MAX_NOTES_BYTES = 120000;
const HEADER = /^(?<type>[a-z]+)(?:\((?<scope>[^()\r\n]+)\))?(?<bang>!)?: (?<desc>\S.*)$/;
const DEFAULT_SECTIONS = [
  { type: 'feat', section: 'Features' },
  { type: 'fix', section: 'Bug Fixes' },
  { type: 'perf', section: 'Performance Improvements' },
  { type: 'revert', section: 'Reverts' },
  { type: 'docs', section: 'Documentation' },
];

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

// Caller sections use the release-please `changelog-sections` shape. Returns
// null when omitted (built-in sections apply), otherwise a validated copy in
// caller order with `hidden: false` normalized away, so equivalent inputs
// freeze to the same plan identity. Bounds keep a frozen plan and its note
// headings small and free of markdown/control injection from a bad config.
export function normalizeSections(sections) {
  if (sections === undefined || sections === null) return null;
  requireValue(Array.isArray(sections) && sections.length >= 1 && sections.length <= 32, 'sections must be an array of 1 to 32 entries');
  const types = new Set();
  let visible = false;
  const normalized = sections.map(section => {
    // Plain data only, with own properties: plan identity hashes own
    // enumerable keys, so an inherited value must never change rendering.
    requireValue(section && typeof section === 'object' && !Array.isArray(section) && [Object.prototype, null].includes(Object.getPrototypeOf(section)), 'each section must be a plain object');
    requireValue(Object.hasOwn(section, 'type') && Object.hasOwn(section, 'section') && Object.keys(section).every(key => ['type', 'section', 'hidden'].includes(key)), 'section entries may only set type, section and hidden');
    requireValue(typeof section.type === 'string' && /^[a-z]{1,32}$/.test(section.type), 'section type must be 1 to 32 lowercase letters');
    requireValue(!types.has(section.type), `duplicate section type: ${section.type}`);
    types.add(section.type);
    requireValue(typeof section.section === 'string' && section.section.length >= 1 && section.section.length <= 80 && section.section.trim() === section.section && !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(section.section) && !section.section.startsWith('#'), 'section heading must be 1 to 80 trimmed characters without controls or a leading #');
    const hidden = Object.hasOwn(section, 'hidden') ? section.hidden : undefined;
    requireValue(hidden === undefined || typeof hidden === 'boolean', 'section hidden must be a boolean');
    if (!hidden) visible = true;
    return hidden ? { type: section.type, section: section.section, hidden: true } : { type: section.type, section: section.section };
  });
  requireValue(visible, 'sections must leave at least one visible section');
  return normalized;
}

function calendarDate(value) {
  requireValue(typeof value === 'string' && /^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}$/.test(value), 'date must be canonical YYYY-MM-DD');
  const date = new Date(`${value}T00:00:00.000Z`);
  requireValue(Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value, 'invalid calendar date');
  return value;
}

export function parseVersion(version) {
  requireValue(typeof version === 'string', 'version must be a string');
  const match = VERSION.exec(version);
  requireValue(match, 'invalid canonical CalVer version');
  const [, year, mdd, patch, canary] = match;
  const month = mdd.slice(0, -2);
  const day = mdd.slice(-2);
  const date = calendarDate(`${year}-${month.padStart(2, '0')}-${day}`);
  requireValue(String(Number(month)) === month, 'noncanonical month');
  requireValue(Number.isSafeInteger(Number(patch)) && (canary === undefined || Number.isSafeInteger(Number(canary))), 'counter outside safe integer range');
  return { date, patch: Number(patch), canary: canary === undefined ? null : Number(canary) };
}

function namespacePrefix(namespace) {
  requireValue(namespace === '' || namespace === 'build', 'unsupported release namespace');
  return namespace ? `${namespace}/` : '';
}

export function parseTag(tag, namespace = '') {
  const prefix = namespacePrefix(namespace);
  if (typeof tag !== 'string' || !tag.startsWith(prefix)) return null;
  const local = tag.slice(prefix.length);
  let channel;
  let version;
  if (local.startsWith('canary/v')) {
    channel = 'canary';
    version = local.slice(8);
  } else if (local.startsWith('v')) {
    channel = 'stable';
    version = local.slice(1);
  } else return null;
  try {
    const parsed = parseVersion(version);
    if ((parsed.canary === null) !== (channel === 'stable')) return null;
    return { ...parsed, version, channel };
  } catch {
    return null;
  }
}

export function allocateVersion({ tags, date, channel, namespace = '' }) {
  calendarDate(date);
  namespacePrefix(namespace);
  requireValue(channel === 'stable' || channel === 'canary', 'invalid channel');
  requireValue(Array.isArray(tags) && tags.every(tag => typeof tag === 'string'), 'tags must be complete tag names');
  const versions = tags.map(tag => parseTag(tag, namespace)).filter(tag => tag?.date === date);
  const patch = Math.max(-1, ...versions.filter(tag => tag.channel === 'stable').map(tag => tag.patch)) + 1;
  const n = Math.max(-1, ...versions.filter(tag => tag.channel === 'canary' && tag.patch === patch).map(tag => tag.canary)) + 1;
  requireValue(Number.isSafeInteger(patch) && Number.isSafeInteger(n), 'counter exhausted');
  const [year, month, day] = date.split('-');
  const version = `${year}.${Number(month)}${day}.${patch}${channel === 'canary' ? `-canary.${n}` : ''}`;
  const tag = `${namespacePrefix(namespace)}${channel === 'canary' ? 'canary/' : ''}v${version}`;
  return { version, tag, date, channel, namespace };
}

export function validateRunner(runnerJson, visibility, privateRunnerJson) {
  const runner = JSON.parse(runnerJson);
  if (visibility === 'public') {
    requireValue(runner === 'ubuntu-latest', 'public caller requires free ubuntu-latest');
  } else {
    requireValue(visibility === 'private', 'unknown repository visibility');
    requireValue(typeof privateRunnerJson === 'string' && privateRunnerJson.length > 0, 'private runner configuration required');
    const configured = JSON.parse(privateRunnerJson);
    requireValue(Array.isArray(configured) && configured.length >= 2 && configured.includes('self-hosted') && configured.every(label => typeof label === 'string' && /^[a-zA-Z0-9_-]+$/.test(label)), 'private configuration must be explicit self-hosted labels');
    requireValue(JSON.stringify(runner) === JSON.stringify(configured), 'private runner differs from existing configured routing');
  }
  return runner;
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

function identity(value) {
  return createHash('sha256').update(canonical(value)).digest('hex');
}

function compareVersions(a, b) {
  return a.date.localeCompare(b.date) || a.patch - b.patch;
}

function tagPath(tag) {
  return encodeURIComponent(tag);
}

function numericStable(tag, namespace = '') {
  const prefix = namespacePrefix(namespace);
  if (!tag.startsWith(`${prefix}v`)) return null;
  const version = tag.slice(prefix.length + 1);
  if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version) || version.length > 100) return null;
  // The bot's legacy daily tags use YYYY.MM.DD, not CalVer MDD.P.
  // Normalize their real calendar date before ordering migration boundaries.
  const daily = /^([1-9][0-9]{3})\.([0-9]{2})\.([0-9]{2})$/.exec(version);
  if (daily) {
    try {
      calendarDate(`${daily[1]}-${daily[2]}-${daily[3]}`);
      return [BigInt(daily[1]), BigInt(`${Number(daily[2])}${daily[3]}`), 0n];
    } catch { /* Other numeric legacy versions retain their numeric ordering. */ }
  }
  return version.split('.').map(BigInt);
}

function compareNumeric(a, b) {
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  return 0;
}

// Conventional Commit parsing/grouping follows the existing daily-digest
// renderer; unlike its subprocess interface, API JSON bodies have no argv cap.
export function renderNotes(commits, { repository = '', sections } = {}) {
  requireValue(Array.isArray(commits), 'commits must be an array');
  const effectiveSections = normalizeSections(sections) || DEFAULT_SECTIONS;
  let bytes = 0;
  const parsed = [];
  for (const commit of commits) {
    requireValue(commit && SHA.test(commit.sha) && typeof commit.message === 'string', 'malformed commit');
    bytes += Buffer.byteLength(commit.message);
    requireValue(bytes <= MAX_HISTORY_BYTES, 'release history exceeds 256 MiB bound');
    const [subject, ...body] = commit.message.split('\n');
    const match = HEADER.exec(subject.trim());
    if (!match) continue;
    let desc = match.groups.desc.trim();
    const pr = / \(#([0-9]+)\)$/.exec(desc);
    if (pr) desc = desc.slice(0, pr.index);
    const breakingNote = /^BREAKING[ -]CHANGE: ([\s\S]*)/m.exec(body.join('\n'))?.[1].trim() || '';
    parsed.push({ sha: commit.sha, type: match.groups.type, scope: match.groups.scope?.trim() || '', desc, pr: pr?.[1], breakingNote, breaking: Boolean(match.groups.bang) || Boolean(breakingNote) });
  }
  const url = repository ? `https://github.com/${repository}` : '';
  const entry = commit => `* ${commit.scope ? `**${commit.scope}:** ` : ''}${commit.desc}${commit.pr && url ? ` ([#${commit.pr}](${url}/issues/${commit.pr}))` : ''}${url ? ` ([${commit.sha.slice(0, 7)}](${url}/commit/${commit.sha}))` : ` (${commit.sha.slice(0, 7)})`}`;
  const sort = (a, b) => a.scope.localeCompare(b.scope) || a.desc.localeCompare(b.desc);
  const lines = [];
  const add = (heading, items) => {
    if (items.length) lines.push(`### ${heading}`, '', ...items.sort(sort).map(entry), '');
  };
  add('⚠ BREAKING CHANGES', parsed.filter(commit => commit.breaking).map(commit => ({ ...commit, desc: commit.breakingNote ? `${commit.desc}\n\n  ${commit.breakingNote.replaceAll('\n', '\n  ')}` : commit.desc })));
  const groups = new Map();
  for (const section of effectiveSections) {
    if (section.hidden) continue;
    if (!groups.has(section.section)) groups.set(section.section, new Set());
    groups.get(section.section).add(section.type);
  }
  for (const [heading, types] of groups) add(heading, parsed.filter(commit => types.has(commit.type)));
  const notes = lines.length ? `${lines.join('\n')}\n` : 'No user-facing changes; the compare link lists every commit.\n';
  requireValue(Buffer.byteLength(notes) <= MAX_NOTES_BYTES, 'release notes exceed GitHub body budget; narrow the boundary explicitly');
  return notes;
}

export class Releaser {
  constructor({ api, repository, now = () => new Date() }) {
    requireValue(typeof repository === 'string' && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository), 'invalid repository');
    this.api = api;
    this.repository = repository;
    this.base = `/repos/${repository}`;
    this.now = now;
  }

  async optional(path) {
    try { return await this.api.request('GET', path); }
    catch (error) { if (error.status === 404) return null; throw error; }
  }

  async ref(tag) {
    const ref = await this.optional(`${this.base}/git/ref/tags/${tagPath(tag)}`);
    if (!ref) return null;
    requireValue(ref.ref === `refs/tags/${tag}`, 'unexpected exact ref response');
    let object = ref.object;
    const seen = new Set();
    let annotation = null;
    for (let depth = 0; object?.type === 'tag'; depth += 1) {
      requireValue(depth < 16 && SHA.test(object.sha) && !seen.has(object.sha), 'annotated tag peel is cyclic or too deep');
      seen.add(object.sha);
      const tagObject = await this.api.request('GET', `${this.base}/git/tags/${object.sha}`);
      requireValue(tagObject.sha === object.sha, 'tag object SHA mismatch');
      if (annotation === null) annotation = tagObject.message;
      object = tagObject.object;
    }
    requireValue(object?.type === 'commit' && SHA.test(object.sha), 'tag does not point to an exact commit');
    return { name: tag, sha: object.sha, annotation };
  }

  async tags() {
    const listed = await this.api.paginate(`${this.base}/tags?per_page=100`);
    requireValue(Array.isArray(listed) && listed.length <= 10000, 'tag enumeration exceeds bound');
    const names = new Set();
    const tags = [];
    for (const item of listed) {
      requireValue(typeof item?.name === 'string' && item.name.length > 0 && !names.has(item.name) && SHA.test(item.commit?.sha), 'ambiguous tag enumeration');
      names.add(item.name);
      const ref = await this.ref(item.name);
      requireValue(ref && ref.sha === item.commit.sha, 'tag changed during enumeration');
      tags.push(ref);
    }
    return tags;
  }

  async reachable(ancestor, target) {
    const result = await this.api.request('GET', `${this.base}/compare/${ancestor}...${target}`);
    requireValue(['ahead', 'behind', 'identical', 'diverged'].includes(result.status), 'invalid comparison response');
    return result.status === 'ahead' || result.status === 'identical';
  }

  async verifySha(sha) {
    requireValue(typeof sha === 'string' && SHA.test(sha), 'sha must be exact lowercase 40-hex');
    const commit = await this.api.request('GET', `${this.base}/git/commits/${sha}`);
    requireValue(commit.sha === sha && await this.reachable(sha, 'main'), 'sha is not reachable from repository main');
  }

  policy(input) {
    requireValue(['stable', 'canary'].includes(input.channel), 'invalid channel');
    requireValue(['staged-promotion', 'live-digest', 'direct-release'].includes(input.policy), 'explicit release policy required');
    namespacePrefix(input.namespace || '');
    requireValue(['auto', 'never'].includes(input.latest || 'auto'), 'invalid Latest policy');
    requireValue(typeof input.operation === 'string' && /^[A-Za-z0-9_.:/-]{1,200}$/.test(input.operation), 'bounded operation identifier required');
    if (input.policy === 'live-digest') requireValue(this.repository === 'TogetherWeOwn/two-bot-next' && input.channel === 'stable' && !input.namespace, 'live-digest is only the bot stable exception');
    if (input.policy === 'direct-release') requireValue(input.channel === 'stable' && ((this.repository === 'TogetherWeOwn/kagi-shinobi' && !input.namespace) || (this.repository === 'TogetherWeOwn/routeware-shadow-api' && input.namespace === 'build' && input.latest === 'never')), 'direct-release requires an explicit repository exception');
    if (input.namespace) requireValue(input.latest === 'never', 'build namespace cannot advance API Latest');
  }

  sourceSelection(e) {
    requireValue(e && Number.isSafeInteger(e.runId) && e.runId > 0 && Number.isSafeInteger(e.attempt) && e.attempt > 0, 'source run/attempt required');
    requireValue(typeof e.workflow === 'string' && /^\.github\/workflows\/[a-zA-Z0-9_-]+\.ya?ml$/.test(e.workflow), 'exact workflow path required');
    requireValue(Array.isArray(e.jobs) && e.jobs.length > 0 && e.jobs.length <= 100 && new Set(e.jobs).size === e.jobs.length && e.jobs.every(job => typeof job === 'string' && job.length > 0 && job.length <= 200), 'required executed job names required');
    return { runId: e.runId, attempt: e.attempt, workflow: e.workflow, jobs: [...e.jobs] };
  }

  async verifyRun(input, source, { allowActive = false, planning = false } = {}) {
    const e = this.sourceSelection(source);
    const run = await this.api.request('GET', `${this.base}/actions/runs/${e.runId}/attempts/${e.attempt}`);
    const complete = run.status === 'completed' && run.conclusion === 'success';
    // A canary publisher can be a successor job in the same transaction run.
    // Its named deploy/certification jobs must already be completed below;
    // requiring its own whole run to finish would make publication impossible.
    const activeCanary = allowActive && input.channel === 'canary' && run.status === 'in_progress' && run.conclusion === null;
    requireValue(run.id === e.runId && run.run_attempt === e.attempt && run.repository?.full_name === this.repository && run.head_repository?.full_name === this.repository && Number.isSafeInteger(run.repository?.id) && run.repository.id > 0 && run.head_repository.id === run.repository.id && run.head_sha === input.sha && run.head_branch === 'main' && run.event === 'push' && run.path === e.workflow && (complete || activeCanary), `unsafe staging run${planning ? ' selector' : ''}: require eligible same-repository main push attempt`);
    return run;
  }

  async verifyEvidence(input, { allowActive = false } = {}) {
    if (input.policy !== 'staged-promotion') return;
    const e = input.evidence;
    requireValue(e && Number.isSafeInteger(e.artifactId) && e.artifactId > 0, 'source artifact required');
    requireValue(typeof e.artifactName === 'string' && e.artifactName.length > 0 && e.artifactName.length <= 200 && /^sha256:[0-9a-f]{64}$/.test(e.artifactDigest), 'immutable artifact identity required');
    const run = await this.verifyRun(input, e, { allowActive });
    const jobs = await this.api.paginate(`${this.base}/actions/runs/${e.runId}/attempts/${e.attempt}/jobs?per_page=100`, 'jobs');
    for (const name of e.jobs) {
      const found = jobs.filter(job => job.name === name);
      requireValue(found.length === 1 && found[0].run_id === e.runId && found[0].run_attempt === e.attempt && found[0].head_sha === input.sha && found[0].status === 'completed' && found[0].conclusion === 'success' && Number.isFinite(Date.parse(found[0].started_at)) && Date.parse(found[0].started_at) >= Date.parse(run.run_started_at) && Number.isFinite(Date.parse(found[0].completed_at)) && Date.parse(found[0].completed_at) >= Date.parse(found[0].started_at), `required staging job not executed successfully: ${name}`);
    }
    const artifact = await this.api.request('GET', `${this.base}/actions/artifacts/${e.artifactId}`);
    requireValue(artifact.id === e.artifactId && artifact.name === e.artifactName && artifact.digest === e.artifactDigest && artifact.expired === false && artifact.size_in_bytes > 0 && artifact.workflow_run?.id === e.runId && artifact.workflow_run?.repository_id === run.repository.id && artifact.workflow_run?.head_repository_id === run.head_repository.id && artifact.workflow_run?.head_sha === input.sha && artifact.workflow_run?.head_branch === 'main', 'artifact identity or source mismatch');
    requireValue(Number.isFinite(Date.parse(artifact.created_at)) && Number.isFinite(Date.parse(run.run_started_at)) && Date.parse(artifact.created_at) >= Date.parse(run.run_started_at) && Number.isFinite(Date.parse(artifact.expires_at)) && Date.parse(artifact.expires_at) > this.now().getTime(), 'artifact is expired or from a prior attempt');
    if (input.channel === 'stable') {
      const parsed = parseTag(input.sourceCanary, input.namespace || '');
      requireValue(parsed?.channel === 'canary', 'source canary required for staged stable');
      const ref = await this.ref(input.sourceCanary);
      const source = this.provenance(ref);
      requireValue(ref?.sha === input.sha && source.channel === 'canary' && source.repository === this.repository && source.policy === 'staged-promotion' && canonical(source.publicationEvidence || source.evidence) === canonical(e), 'source canary does not bind this staged artifact');
    }
  }

  provenance(ref) {
    requireValue(typeof ref?.annotation === 'string' && ref.annotation.startsWith('calver-plan-v1\n'), 'tag lacks operation-bound CalVer provenance');
    const record = JSON.parse(ref.annotation.slice('calver-plan-v1\n'.length));
    const { publicationEvidence, ...plan } = record;
    this.validatePlan(plan);
    requireValue(plan.tag === ref.name && plan.sha === ref.sha, 'annotation target mismatch');
    if (plan.policy === 'staged-promotion') requireValue(canonical(this.sourceSelection(publicationEvidence || plan.evidence)) === canonical(plan.source), 'publication source mismatch');
    return { ...plan, publicationEvidence: publicationEvidence || plan.evidence };
  }

  validatePlan(plan) {
    requireValue(plan && plan.schema === 'calver-plan-v1' && plan.repository === this.repository, 'wrong plan repository/schema');
    const { id, ...payload } = plan;
    requireValue(id === identity(payload), 'plan integrity mismatch');
    this.policy(plan);
    const parsed = parseTag(plan.tag, plan.namespace);
    requireValue(parsed && parsed.version === plan.version && parsed.date === calendarDate(plan.date) && parsed.channel === plan.channel && SHA.test(plan.sha), 'plan version binding mismatch');
    requireValue(plan.repairDate === null || plan.repairDate === plan.date, 'repair date mismatch');
    // Plans frozen before caller sections existed carry no field; both that
    // and an explicit null mean the built-in sections.
    normalizeSections(plan.sections);
  }

  async previousStable(tags, input) {
    const candidates = tags.filter(tag => {
      const parsed = parseTag(tag.name, input.namespace);
      return parsed?.channel === 'stable' && compareVersions(parsed, parseVersion(input.version)) < 0;
    }).sort((a, b) => compareVersions(parseTag(b.name, input.namespace), parseTag(a.name, input.namespace)));
    // Preserve the first-release notes boundary without treating legacy SemVer
    // or daily digest tags as CalVer counter authorities.
    if (!input.namespace) {
      const current = numericStable(`v${input.version.replace(/-canary\.[0-9]+$/, '')}`);
      const legacy = tags.filter(tag => numericStable(tag.name) && !parseTag(tag.name) && compareNumeric(numericStable(tag.name), current) < 0);
      legacy.sort((a, b) => compareNumeric(numericStable(b.name), numericStable(a.name)));
      candidates.push(...legacy);
    }
    candidates.sort((a, b) => compareNumeric(numericStable(b.name, input.namespace), numericStable(a.name, input.namespace)));
    for (const tag of candidates) if (await this.reachable(tag.sha, input.sha)) return tag.name;
    return null;
  }

  assertOperation(prior, input) {
    if (prior.channel !== input.channel || prior.namespace !== (input.namespace || '')) return;
    if (prior.sha === input.sha) requireValue(prior.operation === input.operation, 'SHA already published under a different operation');
    if (prior.operation === input.operation && input.id) requireValue(prior.id === input.id, 'operation already belongs to a different frozen plan');
  }

  async plan(input) {
    input = { ...input, latest: input.latest || (input.namespace ? 'never' : 'auto') };
    this.policy(input);
    const sections = normalizeSections(input.sections);
    await this.verifySha(input.sha);
    if (input.policy === 'staged-promotion' && input.channel === 'canary' && !input.evidence) {
      await this.verifyRun(input, input.source, { allowActive: true, planning: true });
    } else await this.verifyEvidence(input);
    const source = input.policy === 'staged-promotion' ? this.sourceSelection(input.evidence || input.source) : null;
    if (input.policy === 'staged-promotion' && input.source && input.evidence) requireValue(canonical(this.sourceSelection(input.source)) === canonical(source), 'source selector differs from verified evidence');
    const date = input.repairDate ? calendarDate(input.repairDate) : this.now().toISOString().slice(0, 10);
    requireValue(date <= this.now().toISOString().slice(0, 10), 'repair date cannot be in the future');
    const tags = await this.tags();
    // An operation retry recovers its frozen original plan, even across dates.
    for (const tag of tags) {
      if (!tag.annotation?.startsWith('calver-plan-v1\n')) continue;
      const { publicationEvidence, ...prior } = this.provenance(tag);
      this.assertOperation(prior, input);
      if (prior.operation !== input.operation || prior.channel !== input.channel || prior.namespace !== (input.namespace || '')) continue;
      requireValue(prior.sha === input.sha && prior.policy === input.policy && canonical(prior.source) === canonical(source) && canonical(prior.evidence) === canonical(input.evidence || null) && prior.sourceCanary === (input.sourceCanary || null) && prior.latest === (input.latest || 'auto') && prior.repairDate === (input.repairDate || null) && canonical(normalizeSections(prior.sections)) === canonical(sections), 'operation identity already used for a different release');
      return prior;
    }
    if (input.policy === 'live-digest' && !input.repairDate) {
      const stables = tags.filter(tag => numericStable(tag.name)).sort((a, b) => compareNumeric(numericStable(b.name), numericStable(a.name)));
      const latest = stables[0];
      const skip = (code, message) => { throw Object.assign(new Error(message), { code }); };
      if (latest?.sha === input.sha) skip('quiet-day', 'live production already matches the latest digest');
      if (latest && !await this.reachable(latest.sha, input.sha)) skip('rollback', 'live digest refuses a rollback or divergent build');
      if (stables.some(tag => parseTag(tag.name)?.date === date || tag.name === `v${date.replaceAll('-', '.')}`)) skip('digest-already-published', 'daily digest already published for this UTC date');
    }
    const allocated = allocateVersion({ tags: tags.map(tag => tag.name), date, channel: input.channel, namespace: input.namespace || '' });
    const payload = { schema: 'calver-plan-v1', repository: this.repository, ...allocated, sha: input.sha, operation: input.operation, policy: input.policy, latest: input.latest || 'auto', repairDate: input.repairDate || null, sourceCanary: input.sourceCanary || null, source, evidence: input.evidence || null, sections };
    payload.previousStableTag = await this.previousStable(tags, payload);
    return { ...payload, id: identity(payload) };
  }

  async notes(plan) {
    const query = plan.previousStableTag
      ? `${this.base}/compare/${tagPath(plan.previousStableTag)}...${plan.sha}?per_page=100`
      : `${this.base}/commits?sha=${plan.sha}&per_page=100`;
    const commits = await this.api.paginate(query, plan.previousStableTag ? 'commits' : undefined);
    const body = renderNotes(commits.map(commit => ({ sha: commit.sha, message: commit.commit.message })), { repository: this.repository, sections: plan.sections });
    return `${body}\n<!-- calver-plan:${plan.id} -->\n\nSource: ${plan.sha}${plan.sourceCanary ? `; canary: ${plan.sourceCanary}` : ''}${plan.evidence ? `; run: https://github.com/${this.repository}/actions/runs/${plan.evidence.runId}/attempts/${plan.evidence.attempt}; artifact: ${plan.evidence.artifactId} (${plan.evidence.artifactDigest})` : ''}\n`;
  }

  async publish(plan, { dryRun = false, repair = false, evidence } = {}) {
    this.validatePlan(plan);
    requireValue(typeof dryRun === 'boolean' && typeof repair === 'boolean', 'publish flags must be booleans');
    requireValue(!plan.repairDate || repair, 'backdated plans require explicit repair');
    const proof = evidence || plan.evidence;
    if (plan.policy === 'staged-promotion') {
      requireValue(canonical(this.sourceSelection(proof)) === canonical(plan.source), 'publication must match the frozen source selection');
      if (plan.evidence) requireValue(canonical(proof) === canonical(plan.evidence), 'cannot replace a prestamped artifact identity');
    } else requireValue(!evidence, 'repository exception does not accept staged proof');
    await this.verifySha(plan.sha);
    await this.verifyEvidence({ ...plan, evidence: proof }, { allowActive: plan.channel === 'canary' && plan.evidence === null });
    const matches = ref => {
      const record = this.provenance(ref);
      return record.id === plan.id && canonical(record.publicationEvidence) === canonical(proof);
    };
    let existing = await this.ref(plan.tag);
    if (existing) requireValue(matches(existing), 'planned tag collision: never reallocate after stamping/deployment');
    // All bounded notes/evidence work precedes the first mutating request.
    const body = plan.channel === 'stable' ? await this.notes(plan) : null;
    const releasePath = `${this.base}/releases/tags/${tagPath(plan.tag)}`;
    const release = plan.channel === 'stable' ? await this.optional(releasePath) : null;
    if (release) requireValue(existing && release.tag_name === plan.tag && release.draft === false && release.prerelease === false && release.body?.includes(`<!-- calver-plan:${plan.id} -->`), 'existing release provenance mismatch');
    if (existing && plan.channel === 'stable' && !release) requireValue(repair, 'tag exists without Release: explicit repair required');
    const allTags = await this.tags();
    for (const tag of allTags) {
      if (tag.annotation?.startsWith('calver-plan-v1\n')) this.assertOperation(this.provenance(tag), plan);
    }
    const stableVersion = numericStable(`${namespacePrefix(plan.namespace)}v${plan.version.replace(/-canary\.[0-9]+$/, '')}`, plan.namespace);
    const historical = allTags.some(tag => {
      const other = numericStable(tag.name, plan.namespace);
      return other && compareNumeric(other, stableVersion) > 0;
    });
    const makeLatest = plan.latest !== 'never' && !plan.namespace && !historical && (!plan.repairDate || plan.date === this.now().toISOString().slice(0, 10));
    if (dryRun) return { ...plan, created: false, releaseUrl: release?.html_url || '', dryRun: true };
    let created = false;
    if (!existing) {
      const tagObject = await this.api.request('POST', `${this.base}/git/tags`, { tag: plan.tag, message: `calver-plan-v1\n${canonical({ ...plan, publicationEvidence: proof })}`, object: plan.sha, type: 'commit' });
      requireValue(SHA.test(tagObject.sha), 'tag creation returned invalid object');
      try {
        await this.api.request('POST', `${this.base}/git/refs`, { ref: `refs/tags/${plan.tag}`, sha: tagObject.sha });
        created = true;
      } catch (error) {
        if (error.status !== 422) throw error;
        existing = await this.ref(plan.tag);
        if (!existing) throw error;
        requireValue(matches(existing), 'allocation collision; deployment version must not change');
      }
      if (created) {
        const verified = await this.ref(plan.tag);
        requireValue(verified && matches(verified), 'published tag verification failed');
      }
    }
    if (plan.channel === 'canary') return { ...plan, created, releaseUrl: '', dryRun: false };
    if (release) return { ...plan, created: false, releaseUrl: release.html_url, dryRun: false };
    let published;
    try {
      published = await this.api.request('POST', `${this.base}/releases`, { tag_name: plan.tag, target_commitish: plan.sha, name: plan.version, body, draft: false, prerelease: false, make_latest: makeLatest ? 'true' : 'false' });
    } catch (error) {
      if (error.status !== 422) throw error;
      published = await this.optional(releasePath);
      if (!published) throw error;
      requireValue(published?.tag_name === plan.tag && published.draft === false && published.prerelease === false && published.body?.includes(`<!-- calver-plan:${plan.id} -->`), 'Release creation failed without an exact concurrent match');
    }
    requireValue(published?.tag_name === plan.tag && typeof published.html_url === 'string' && published.html_url.startsWith(`https://github.com/${this.repository}/releases/tag/`), 'invalid published Release response');
    return { ...plan, created: true, releaseUrl: published.html_url, dryRun: false };
  }
}
