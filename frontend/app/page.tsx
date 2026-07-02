import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-semibold">Polaris AI</h1>
      <p className="mt-2 text-gray-600 dark:text-gray-300">
        Your AI real-estate agent &amp; copilot. Intake a listing, value it against real
        King County comps, and set your agent&apos;s mandate — all from chat.
      </p>
      <div className="mt-6 flex gap-4">
        <Link
          href="/login"
          className="rounded bg-black px-4 py-2 text-white dark:bg-white dark:text-black"
        >
          Log in
        </Link>
        <Link href="/copilot" className="rounded border border-gray-400 px-4 py-2">
          Open copilot
        </Link>
        <Link href="/inbox" className="rounded border border-gray-400 px-4 py-2">
          Open inbox
        </Link>
        <Link href="/spike" className="rounded border border-gray-300 px-4 py-2 text-sm text-gray-500">
          P0 spike
        </Link>
      </div>
    </main>
  );
}
