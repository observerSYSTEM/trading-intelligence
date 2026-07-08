import Link from "next/link";

import DashboardModuleCard from "@/components/landing/DashboardModuleCard";
import FeatureCard from "@/components/landing/FeatureCard";
import PricingCard from "@/components/landing/PricingCard";
import RevealOnScroll from "@/components/landing/RevealOnScroll";
import SectionHeading from "@/components/landing/SectionHeading";

const navItems = [
  { href: "#what-it-does", label: "Product" },
  { href: "#dashboard-preview", label: "Preview" },
  { href: "#pricing", label: "Pricing" },
  { href: "#faq", label: "FAQ" },
];

const featureCards = [
  {
    icon: "bias" as const,
    title: "Daily Bias Snapshot",
    description: "Start each trading day with clear directional context for XAUUSD.",
  },
  {
    icon: "target" as const,
    title: "Liquidity Magnet Targets",
    description: "Track magnet levels and target zones with a structured, practical view.",
  },
  {
    icon: "telegram" as const,
    title: "Telegram Alerts",
    description: "Receive timely intelligence updates directly in Telegram when connected.",
  },
  {
    icon: "elite" as const,
    title: "Elite Dashboard Intelligence",
    description: "Add risk and news context for deeper decision support when needed.",
  },
  {
    icon: "plan" as const,
    title: "Subscription Access",
    description: "Basic, Pro, and Elite plans designed for different trading workflows.",
  },
  {
    icon: "workflow" as const,
    title: "Fast Trader Workflow",
    description: "Move from bias to execution planning in one clean dashboard process.",
  },
];

const audience = [
  {
    icon: "gold" as const,
    label: "Gold traders",
    detail: "Focused on XAUUSD workflow clarity from the start.",
  },
  {
    icon: "intraday" as const,
    label: "Intraday traders",
    detail: "Built for session-based decision support and level tracking.",
  },
  {
    icon: "structure" as const,
    label: "Structure-first traders",
    detail: "Bias and context before execution, not random entries.",
  },
  {
    icon: "notifications" as const,
    label: "Telegram + dashboard users",
    detail: "One connected workflow across interface and notifications.",
  },
];

const dashboardModules = [
  { title: "Current Plan", value: "Elite", caption: "Subscription status and plan view" },
  { title: "Signals Remaining", value: "992 / 1000", caption: "Monthly usage visibility" },
  { title: "XAUUSD Daily Bias Snapshot", value: "SELL_ONLY", caption: "Main operational direction" },
  { title: "Pro Targets", value: "Liquidity magnet active", caption: "Target and level context" },
  { title: "Elite Risk & News", value: "Risk Gate: PASS", caption: "Additional risk context" },
  { title: "Telegram Settings", value: "Connected", caption: "Notification routing enabled" },
  { title: "Billing Settings", value: "Stripe portal ready", caption: "Plan and billing management" },
];

const faqItems = [
  {
    q: "What is Trading Intelligence SaaS?",
    a: "Trading Intelligence SaaS is a subscription platform that provides structured XAUUSD market intelligence, including bias snapshots, liquidity targets, and actionable dashboard visibility.",
  },
  {
    q: "Does this place trades automatically?",
    a: "No. The platform delivers intelligence and workflow support. Trade execution remains user-controlled.",
  },
  {
    q: "Which asset is supported?",
    a: "Launch support is focused on XAUUSD (Gold), with architecture designed to expand to additional symbols.",
  },
  {
    q: "How are plans managed?",
    a: "Plans are managed through subscription billing with Stripe-backed billing workflows.",
  },
  {
    q: "Do I receive Telegram notifications?",
    a: "Yes. Telegram notifications are supported for connected accounts.",
  },
  {
    q: "Can I upgrade later?",
    a: "Yes. You can move between Basic, Pro, and Elite plans at any time.",
  },
];

export default function HomePage() {
  return (
    <main className="page-shell min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/75 dark:border-slate-800 dark:bg-slate-950/75">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-6">
          <Link href="/" className="text-sm font-semibold tracking-[0.04em] text-slate-900 dark:text-slate-100">
            Trading Intelligence SaaS
          </Link>

          <nav className="hidden items-center gap-6 md:flex">
            {navItems.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="text-sm text-slate-600 transition hover:text-slate-900 dark:text-slate-300 dark:hover:text-slate-100"
              >
                {item.label}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <Link
              href="/login"
              className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              Login
            </Link>
            <Link
              href="/pricing"
              className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-medium text-white transition hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
            >
              Get Started
            </Link>
          </div>
        </div>
      </header>

      <section id="home" className="section-shell mx-auto max-w-6xl scroll-mt-24 px-6 pb-10 pt-14 sm:pt-18">
        <div className="grid items-start gap-10 lg:grid-cols-[1.05fr_0.95fr]">
          <div>
            <p className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.14em] text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
              XAUUSD-first at launch
            </p>
            <h1 className="mt-6 text-4xl font-semibold leading-tight tracking-tight text-slate-900 sm:text-5xl lg:text-6xl dark:text-slate-100">
              Trade with structured market intelligence, not guesswork.
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg dark:text-slate-300">
              Get daily bias snapshots, liquidity magnet targets, and clear dashboard visibility. Built for XAUUSD
              first, designed to expand to more symbols over time.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/pricing"
                className="rounded-lg bg-slate-900 px-5 py-3 text-sm font-medium text-white shadow-[0_14px_26px_-16px_rgba(15,23,42,0.75)] transition hover:-translate-y-0.5 hover:bg-slate-800 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
              >
                Get Started
              </Link>
              <Link
                href="/pricing"
                className="rounded-lg border border-slate-300 bg-white px-5 py-3 text-sm font-medium text-slate-800 transition hover:-translate-y-0.5 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
              >
                View Pricing
              </Link>
            </div>
            <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">
              Paid subscription SaaS for traders who want clean structure and execution discipline.
            </p>
          </div>

          <div className="rounded-2xl border border-slate-200/90 bg-white/95 p-6 shadow-[0_28px_44px_-24px_rgba(15,23,42,0.38)] dark:border-slate-800 dark:bg-slate-900/90">
            <div className="mb-5 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">
                Live Product Style
              </p>
              <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                Dashboard Preview
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {dashboardModules.slice(0, 6).map((module) => (
                <DashboardModuleCard
                  key={module.title}
                  title={module.title}
                  value={module.value}
                  caption={module.caption}
                />
              ))}
            </div>
          </div>
        </div>
      </section>

      <RevealOnScroll as="section" className="section-shell mx-auto max-w-6xl px-6 py-7" delayMs={50}>
        <div className="grid gap-3 rounded-2xl border border-slate-200/90 bg-white/90 p-4 text-sm text-slate-600 shadow-[0_16px_30px_-22px_rgba(15,23,42,0.45)] sm:grid-cols-3 sm:p-5 dark:border-slate-800 dark:bg-slate-900/80 dark:text-slate-300">
          <p className="font-medium text-slate-700 dark:text-slate-200">Daily bias + target workflow in one platform</p>
          <p>Built for XAUUSD first with expansion-ready architecture</p>
          <p>Dashboard + Telegram delivery with subscription management</p>
        </div>
      </RevealOnScroll>

      <RevealOnScroll
        as="section"
        id="what-it-does"
        className="section-shell mx-auto max-w-6xl scroll-mt-24 px-6 py-16"
        delayMs={60}
      >
        <SectionHeading
          eyebrow="What It Does"
          title="A premium intelligence stack for active traders"
          description="Clear, actionable context for XAUUSD across bias, targets, risk layers, and delivery workflow."
        />
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {featureCards.map((feature) => (
            <FeatureCard key={feature.title} icon={feature.icon} title={feature.title} description={feature.description} />
          ))}
        </div>
      </RevealOnScroll>

      <RevealOnScroll as="section" className="section-shell mx-auto max-w-6xl px-6 py-16" delayMs={80}>
        <SectionHeading
          eyebrow="Who It Is For"
          title="Designed for traders who need structure before execution"
          description="Built for traders who prefer directional clarity, clean workflow visibility, and consistent process."
        />
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {audience.map((item) => (
            <FeatureCard key={item.label} icon={item.icon} title={item.label} description={item.detail} />
          ))}
        </div>
      </RevealOnScroll>

      <RevealOnScroll as="section" className="section-shell mx-auto max-w-6xl px-6 py-16" delayMs={100}>
        <SectionHeading eyebrow="How It Works" title="Simple, structured, and subscription-ready" align="center" />
        <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { step: "01", title: "Sign up", detail: "Create your account and access the platform." },
            { step: "02", title: "Choose a plan", detail: "Select Basic, Pro, or Elite subscription access." },
            { step: "03", title: "Open dashboard", detail: "Use modules for bias, targets, and intelligence context." },
            {
              step: "04",
              title: "Receive updates",
              detail: "Track intelligence on dashboard and Telegram, and manage billing.",
            },
          ].map((item) => (
            <li
              key={item.step}
              className="rounded-2xl border border-slate-200/90 bg-white p-5 shadow-[0_10px_24px_-18px_rgba(15,23,42,0.4)] dark:border-slate-800 dark:bg-slate-900"
            >
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-emerald-700 dark:text-emerald-400">
                {item.step}
              </p>
              <h3 className="mt-2 text-base font-semibold text-slate-900 dark:text-slate-100">{item.title}</h3>
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{item.detail}</p>
            </li>
          ))}
        </ol>
      </RevealOnScroll>

      <RevealOnScroll
        as="section"
        id="dashboard-preview"
        className="section-shell mx-auto max-w-6xl scroll-mt-24 px-6 py-16"
        delayMs={120}
      >
        <SectionHeading
          eyebrow="Dashboard Preview"
          title="A public preview that matches the actual product modules"
          description="These cards mirror the same module structure used inside the product dashboard."
        />
        <div className="mt-8 rounded-2xl border border-slate-200/90 bg-white p-5 shadow-[0_24px_46px_-28px_rgba(15,23,42,0.5)] sm:p-7 dark:border-slate-800 dark:bg-slate-900">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {dashboardModules.map((module) => (
              <DashboardModuleCard
                key={module.title}
                title={module.title}
                value={module.value}
                caption={module.caption}
              />
            ))}
          </div>
        </div>
      </RevealOnScroll>

      <RevealOnScroll
        as="section"
        id="pricing"
        className="section-shell mx-auto max-w-6xl scroll-mt-24 px-6 py-16"
        delayMs={140}
      >
        <SectionHeading
          eyebrow="Pricing Preview"
          title="Subscription plans for different trader depth"
          description="Choose the intelligence layer that matches your workflow: Basic, Pro, or Elite."
        />
        <div className="mt-8 grid gap-4 lg:grid-cols-3">
          <PricingCard
            name="Basic"
            summary="Core directional workflow for disciplined daily use."
            priceText="From $-- / month"
            features={["Daily bias snapshot", "Core dashboard visibility", "Telegram-connected workflow"]}
            ctaLabel="Get Basic"
            ctaHref="/pricing"
          />
          <PricingCard
            name="Pro"
            summary="Adds intraday target context with liquidity magnet support."
            priceText="From $-- / month"
            features={["Everything in Basic", "Pro Targets module", "Liquidity level context"]}
            ctaLabel="Choose Pro"
            ctaHref="/pricing"
          />
          <PricingCard
            name="Elite"
            summary="Most advanced layer for full risk and intelligence visibility."
            priceText="From $-- / month"
            features={["Everything in Pro", "Elite Risk & News", "Higher-confluence intelligence view"]}
            ctaLabel="Go Elite"
            ctaHref="/pricing"
            highlighted
          />
        </div>
        <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">Manage billing and plan changes with Stripe.</p>
      </RevealOnScroll>

      <RevealOnScroll as="section" id="faq" className="section-shell mx-auto max-w-6xl scroll-mt-24 px-6 py-16" delayMs={160}>
        <SectionHeading eyebrow="FAQ" title="Common questions before subscribing" />
        <div className="mt-8 space-y-3">
          {faqItems.map((item) => (
            <details
              key={item.q}
              className="group rounded-xl border border-slate-200/90 bg-white p-4 shadow-[0_10px_24px_-18px_rgba(15,23,42,0.4)] transition dark:border-slate-800 dark:bg-slate-900"
            >
              <summary className="cursor-pointer list-none pr-6 text-sm font-semibold text-slate-900 marker:content-none dark:text-slate-100">
                {item.q}
              </summary>
              <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">{item.a}</p>
            </details>
          ))}
        </div>
      </RevealOnScroll>

      <RevealOnScroll as="section" className="section-shell mx-auto max-w-6xl px-6 py-16" delayMs={180}>
        <div className="rounded-2xl border border-slate-200 bg-slate-900 p-8 text-white shadow-[0_28px_52px_-30px_rgba(15,23,42,0.9)] sm:p-10 dark:border-slate-700 dark:bg-slate-100 dark:text-slate-900">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            Subscribe to structured XAUUSD intelligence.
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300 sm:text-base dark:text-slate-700">
            Move from random decision-making to a cleaner operational workflow with bias, targets, and dashboard clarity.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              href="/pricing"
              className="rounded-lg bg-white px-5 py-3 text-sm font-medium text-slate-900 transition hover:bg-slate-200 dark:bg-slate-900 dark:text-white dark:hover:bg-slate-700"
            >
              Get Started
            </Link>
            <Link
              href="/pricing"
              className="rounded-lg border border-slate-500 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 dark:border-slate-500 dark:text-slate-900 dark:hover:bg-slate-200"
            >
              View Pricing
            </Link>
          </div>
        </div>
      </RevealOnScroll>

      <footer className="section-shell border-t border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto grid max-w-6xl gap-8 px-6 py-10 sm:grid-cols-2 lg:grid-cols-[1.6fr_1fr]">
          <div>
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">Trading Intelligence SaaS</p>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              Premium XAUUSD intelligence workflow for serious traders.
            </p>
            <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
              Educational and analytical tool only. Not financial advice.
            </p>
          </div>
          <nav className="grid grid-cols-2 gap-2 text-sm text-slate-600 dark:text-slate-300">
            <Link href="/pricing" className="hover:text-slate-900 dark:hover:text-slate-100">
              Pricing
            </Link>
            <Link href="/login" className="hover:text-slate-900 dark:hover:text-slate-100">
              Login
            </Link>
            <Link href="/pricing" className="hover:text-slate-900 dark:hover:text-slate-100">
              Get Started
            </Link>
            <Link href="/dashboard" className="hover:text-slate-900 dark:hover:text-slate-100">
              Dashboard
            </Link>
            <Link href="/settings/telegram" className="hover:text-slate-900 dark:hover:text-slate-100">
              Telegram Settings
            </Link>
            <Link href="/settings/billing" className="hover:text-slate-900 dark:hover:text-slate-100">
              Billing
            </Link>
          </nav>
        </div>
        <div className="border-t border-slate-200 py-4 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          &copy; {new Date().getFullYear()} Trading Intelligence SaaS. All rights reserved.
        </div>
      </footer>
    </main>
  );
}
