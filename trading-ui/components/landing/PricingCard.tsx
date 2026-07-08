import Link from "next/link";

type PricingCardProps = {
  name: "Basic" | "Pro" | "Elite";
  summary: string;
  priceText: string;
  features: string[];
  ctaLabel: string;
  ctaHref: string;
  highlighted?: boolean;
};

export default function PricingCard({
  name,
  summary,
  priceText,
  features,
  ctaLabel,
  ctaHref,
  highlighted = false,
}: PricingCardProps) {
  const wrapper = highlighted
    ? "rounded-2xl border border-emerald-300/80 bg-gradient-to-b from-emerald-50 to-white p-6 shadow-[0_24px_42px_-24px_rgba(5,150,105,0.55)] transition duration-300 hover:-translate-y-1 dark:border-emerald-700 dark:from-emerald-900/25 dark:to-slate-900"
    : "rounded-2xl border border-slate-200/90 bg-white p-6 shadow-[0_12px_30px_-20px_rgba(15,23,42,0.35)] transition duration-300 hover:-translate-y-1 hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700";

  return (
    <article className={wrapper}>
      {highlighted ? (
        <p className="mb-3 inline-block rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">
          Most Advanced
        </p>
      ) : null}
      <h3 className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">{name}</h3>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{summary}</p>
      <p className="mt-6 text-3xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">{priceText}</p>
      <ul className="mt-5 space-y-2.5 text-sm text-slate-700 dark:text-slate-200">
        {features.map((feature) => (
          <li key={feature} className="flex items-start gap-2">
            <span className="mt-[1px] text-emerald-600 dark:text-emerald-400">&bull;</span>
            <span>{feature}</span>
          </li>
        ))}
      </ul>
      <Link
        href={ctaHref}
        className="mt-7 inline-flex w-full items-center justify-center rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
      >
        {ctaLabel}
      </Link>
    </article>
  );
}
