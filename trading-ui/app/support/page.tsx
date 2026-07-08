import Link from "next/link";

export default function SupportPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16 space-y-6">
      <h1 className="text-3xl font-semibold">Support</h1>
      <p className="text-sm text-gray-600">
        For billing, access, and delivery issues, contact support with your account email and issue
        summary.
      </p>
      <div className="rounded-lg border p-4 text-sm">
        <p>Support Email: support@yourdomain.com</p>
        <p>Response Target: within 1 business day</p>
      </div>
      <div className="flex gap-3 text-sm">
        <Link href="/legal/terms" className="underline">
          Terms
        </Link>
        <Link href="/legal/privacy" className="underline">
          Privacy
        </Link>
      </div>
    </main>
  );
}
