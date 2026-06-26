import { Link, createFileRoute } from "@tanstack/react-router"
import {
  ArrowRight,
  CheckCircle2,
  Radio,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react"

import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({ meta: [{ title: "LedgerFlow" }] }),
})

const guarantees = [
  { icon: ShieldCheck, title: "No double-charge", body: "Every transfer carries an idempotency key — retries never move money twice." },
  { icon: Scale, title: "Always balanced", body: "Money stored as integer cents; every transaction's debits equal its credits." },
  { icon: RefreshCw, title: "Safe multi-step", body: "A transfer that fails partway rolls back cleanly (SAGA + compensation)." },
  { icon: Radio, title: "No lost events", body: "Each transfer emits an event via a transactional outbox → Redis Streams." },
  { icon: CheckCircle2, title: "Provably no drift", body: "A reconciliation check proves the whole ledger nets to exactly zero." },
]

const steps = [
  "Open the Ledger page and click “Load demo data”",
  "Watch the wallets, balances and activity feed fill in",
  "Set up a recurring payment — money then moves on its own",
  "Open Events to watch each event go pending → published",
  "Open Operations to see drift held at exactly $0.00",
]

function Dashboard() {
  const { user } = useAuth()
  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      {/* hero */}
      <div className="rounded-3xl border bg-gradient-to-br from-indigo-500/10 via-violet-500/5 to-transparent p-8 md:p-12">
        <span className="inline-flex items-center gap-1 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-400">
          ● Fintech-grade ledger
        </span>
        <h1 className="mt-4 text-4xl md:text-5xl font-bold tracking-tight">
          <span className="bg-gradient-to-r from-indigo-400 to-violet-400 bg-clip-text text-transparent">
            LedgerFlow
          </span>
        </h1>
        <p className="mt-3 max-w-2xl text-lg text-muted-foreground">
          The money engine inside a <b className="text-foreground">digital wallet</b>. Like the core of Venmo or a neobank:
          people hold balances, get paid, send money to friends, and pay subscriptions — LedgerFlow is the part that
          moves every cent <b className="text-foreground">correctly, and never loses track</b>.
        </p>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Under the hood: an immutable double-entry ledger with an event-driven core — idempotent transfers,
          SAGA compensation, a transactional outbox, and a reconciliation guarantee that the books always balance.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            to="/ledger"
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-5 py-2.5 font-medium text-white transition hover:bg-indigo-400"
          >
            Open the Ledger <ArrowRight className="h-4 w-4" />
          </Link>
          <Link
            to="/events"
            className="inline-flex items-center gap-2 rounded-xl border px-5 py-2.5 font-medium transition hover:bg-accent"
          >
            <Radio className="h-4 w-4" /> See the live event flow
          </Link>
        </div>
        {user?.email && (
          <p className="mt-4 text-sm text-muted-foreground">Signed in as {user.email}</p>
        )}
      </div>

      {/* guarantees */}
      <h2 className="mt-10 mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Correctness guarantees
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {guarantees.map((g) => (
          <div key={g.title} className="rounded-2xl border bg-card p-5 transition hover:border-indigo-500/40 hover:shadow-lg">
            <g.icon className="h-6 w-6 text-indigo-400" />
            <h3 className="mt-3 font-semibold">{g.title}</h3>
            <p className="mt-1 text-sm text-muted-foreground">{g.body}</p>
          </div>
        ))}
      </div>

      {/* how to try */}
      <div className="mt-10 grid gap-6 md:grid-cols-2">
        <div className="rounded-2xl border bg-card p-6">
          <h2 className="font-semibold">Try it in 60 seconds</h2>
          <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
            {steps.map((s, i) => (
              <li key={s} className="flex gap-3">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-500/15 text-xs font-semibold text-indigo-400">
                  {i + 1}
                </span>
                {s}
              </li>
            ))}
          </ol>
          <Link to="/ledger" className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-indigo-400 hover:underline">
            Go to the Ledger <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
        <div className="rounded-2xl border bg-card p-6">
          <h2 className="font-semibold">How it works</h2>
          <p className="mt-3 text-sm text-muted-foreground">
            Transfers are ACID and synchronous; reactions are event-driven and eventual.
          </p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground">
{`POST /transfers
  └─ ledger entries + outbox event   (one DB txn)
       └─ relay → Redis Stream
             └─ worker handles event (async)`}
          </pre>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            {["FastAPI", "PostgreSQL", "SQLModel", "Redis Streams", "Docker"].map((t) => (
              <span key={t} className="rounded-md border px-2 py-1 text-muted-foreground">{t}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
