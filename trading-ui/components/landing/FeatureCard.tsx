import IconGlyph from "./IconGlyph";

type FeatureCardProps = {
  icon:
    | "bias"
    | "target"
    | "pro"
    | "elite"
    | "telegram"
    | "workflow"
    | "gold"
    | "intraday"
    | "structure"
    | "notifications"
    | "account"
    | "plan";
  title: string;
  description: string;
};

export default function FeatureCard({ icon, title, description }: FeatureCardProps) {
  return (
    <article className="group rounded-2xl border border-slate-200/85 bg-white/95 p-6 shadow-[0_12px_30px_-16px_rgba(15,23,42,0.32)] transition duration-300 hover:-translate-y-1 hover:border-emerald-200 hover:shadow-[0_24px_42px_-20px_rgba(15,23,42,0.4)] dark:border-slate-800 dark:bg-slate-900/90 dark:hover:border-emerald-700/70">
      <div className="mb-4 inline-flex h-11 w-11 items-center justify-center rounded-xl bg-slate-100 text-slate-700 transition-colors group-hover:bg-emerald-100 group-hover:text-emerald-700 dark:bg-slate-800 dark:text-slate-200 dark:group-hover:bg-emerald-900/40 dark:group-hover:text-emerald-300">
        <IconGlyph name={icon} className="h-5 w-5" />
      </div>
      <h3 className="text-base font-semibold tracking-tight text-slate-900 dark:text-slate-100">{title}</h3>
      <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{description}</p>
    </article>
  );
}
