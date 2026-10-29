const API_ORIGIN = 'https://api.github.com';
const MAX_BYTES = 256 * 1024 * 1024;

export class GitHub {
  constructor({ token, fetchImpl = fetch, readOnly = false }) {
    if (!token) throw new Error('repository-local GitHub token required');
    this.token = token;
    this.fetch = fetchImpl;
    this.readOnly = readOnly;
  }

  async page(method, path, body) {
    const url = new URL(path, API_ORIGIN);
    if (url.origin !== API_ORIGIN || !url.pathname.startsWith('/repos/') || url.username || url.password || url.hash) throw new Error('refuse nonrepository GitHub API URL');
    if (this.readOnly && method !== 'GET') throw new Error('read-only invocation cannot mutate GitHub');
    const response = await this.fetch(url.href, {
      method,
      headers: { Authorization: `Bearer ${this.token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: 'error',
      signal: AbortSignal.timeout(30000),
    });
    if (response.redirected || (response.url && response.url !== url.href)) throw new Error('refuse redirected GitHub response');
    if (!response.ok || response.status === 206) {
      // Do not echo upstream bodies or request headers: they can contain tokens
      // or private metadata. Preserve the actual HTTP status for exact handling.
      const error = new Error(`GitHub ${method} ${url.pathname}: HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    const chunks = [];
    let bytes = 0;
    for await (const chunk of response.body) {
      bytes += chunk.byteLength;
      if (bytes > MAX_BYTES) throw new Error('GitHub response exceeds 256 MiB');
      chunks.push(chunk);
    }
    let data;
    try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw new Error('invalid GitHub JSON response'); }
    if (data === null || typeof data !== 'object' || data.truncated === true || data.incomplete_results === true) throw new Error('GitHub response is incomplete');
    return { data, link: response.headers.get('link'), bytes };
  }

  async request(method, path, body) {
    return (await this.page(method, path, body)).data;
  }

  async paginate(path, key) {
    const all = [];
    const seen = new Set();
    let next = path;
    let bytes = 0;
    let expected = null;
    let repositoryId = null;
    const endpoint = new URL(path, API_ORIGIN);
    const repository = /^\/repos\/([^/]+\/[^/]+)(\/.*)?$/.exec(endpoint.pathname);
    const filters = url => JSON.stringify([...url.searchParams].filter(([name]) => name !== 'page').sort());
    for (let page = 0; next; page += 1) {
      const url = new URL(next, API_ORIGIN);
      if (page >= 10000 || seen.has(url.href)) throw new Error('cyclic or excessive GitHub pagination');
      seen.add(url.href);
      const result = await this.page('GET', next);
      bytes += result.bytes;
      if (bytes > MAX_BYTES) throw new Error('GitHub history exceeds 256 MiB');
      const items = key ? result.data[key] : result.data;
      if (!Array.isArray(items)) throw new Error('invalid GitHub list response');
      const count = key === 'commits' ? result.data.total_commits : result.data.total_count;
      if (count !== undefined) {
        if (!Number.isSafeInteger(count) || count < 0 || (expected !== null && expected !== count)) throw new Error('changing GitHub pagination count');
        expected = count;
      }
      all.push(...items);
      if (all.length > 100000) throw new Error('GitHub list exceeds item bound');
      next = null;
      if (result.link) {
        for (const link of result.link.split(',')) {
          const match = /^\s*<([^>]+)>;\s*rel="([a-z]+)"\s*$/.exec(link);
          if (!match) throw new Error('malformed GitHub pagination Link');
          if (match[2] === 'next') {
            if (next) throw new Error('ambiguous GitHub pagination');
            next = match[1];
            const candidate = new URL(next, API_ORIGIN);
            if (candidate.origin !== API_ORIGIN || candidate.username || candidate.password || candidate.hash || filters(candidate) !== filters(endpoint)) throw new Error('GitHub pagination changed endpoint');
            if (candidate.pathname !== endpoint.pathname) {
              const alias = /^\/repositories\/([1-9][0-9]*)(\/.*)?$/.exec(candidate.pathname);
              if (!repository || !alias || (alias[2] || '') !== (repository[2] || '')) throw new Error('GitHub pagination changed endpoint');
              // GitHub's Link URLs use numeric repository IDs. Verify that alias
              // independently, then keep requests in the named repository scope.
              if (repositoryId === null) {
                const metadata = await this.request('GET', `/repos/${repository[1]}`);
                if (!Number.isSafeInteger(metadata.id) || metadata.id <= 0 || typeof metadata.full_name !== 'string' || metadata.full_name.toLowerCase() !== repository[1].toLowerCase()) throw new Error('GitHub pagination repository identity mismatch');
                repositoryId = String(metadata.id);
              }
              if (alias[1] !== repositoryId) throw new Error('GitHub pagination repository identity mismatch');
              candidate.pathname = endpoint.pathname;
            }
            const pages = candidate.searchParams.getAll('page');
            if (pages.length !== 1 || !/^[1-9][0-9]*$/.test(pages[0]) || !Number.isSafeInteger(Number(pages[0]))) throw new Error('invalid GitHub pagination page');
            next = candidate.href;
          }
        }
      }
    }
    if (expected !== null && all.length !== expected) throw new Error('truncated or duplicated GitHub list');
    return all;
  }
}
