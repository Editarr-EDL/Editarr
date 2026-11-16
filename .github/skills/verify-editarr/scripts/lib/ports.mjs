import net from "node:net";

export function parsePortRange(value) {
  if (value === undefined) return undefined;
  const match = /^(\d+)-(\d+)$/.exec(value);
  if (!match) throw new Error("Expected --ports=A-B");
  const [first, last] = match.slice(1).map(Number);
  if (first < 1024 || last > 65535 || last - first < 2) throw new Error("Port range must contain at least three unprivileged ports");
  return [first, last];
}

export function createPortPool(range) {
  const used = new Set();
  return {
    async next() {
      for (let candidate = range?.[0] ?? 0; candidate <= (range?.[1] ?? 0); candidate++) {
        if (used.has(candidate)) continue;
        const listener = net.createServer();
        const port = await new Promise((resolve, reject) => {
          listener.once("error", error => error.code === "EADDRINUSE" ? resolve(null) : reject(error));
          listener.listen(candidate, "127.0.0.1", () => {
            const selected = listener.address().port;
            listener.close(error => error ? reject(error) : resolve(selected));
          });
        });
        if (port !== null && !used.has(port)) { used.add(port); return port; }
      }
      throw new Error("No unused port remains in the requested range");
    },
  };
}