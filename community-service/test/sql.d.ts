declare module '*.sql?raw' {
  const sql: string;
  export default sql;
}

declare module '*.jsonc?raw' {
  const configuration: string;
  export default configuration;
}