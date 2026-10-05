export function Shell({ title }: { title: string }) {
  return (
    <main className="shell">
      <p className="eyebrow">OPD Saathi</p>
      <h1>{title}</h1>
      <p>Patient history-taking only. This tool does not diagnose.</p>
    </main>
  );
}
