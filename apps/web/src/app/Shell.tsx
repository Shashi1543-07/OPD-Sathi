type ShellProps = {
  title: string;
  layout: "kiosk" | "staff" | "status" | "gallery";
};

export function Shell({ title, layout }: ShellProps) {
  return (
    <main className="shell" data-layout={layout}>
      <p className="eyebrow">OPD Saathi</p>
      <h1>{title}</h1>
      <p>Patient history-taking only. This tool does not diagnose.</p>
    </main>
  );
}
