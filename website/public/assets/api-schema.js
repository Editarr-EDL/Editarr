/** Schema helpers for the API reference: type labels, lazy property trees, and curl samples. */
(() => {
  const MAX_DEPTH = 12;

  /** Create an element with optional class names and text content. */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  /** Bind schema helpers to one OpenAPI document. */
  function create(spec) {
    function refName(ref) {
      return ref.split('/').at(-1);
    }

    function lookup(ref) {
      return spec.components?.schemas?.[refName(ref)] ?? {};
    }

    /** Split anyOf/oneOf into non-null variants and a nullable flag. */
    function variants(schema) {
      const list = schema.anyOf ?? schema.oneOf;
      if (!list) return null;
      const nonNull = list.filter((item) => item.type !== 'null');
      return { nonNull, nullable: nonNull.length !== list.length };
    }

    /** Describe a schema's type in one short label such as `string (date-time)` or `Item[]`. */
    function typeLabel(schema) {
      if (!schema || typeof schema !== 'object') return 'any';
      if (schema.$ref) return refName(schema.$ref);
      const alternatives = variants(schema);
      if (alternatives) return alternatives.nonNull.map(typeLabel).join(' | ') || 'null';
      if (schema.allOf?.length === 1) return typeLabel(schema.allOf[0]);
      if (schema.const !== undefined) return `const ${JSON.stringify(schema.const)}`;
      if (Array.isArray(schema.type)) return schema.type.join(' | ');
      if (schema.type === 'array') return `${typeLabel(schema.items)}[]`;
      if (schema.type === 'object' && schema.additionalProperties && !schema.properties) {
        return `map<string, ${schema.additionalProperties === true ? 'any' : typeLabel(schema.additionalProperties)}>`;
      }
      const base = schema.type ?? (schema.properties ? 'object' : 'any');
      return schema.format ? `${base} (${schema.format})` : base;
    }

    /** Follow refs, single variants, arrays, and maps to the object whose properties can be expanded. */
    function expandable(schema, seen) {
      let current = schema;
      const refs = [];
      for (let guard = 0; current && guard < 8; guard += 1) {
        if (current.$ref) {
          const name = refName(current.$ref);
          if (seen.has(name)) return { recursive: name };
          refs.push(name);
          current = lookup(current.$ref);
          continue;
        }
        const alternatives = variants(current);
        if (alternatives?.nonNull.length === 1) { current = alternatives.nonNull[0]; continue; }
        if (current.allOf?.length === 1) { current = current.allOf[0]; continue; }
        if (current.type === 'array' && current.items) { current = current.items; continue; }
        if (current.additionalProperties && typeof current.additionalProperties === 'object' && !current.properties) {
          current = current.additionalProperties;
          continue;
        }
        break;
      }
      if (!current?.properties || !Object.keys(current.properties).length) return null;
      return { schema: current, refs };
    }

    function enumValues(schema) {
      if (schema?.$ref) return enumValues(lookup(schema.$ref));
      if (schema?.enum) return schema.enum;
      const alternatives = variants(schema ?? {});
      if (alternatives?.nonNull.length === 1) return enumValues(alternatives.nonNull[0]);
      if (schema?.type === 'array') return enumValues(schema.items);
      return null;
    }

    function valueLine(label, values) {
      const line = el('p', 'api-enum');
      line.append(el('span', 'api-enum-label', label));
      values.forEach((value) => line.append(el('code', null, JSON.stringify(value))));
      return line;
    }

    function isNullable(schema) {
      return Boolean(variants(schema ?? {})?.nullable || (Array.isArray(schema?.type) && schema.type.includes('null')));
    }

    /** Build one property row; nested objects become a lazily filled <details>. */
    function propertyRow(name, schema, required, seen, depth) {
      const item = el('li', 'api-prop');
      const head = el('span', 'api-prop-head');
      head.append(el('code', 'api-prop-name', name), el('span', 'api-type', typeLabel(schema)));
      if (required) head.append(el('span', 'api-flag api-flag-required', 'required'));
      if (isNullable(schema)) head.append(el('span', 'api-flag', 'nullable'));
      const body = el('div', 'api-prop-body');
      const resolved = schema?.$ref ? lookup(schema.$ref) : schema;
      const description = schema?.description ?? resolved?.description;
      if (description) body.append(el('p', 'api-prop-desc', description));
      const values = enumValues(schema);
      if (values) body.append(valueLine('One of', values));
      if (schema?.default !== undefined) {
        body.append(valueLine('Default', [schema.default]));
      }
      const nested = depth < MAX_DEPTH ? expandable(schema, seen) : null;
      if (nested?.schema) {
        const details = el('details', 'api-node');
        const summary = el('summary');
        summary.append(head);
        details.append(summary, body);
        details.addEventListener('toggle', () => {
          if (!details.open || details.dataset.built) return;
          details.dataset.built = 'true';
          body.append(propertyList(nested.schema, new Set([...seen, ...nested.refs]), depth + 1));
        });
        item.append(details);
      } else {
        item.append(head);
        if (nested?.recursive) body.append(el('p', 'api-prop-desc', `↻ Recursive reference to ${nested.recursive}`));
        if (body.childNodes.length) item.append(body);
      }
      return item;
    }

    function propertyList(schema, seen, depth) {
      const list = el('ul', 'api-props');
      const required = new Set(schema.required ?? []);
      for (const [name, child] of Object.entries(schema.properties ?? {})) {
        list.append(propertyRow(name, child, required.has(name), seen, depth));
      }
      return list;
    }

    /** Render a schema root: its type name, description, and first property level. */
    function schemaTree(schema) {
      const box = el('div', 'api-schema');
      const head = el('p', 'api-schema-head');
      head.append(el('span', 'api-type api-type-root', typeLabel(schema)));
      if (isNullable(schema)) head.append(el('span', 'api-flag', 'nullable'));
      box.append(head);
      const nested = expandable(schema, new Set());
      const description = (schema.$ref ? lookup(schema.$ref) : schema).description;
      if (description) box.append(el('p', 'api-prop-desc', description));
      const values = enumValues(schema);
      if (values) box.append(valueLine('One of', values));
      if (nested?.schema) box.append(propertyList(nested.schema, new Set(nested.refs), 1));
      return box;
    }

    /** Produce a small example value from a schema for curl request bodies. */
    function sample(schema, depth = 0, seen = new Set()) {
      if (!schema || depth > 4) return null;
      if (schema.example !== undefined) return schema.example;
      if (schema.default !== undefined) return schema.default;
      if (schema.$ref) {
        const name = refName(schema.$ref);
        if (seen.has(name)) return {};
        return sample(lookup(schema.$ref), depth, new Set([...seen, name]));
      }
      const alternatives = variants(schema);
      if (alternatives) return alternatives.nonNull.length ? sample(alternatives.nonNull[0], depth, seen) : null;
      if (schema.enum) return schema.enum[0];
      if (schema.const !== undefined) return schema.const;
      if (schema.type === 'array') return [];
      if (schema.type === 'object' || schema.properties) {
        const result = {};
        for (const name of schema.required ?? []) result[name] = sample(schema.properties?.[name], depth + 1, seen);
        return result;
      }
      return { integer: 0, number: 0, boolean: false, string: 'string' }[schema.type] ?? null;
    }

    return { typeLabel, expandable, schemaTree, sample };
  }

  globalThis.EditarrApiSchema = { el, create };
})();
