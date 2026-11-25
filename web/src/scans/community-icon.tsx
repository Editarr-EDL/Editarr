const paths = {
  expand: "m9 18 6-6-6-6",
  download: "M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4",
  complete: "m5 12 4 4L19 6",
  sort: "m8 9 4-4 4 4m-8 6 4 4 4-4",
  ascending: "m8 9 4-4 4 4m-4-4v14",
  descending: "m8 15 4 4 4-4m-4-10v14",
};

export function CommunityIcon({ name }: { name: keyof typeof paths }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
