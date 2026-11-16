export interface SqlResult<Row = Record<string, unknown>> {
  success: boolean;
  results: Row[];
  meta: {
    changes: number;
    duration: number;
    rows_read: number | null;
    rows_written: number | null;
  };
}

export interface SqlStatement {
  bind(...values: unknown[]): SqlStatement;
  all<Row = Record<string, unknown>>(): Promise<SqlResult<Row>>;
  run<Row = Record<string, unknown>>(): Promise<SqlResult<Row>>;
  first<Row = Record<string, unknown>>(): Promise<Row | null>;
  first<Value = unknown>(column: string): Promise<Value | null>;
}

export interface SqlDatabase {
  prepare(sql: string): SqlStatement;
  batch<Row = Record<string, unknown>>(statements: SqlStatement[]): Promise<SqlResult<Row>[]>;
}

export interface AssetFetcher {
  fetch(request: Request): Promise<Response>;
}
