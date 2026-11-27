/** Render the local OpenAPI document as a searchable reference using DOM text nodes only. */
(() => {
  const METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'];
  const toc = document.querySelector('#api-toc');
  const container = document.querySelector('#api-operations');
  const count = document.querySelector('#api-count');
  const search = document.querySelector('#api-search');
  const version = document.querySelector('#api-version');
  const tocToggle = document.querySelector('.api-toc-toggle');
  const nav = document.querySelector('#api-nav');
  if (!toc || !container || !count || !search || !globalThis.EditarrApiSchema || !globalThis.EditarrApiExamples) return;
  const { el } = globalThis.EditarrApiSchema;
  const { AUTH_LABELS, authMode } = globalThis.EditarrApiExamples;
  let parameterType, schemaTree, curlExample;

  let spec = null;
  let total = 0;
  const operations = [];
  const sections = [];

  function slug(text) {
    return text.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  function methodBadge(method) {
    return el('span', `api-method api-method-${method}`, method.toUpperCase());
  }

  function parametersTable(entry) {
    const parameters = entry.operation.parameters ?? [];
    const scroll = el('div', 'api-table-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', `Parameters for ${entry.method.toUpperCase()} ${entry.path}`);
    const table = el('table', 'api-table');
    const headRow = el('tr');
    ['Name', 'In', 'Type', 'Required', 'Description'].forEach((name) => {
      const cell = el('th', null, name);
      cell.scope = 'col';
      headRow.append(cell);
    });
    const thead = el('thead');
    thead.append(headRow);
    table.append(thead);
    const rows = el('tbody');
    for (const parameter of parameters) {
      const row = el('tr');
      const name = el('td');
      name.append(el('code', null, parameter.name));
      row.append(name, el('td', null, parameter.in), el('td', 'api-type-cell', parameterType(parameter.schema)),
        el('td', null, parameter.required ? 'Yes' : 'No'),
        el('td', null, parameter.description ?? parameter.schema?.description ?? ''));
      rows.append(row);
    }
    table.append(rows);
    scroll.append(table);
    return scroll;
  }

  function block(title, ...children) {
    const section = el('div', 'api-block');
    section.append(el('h3', 'api-block-title', title), ...children);
    return section;
  }

  /** Fill an operation's body the first time it is opened. */
  function buildBody(entry) {
    if (entry.built) return;
    entry.built = true;
    const { operation } = entry;
    const body = entry.body;
    for (const paragraph of (operation.description ?? '').split(/\n\s*\n/).filter(Boolean)) {
      body.append(el('p', 'api-op-desc', paragraph.trim()));
    }
    const meta = el('p', 'api-op-meta');
    meta.append(el('span', `api-auth api-auth-${entry.auth}`, AUTH_LABELS[entry.auth]));
    meta.append(el('code', 'api-op-id', operation.operationId));
    body.append(meta);
    if (operation.parameters?.length) body.append(block('Parameters', parametersTable(entry)));
    const requestBody = operation.requestBody;
    if (requestBody?.content) {
      const parts = [];
      for (const [type, media] of Object.entries(requestBody.content)) {
        const label = el('p', 'api-media');
        label.append(el('code', null, type));
        if (requestBody.required) label.append(el('span', 'api-flag api-flag-required', 'required'));
        parts.push(label, schemaTree(media.schema ?? {}));
      }
      body.append(block('Request body', ...parts));
    }
    const responses = el('div', 'api-responses');
    for (const [status, response] of Object.entries(operation.responses ?? {})) {
      const row = el('div', 'api-response');
      const head = el('p', 'api-response-head');
      head.append(el('span', `api-status api-status-${status[0]}`, status), el('span', null, response.description ?? ''));
      row.append(head);
      for (const [type, media] of Object.entries(response.content ?? {})) {
        if (type !== 'application/json') { row.append(el('p', 'api-media', type)); continue; }
        if (media.schema && Object.keys(media.schema).length) row.append(schemaTree(media.schema));
      }
      responses.append(row);
    }
    body.append(block('Responses', responses));
    const example = curlExample(entry);
    const parts = example.notes.map((note) => el('p', 'api-note api-example-note', note));
    if (example.text) {
      const pre = el('pre', 'api-code');
      pre.tabIndex = 0;
      pre.append(el('code', null, example.text));
      parts.unshift(pre);
    }
    body.append(block('Example', ...parts));
  }

  function renderOperation(entry) {
    const article = el('details', 'api-op');
    article.id = entry.id;
    const summary = el('summary', 'api-op-summary');
    summary.append(methodBadge(entry.method), el('code', 'api-op-path', entry.path),
      el('span', 'api-op-title', entry.operation.summary ?? ''));
    entry.body = el('div', 'api-op-body');
    article.append(summary, entry.body);
    article.addEventListener('toggle', () => { if (article.open) buildBody(entry); });
    entry.element = article;
    return article;
  }

  function render() {
    const declared = (spec.tags ?? []).map((tag) => tag.name);
    const groups = new Map(declared.map((name) => [name, []]));
    for (const [path, item] of Object.entries(spec.paths ?? {})) {
      for (const method of METHODS) {
        const operation = item[method];
        if (!operation) continue;
        const tag = operation.tags?.[0] ?? 'Other';
        if (!groups.has(tag)) groups.set(tag, []);
        const entry = { method, path, operation, tag, id: `op-${operation.operationId}`, auth: authMode(operation) };
        entry.text = [method, path, operation.summary, operation.operationId, tag].join(' ').toLowerCase();
        groups.get(tag).push(entry);
        operations.push(entry);
      }
    }
    total = operations.length;
    const tagInfo = new Map((spec.tags ?? []).map((tag) => [tag.name, tag]));
    const tocFragment = document.createDocumentFragment();
    const mainFragment = document.createDocumentFragment();
    for (const [name, entries] of groups) {
      if (!entries.length) continue;
      const id = `tag-${slug(name)}`;
      const section = el('section', 'api-tag');
      section.id = id;
      section.append(el('h2', 'api-tag-title', name));
      if (tagInfo.get(name)?.description) section.append(el('p', 'api-tag-desc', tagInfo.get(name).description));
      const group = el('div', 'api-toc-group');
      const tagLink = el('a', 'api-toc-tag');
      tagLink.href = `#${id}`;
      tagLink.append(el('span', null, name), el('span', 'api-toc-count', entries.length));
      const list = el('ul', 'api-toc-list');
      group.append(tagLink, list);
      for (const entry of entries) {
        section.append(renderOperation(entry));
        const link = el('a', 'api-toc-op');
        link.href = `#${entry.id}`;
        link.title = `${entry.method.toUpperCase()} ${entry.path}`;
        link.append(methodBadge(entry.method), el('span', 'api-toc-path', entry.path));
        const listItem = el('li');
        listItem.append(link);
        list.append(listItem);
        entry.tocItem = listItem;
      }
      sections.push({ section, group, entries });
      tocFragment.append(group);
      mainFragment.append(section);
    }
    toc.append(tocFragment);
    container.append(mainFragment);
    if (version) version.textContent = `Version ${spec.info?.version ?? ''} · OpenAPI ${spec.openapi} · ${total} endpoints`;
  }

  function applyFilter() {
    const terms = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const entry of operations) {
      const match = terms.every((term) => entry.text.includes(term));
      entry.element.hidden = !match;
      entry.tocItem.hidden = !match;
      if (match) shown += 1;
    }
    for (const { section, group, entries } of sections) {
      const empty = entries.every((entry) => entry.element.hidden);
      section.hidden = empty;
      group.hidden = empty;
    }
    count.textContent = `${shown} of ${total} endpoints · API ${spec.info?.version ?? ''}`;
  }

  /** Show, open, and scroll to the hash target, clearing a search that hides it. */
  function reveal(hash, scroll = true) {
    if (!hash || hash.length < 2) return;
    const target = document.getElementById(decodeURIComponent(hash.slice(1)));
    if (!target || !container.contains(target)) return;
    clearTimeout(timer);
    applyFilter();
    if (target.hidden || target.closest('[hidden]')) {
      search.value = '';
      applyFilter();
    }
    if (target.tagName === 'DETAILS') target.open = true;
    if (scroll) target.scrollIntoView({ block: 'start' });
  }

  let timer = 0;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(applyFilter, 60);
  });
  nav?.addEventListener('click', (event) => {
    const link = event.target.closest('a[href^="#"]');
    if (!link) return;
    reveal(link.getAttribute('href'), false);
    if (tocToggle && nav) { tocToggle.setAttribute('aria-expanded', 'false'); nav.dataset.open = 'false'; }
  });
  tocToggle?.addEventListener('click', () => {
    const open = tocToggle.getAttribute('aria-expanded') !== 'true';
    tocToggle.setAttribute('aria-expanded', String(open));
    nav.dataset.open = String(open);
  });
  window.addEventListener('hashchange', () => reveal(location.hash));

  fetch('/docs/openapi.json')
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((document_) => {
      spec = document_;
      const schemas = globalThis.EditarrApiSchema.create(spec);
      ({ parameterType, schemaTree } = schemas);
      ({ curlExample } = globalThis.EditarrApiExamples.create(schemas));
      render();
      applyFilter();
      reveal(location.hash);
    })
    .catch(() => {
      count.textContent = 'The API reference could not be loaded. Download the OpenAPI JSON instead.';
    });
})();
