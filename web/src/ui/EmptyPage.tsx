export function EmptyPage({ title, body }: { title: string; body: string }) {
  return <div className="empty-page"><h1>{title}</h1><p>{body}</p></div>;
}

