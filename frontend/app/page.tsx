import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-semibold">Polaris AI — Phase 0 spike</h1>
      <p className="mt-2 text-gray-600 dark:text-gray-300">
        Proves the review-#8 integration seam: session-cookie auth → authenticated
        WebSocket → async LangGraph over a shared Postgres checkpointer →
        GeoDjango <code>ST_DWithin</code> → Inngest round-trip.
      </p>
      <div className="mt-6 flex gap-4">
        <Link
          href="/login"
          className="rounded bg-black px-4 py-2 text-white dark:bg-white dark:text-black"
        >
          Log in
        </Link>
        <Link
          href="/spike"
          className="rounded border border-gray-400 px-4 py-2"
        >
          Spike page
        </Link>
      </div>
    </main>
  );
}
