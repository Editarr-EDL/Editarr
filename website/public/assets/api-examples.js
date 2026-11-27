/** Authentication modes and curl examples for operations in the API reference. */
(() => {
  const AUTH_LABELS = {
    public: 'Public',
    key: 'API key or browser session',
    browser: 'Browser session only',
    cookie: 'Browser session cookie only',
    setup: 'First-run browser flow',
  };
  /** Why browser-only operations have no curl example, keyed by auth mode. */
  const GUIDANCE = {
    browser: 'Use a signed-in browser session; API keys receive 403 interactive_session_required.',
    cookie: 'Use a signed-in browser session; this reads only its session cookie, so a request with just an API key receives 401 authentication_required.',
    setup: 'Restore runs from the first-run setup page in a browser (Restore from backup) before an administrator exists. Each request needs a same-origin Origin or Referer, the editarr_setup_restore cookie from POST /api/setup-restore/flow, and that response\'s token in X-Setup-Restore-Token on every change.',
  };
  const BROWSER_KINDS = { interactive: 'browser', session_cookie: 'cookie', setup_restore: 'setup' };

  /**
   * Classify an operation from its OpenAPI `security` and `x-editarr-browser-session` marker:
   * public, API key allowed, or one of the browser-only modes in GUIDANCE.
   */
  function authMode(operation) {
    const browserKind = BROWSER_KINDS[operation['x-editarr-browser-session']];
    if (browserKind) return browserKind;
    const security = operation.security;
    if (Array.isArray(security) && security.length === 0) return 'public';
    if (Array.isArray(security) && !security.some((requirement) => 'ApiKey' in requirement)) return 'browser';
    return 'key';
  }

  function isJsonText(schema) {
    return /^application\/(.+\+)?json$/.test(schema?.contentMediaType ?? '');
  }

  function isBinary(schema) {
    if (!schema) return false;
    if (schema.format === 'binary') return true;
    return Boolean(schema.contentMediaType) && !isJsonText(schema) && !schema.contentMediaType.startsWith('text/');
  }

  /** Bind curl rendering to the schema helpers of one OpenAPI document. */
  function create({ expandable, resolve, sample }) {
    function quote(text) {
      return text.replace(/(["\\$`])/g, '\\$1');
    }

    /** One `-F` argument per multipart field: `@` for files, `<` for JSON text, plain text otherwise. */
    function formFields(schema, notes) {
      const object = expandable(schema, new Set())?.schema;
      const lines = [];
      for (const [name, property] of Object.entries(object?.properties ?? { file: { format: 'binary' } })) {
        const field = resolve(property);
        const item = field?.type === 'array' ? resolve(field.items) : null;
        if (isBinary(field) || isBinary(item)) {
          lines.push(`  -F "${quote(name)}=@path/to/${quote(name)}"`);
        } else if (isJsonText(field)) {
          lines.push(`  -F "${quote(name)}=<${quote(name)}.json"`);
          notes.push(`${name}.json holds the JSON text sent in the ${name} field.`);
        } else {
          const value = sample(property);
          lines.push(`  -F "${quote(name)}=${quote(typeof value === 'string' ? value : JSON.stringify(value ?? ''))}"`);
        }
      }
      return lines;
    }

    /** Build a curl example for an operation, or only guidance when API keys cannot call it. */
    function curlExample(entry) {
      if (GUIDANCE[entry.auth]) return { text: null, notes: [`No curl example. ${GUIDANCE[entry.auth]}`] };
      const { method, path, operation } = entry;
      const notes = [];
      const query = (operation.parameters ?? []).filter((parameter) => parameter.in === 'query' && parameter.required);
      const suffix = query.length ? `?${query.map((parameter) => `${parameter.name}=…`).join('&')}` : '';
      const lines = [`curl -X ${method.toUpperCase()} "$EDITARR_URL${path}${suffix}"`];
      if (entry.auth === 'key') lines.push('  -H "X-Api-Key: $EDITARR_API_KEY"');
      const content = operation.requestBody?.content ?? {};
      if (content['application/json']) {
        lines.push('  -H "Content-Type: application/json"');
        const body = JSON.stringify(sample(content['application/json'].schema) ?? {});
        lines.push(`  -d '${body.replace(/'/g, "'\\''")}'`);
      } else if (content['multipart/form-data']) {
        lines.push(...formFields(content['multipart/form-data'].schema, notes));
      }
      return { text: lines.join(' \\\n'), notes };
    }

    return { curlExample };
  }

  globalThis.EditarrApiExamples = { AUTH_LABELS, GUIDANCE, authMode, create };
})();
