import { createContext, useContext, useState, type ReactNode } from "react";

interface ReceiptStore {
  values: ReadonlyMap<string, unknown>;
  save: (name: string, value: unknown) => void;
}

const ReceiptContext = createContext<ReceiptStore | null>(null);

/** Retain completed operation receipts for one signed-in identity, never drafts or credentials. */
export function OperationReceipts({ children }: { children: ReactNode }) {
  const [values, setValues] = useState<ReadonlyMap<string, unknown>>(() => new Map());
  const store: ReceiptStore = {
    values,
    save: (name, value) => setValues(previous => new Map(previous).set(name, value)),
  };
  return <ReceiptContext.Provider value={store}>{children}</ReceiptContext.Provider>;
}

/** Read and replace a completed receipt without retaining permission-scoped form state. */
export function useOperationReceipt<Receipt>(name: string): [Receipt | null, (receipt: Receipt) => void] {
  const store = useContext(ReceiptContext);
  const [local, setLocal] = useState<Receipt | null>(null);
  return store
    ? [store.values.get(name) as Receipt | undefined ?? null, receipt => store.save(name, receipt)]
    : [local, setLocal];
}