/** Render trusted local OpenAPI data as text, never executable markup. */
async function loadReference() {
  const container = document.querySelector('#api-operations');
  const count = document.querySelector('#api-count');
  const search = document.querySelector('#api-search');
  if (!container || !count || !search) return;
  try {
    const response = await fetch('/docs/openapi.json');
    if (!response.ok) throw new Error('Reference unavailable');
    const document = await response.json();
    const entries = [];
    function referencedSchemas(value, seen = new Set()) {
      if (!value || typeof value !== 'object') return {};
      const result = {};
      for (const [key, child] of Object.entries(value)) {
        if (key === '$ref' && typeof child === 'string' && child.startsWith('#/components/schemas/')) {
          const name = child.split('/').at(-1);
          if (!seen.has(name)) {
            seen.add(name);
            result[name] = document.components.schemas[name];
            Object.assign(result, referencedSchemas(result[name], seen));
          }
        } else if (child && typeof child === 'object') Object.assign(result, referencedSchemas(child, seen));
      }
      return result;
    }
    for (const [path, operations] of Object.entries(document.paths)) {
      for (const [method, operation] of Object.entries(operations)) {
        if (!['get', 'post', 'put', 'patch', 'delete', 'head', 'options'].includes(method)) continue;
        const details = window.document.createElement('details');
        details.className = 'api-operation';
        const summary = window.document.createElement('summary');
        const verb = window.document.createElement('span');
        verb.textContent = method.toUpperCase();
        summary.append(verb, window.document.createTextNode(path));
        details.append(summary);
        details.addEventListener('toggle', () => {
          if (!details.open || details.dataset.loaded) return;
          const description = window.document.createElement('p');
          description.textContent = operation.description || operation.summary || '';
          const pre = window.document.createElement('pre');
          pre.tabIndex = 0;
          const code = window.document.createElement('code');
          code.textContent = JSON.stringify({ parameters: operation.parameters || [], requestBody: operation.requestBody,
            responses: operation.responses, schemas: referencedSchemas(operation) }, null, 2);
          pre.append(code); details.append(description, pre); details.dataset.loaded = 'true';
        });
        container.append(details);
        entries.push({ details, search: `${method} ${path} ${operation.summary || ''} ${(operation.tags || []).join(' ')}`.toLowerCase() });
      }
    }
    function filter() {
      const term = search.value.toLowerCase().trim(); let visible = 0;
      for (const entry of entries) { entry.details.hidden = !entry.search.includes(term); if (!entry.details.hidden) visible++; }
      count.textContent = `${visible} of ${entries.length} endpoints · API ${document.info.version}`;
    }
    search.addEventListener('input', filter); filter();
  } catch {
    count.textContent = 'Unable to load the reference. Try reloading, or use your installation’s /openapi.json.';
  }
}
void loadReference();
