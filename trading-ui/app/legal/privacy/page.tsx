export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16 space-y-6">
      <h1 className="text-3xl font-semibold">Privacy Policy</h1>
      <p className="text-sm text-gray-600">
        We store account, subscription, and delivery metadata required to operate the service.
      </p>
      <p className="text-sm text-gray-600">
        Telegram chat IDs are used only to deliver messages you opt into and are never exposed in
        public API responses.
      </p>
      <p className="text-sm text-gray-600">
        We do not sell personal data. Data is retained only as needed for product operations,
        billing, and auditability.
      </p>
    </main>
  );
}
