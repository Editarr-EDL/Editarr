/** Schema helpers for the API reference: type labels, lazy property and union trees, and samples. */
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
      const detail = schema.format ?? schema.contentMediaType;
      return detail ? `${base} (${detail})` : base;
    }

    /** Follow refs, single non-null variants, and single allOf wrappers to the concrete schema. */
    function resolve(schema) {
      let current = schema;
      for (let guard = 0; current && guard < 8; guard += 1) {
        if (current.$ref) current = lookup(current.$ref);
        else if (variants(current)?.nonNull.length === 1) current = variants(current).nonNull[0];
        else if (current.allOf?.length === 1) current = current.allOf[0];
        else break;
      }
      return current;
    }

    /**
     * Follow refs, single variants, arrays, and maps to what can be expanded: an object's
     * properties (`schema`) or a union of several non-null alternatives (`union`).
     */
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
        if (alternatives?.nonNull.length > 1) {
          return { union: { alternatives: alternatives.nonNull, discriminator: current.discriminator }, refs };
        }
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

    /** Short parameter type: allowed values such as `admin | user` for enums, else the type label. */
    function parameterType(schema) {
      const values = enumValues(schema);
      const label = typeLabel(schema);
      if (!values) return label;
      const text = values.map((value) => (typeof value === 'string' ? value : JSON.stringify(value))).join(' | ');
      const typed = label.endsWith('[]') ? `(${text})[]` : text;
      return isNullable(schema) ? `${typed} | null` : typed;
    }

    function plural(count, unit) {
      return `${count} ${unit}${count === 1 ? '' : 's'}`;
    }

    /** `1–255 chars`, `≥ 1 item`, `≤ 100 chars`, or null when neither bound is set. */
    function sizeRange(min, max, unit) {
      if (min !== undefined && max !== undefined) {
        return min === max ? `exactly ${plural(min, unit)}` : `${min}–${max} ${unit}s`;
      }
      if (min !== undefined) return `≥ ${plural(min, unit)}`;
      return max !== undefined ? `≤ ${plural(max, unit)}` : null;
    }

    /** Numeric bounds as `1–168`, `≥ 1`, `> 0`, or `≤ 100`. */
    function numberRange(schema) {
      const { minimum, maximum, exclusiveMinimum, exclusiveMaximum } = schema;
      if (minimum !== undefined && maximum !== undefined) return [`${minimum}–${maximum}`];
      return [
        minimum !== undefined && `≥ ${minimum}`,
        exclusiveMinimum !== undefined && `> ${exclusiveMinimum}`,
        maximum !== undefined && `≤ ${maximum}`,
        exclusiveMaximum !== undefined && `< ${exclusiveMaximum}`,
      ];
    }

    /**
     * Short labels for a schema's validation keywords and default, read from the schema and
     * its single non-null variant (so `anyOf [string, null]` keeps its string limits).
     */
    function constraints(schema) {
      if (!schema || typeof schema !== 'object') return [];
      const alternatives = variants(schema);
      const value = alternatives?.nonNull.length === 1 ? { ...alternatives.nonNull[0], ...schema } : schema;
      const labels = [
        ...numberRange(value),
        sizeRange(value.minLength, value.maxLength, 'char'),
        sizeRange(value.minItems, value.maxItems, 'item'),
        value.uniqueItems && 'unique items',
        value.pattern !== undefined && `pattern: ${value.pattern}`,
        value.readOnly && 'read-only',
        value.writeOnly && 'write-only',
        value.default !== undefined && `default: ${JSON.stringify(value.default)}`,
      ];
      return labels.filter(Boolean);
    }

    /** Muted chips for `constraints(schema)`, or null when there are none. */
    function constraintChips(schema) {
      const labels = constraints(schema);
      if (!labels.length) return null;
      const group = el('span', 'api-constraints');
      labels.forEach((label) => group.append(el('span', 'api-constraint', label)));
      return group;
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
      const chips = constraintChips(schema);
      if (chips) head.append(chips);
      const body = el('div', 'api-prop-body');
      const resolved = schema?.$ref ? lookup(schema.$ref) : schema;
      const description = schema?.description ?? resolved?.description;
      if (description) body.append(el('p', 'api-prop-desc', description));
      const values = enumValues(schema);
      if (values) body.append(valueLine('One of', values));
      fillRow(item, head, body, schema, seen, depth);
      return item;
    }

    /** Attach a row's head and body, as a lazily expanded node when `schema` has more structure. */
    function fillRow(item, head, body, schema, seen, depth) {
      const nested = depth < MAX_DEPTH ? expandable(schema, seen) : null;
      if (nested?.schema || nested?.union) {
        item.append(lazyNode(head, body, () => nestedContent(nested, seen, depth + 1)));
        return;
      }
      item.append(head);
      if (nested?.recursive) body.append(el('p', 'api-prop-desc', `↻ Recursive reference to ${nested.recursive}`));
      if (body.childNodes.length) item.append(body);
    }

    /** A <details> whose extra content is built the first time it opens. */
    function lazyNode(head, body, build) {
      const details = el('details', 'api-node');
      const summary = el('summary');
      summary.append(head);
      details.append(summary, body);
      details.addEventListener('toggle', () => {
        if (!details.open || details.dataset.built) return;
        details.dataset.built = 'true';
        body.append(build());
      });
      return details;
    }

    /** Render what `expandable` found: a property list or a union's branches. */
    function nestedContent(nested, seen, depth) {
      const scope = new Set([...seen, ...nested.refs]);
      return nested.schema ? propertyList(nested.schema, scope, depth) : unionList(nested.union, scope, depth);
    }

    /** The discriminator value selecting `branch`, from the mapping or the branch's own const/enum. */
    function discriminatorValue(branch, discriminator) {
      if (!discriminator?.propertyName) return undefined;
      const mapped = Object.entries(discriminator.mapping ?? {}).find(([, ref]) => ref === branch.$ref);
      if (mapped) return mapped[0];
      const property = resolve(branch)?.properties?.[discriminator.propertyName];
      if (property?.const !== undefined) return property.const;
      return property?.enum?.length === 1 ? property.enum[0] : undefined;
    }

    /** One expandable row per alternative of a oneOf/anyOf, tagged with its discriminator value. */
    function unionList(union, seen, depth) {
      const list = el('ul', 'api-props api-union');
      const { alternatives, discriminator } = union;
      const intro = el('li', 'api-union-intro');
      const chosen = Boolean(discriminator?.propertyName);
      intro.append(el('span', 'api-enum-label', chosen ? 'One of these shapes, chosen by' : 'One of these shapes'));
      if (chosen) intro.append(el('code', 'api-prop-name', discriminator.propertyName));
      list.append(intro);
      alternatives.forEach((branch, index) => {
        const item = el('li', 'api-prop');
        const head = el('span', 'api-prop-head');
        head.append(el('span', 'api-flag', `Option ${index + 1}`), el('span', 'api-type', typeLabel(branch)));
        const value = discriminatorValue(branch, discriminator);
        if (value !== undefined) {
          head.append(el('code', 'api-flag api-flag-discriminator', `${discriminator.propertyName}: ${JSON.stringify(value)}`));
        }
        const body = el('div', 'api-prop-body');
        const description = branch.description ?? resolve(branch)?.description;
        if (description) body.append(el('p', 'api-prop-desc', description));
        fillRow(item, head, body, branch, seen, depth);
        list.append(item);
      });
      return list;
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
      const chips = constraintChips(schema);
      if (chips) head.append(chips);
      box.append(head);
      const nested = expandable(schema, new Set());
      const description = (schema.$ref ? lookup(schema.$ref) : schema).description;
      if (description) box.append(el('p', 'api-prop-desc', description));
      const values = enumValues(schema);
      if (values) box.append(valueLine('One of', values));
      if (nested?.schema || nested?.union) box.append(nestedContent(nested, new Set(), 1));
      return box;
    }

    /** A number within the schema's minimum/maximum bounds, as close to 0 as they allow. */
    function numberSample(schema) {
      const integer = schema.type === 'integer';
      let value = 0;
      if (schema.minimum !== undefined && value < schema.minimum) value = schema.minimum;
      if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) value = schema.exclusiveMinimum + 1;
      if (schema.maximum !== undefined && value > schema.maximum) value = schema.maximum;
      if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) {
        const low = schema.exclusiveMinimum ?? schema.minimum ?? schema.exclusiveMaximum - 2;
        value = integer ? schema.exclusiveMaximum - 1 : (low + schema.exclusiveMaximum) / 2;
      }
      return integer ? Math.ceil(value) : value;
    }

    function stringSample(schema) {
      const text = 'string'.padEnd(schema.minLength ?? 0, 'x');
      return schema.maxLength !== undefined ? text.slice(0, schema.maxLength) : text;
    }

    /** Produce a small example value from a schema for curl request bodies, within its bounds. */
    function sample(schema, depth = 0, seen = new Set()) {
      if (!schema || depth > 4) return null;
      if (schema.example !== undefined) return schema.example;
      if (Array.isArray(schema.examples) && schema.examples.length) return schema.examples[0];
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
      if (schema.type === 'array') {
        const length = Math.min(schema.minItems ?? 0, schema.maxItems ?? Infinity, 10);
        return Array.from({ length }, () => sample(schema.items, depth + 1, seen));
      }
      if (schema.type === 'object' || schema.properties) {
        const result = {};
        for (const name of schema.required ?? []) result[name] = sample(schema.properties?.[name], depth + 1, seen);
        return result;
      }
      if (schema.type === 'integer' || schema.type === 'number') return numberSample(schema);
      if (schema.type === 'string') return stringSample(schema);
      return schema.type === 'boolean' ? false : null;
    }

    return { typeLabel, parameterType, constraintChips, resolve, expandable, schemaTree, sample };
  }

  globalThis.EditarrApiSchema = { el, create };
})();
