type DashboardModuleCardProps = {
  title: string;
  value: string;
  caption?: string;
};

export default function DashboardModuleCard({ title, value, caption }: DashboardModuleCardProps) {
  return (
    <article className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-[0_10px_26px_-16px_rgba(15,23,42,0.35)] transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_20px_32px_-20px_rgba(15,23,42,0.45)] dark:border-slate-800 dark:bg-slate-900">
      <p className="text-[11px] font-semibold uppercase tracking-[0.13em] text-slate-500 dark:text-slate-400">{title}</p>
      <p className="mt-2 text-base font-semibold tracking-tight text-slate-900 dark:text-slate-100">{value}</p>
      {caption ? <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">{caption}</p> : null}
    </article>
  );
}
