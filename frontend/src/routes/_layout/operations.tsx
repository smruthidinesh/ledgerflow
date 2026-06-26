import { createFileRoute } from "@tanstack/react-router"
import { Activity, CheckCircle2, Gauge, Landmark, Layers, Radio, Send, TrendingUp } from "lucide-react"
import type { ReactNode } from "react"
import { useEffect, useState } from "react"

export const Route = createFileRoute("/_layout/operations")({
  component: OperationsPage,
  head: () => ({ meta: [{ title: "Operations - LedgerFlow" }] }),
})

const BASE = import.meta.env.VITE_API_URL
const token = () => localStorage.getItem("access_token") || ""
const auth = { Authorization: `Bearer ${token()}` }
const dollars = (c: number) => `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`

// Prometheus exposition text -> { metric_name: number }
function parseProm(text: string): Record<string, number> {
  const out: Record<string, number> = {}
  for (const line of text.split("\n")) {
    if (!line || line.startsWith("#")) continue
    const [k, v] = line.split(/\s+/)
    if (k && v !== undefined) out[k] = Number(v)
  }
  return out
}

function OperationsPage() {
  const [m, setM] = useState<Record<string, number>>({})
  const [stream, setStream] = useState<any>(null)
  const [recon, setRecon] = useState<any>(null)

  async function refresh() {
    try {
      const txt = await fetch(`${BASE}/api/v1/ledger/metrics`).then((r) => r.text())
      setM(parseProm(txt))
      setStream(await fetch(`${BASE}/api/v1/ledger/stream-info`, { headers: auth }).then((r) => r.json()))
      setRecon(await fetch(`${BASE}/api/v1/ledger/reconciliation`, { headers: auth }).then((r) => r.json()))
    } catch {
      /* transient */
    }
  }
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [])

  const drift = m.ledger_drift_cents ?? 0
  const balanced = drift === 0

  return (
    <div>
      <div className="flex items-center gap-3">
        <Gauge className="h-7 w-7 text-indigo-400" />
        <div>
          <h1 className="text-2xl font-bold">Operations</h1>
          <p className="text-sm text-muted-foreground">
            The live health of the money-movement system — the same signals an on-call payments engineer watches.
          </p>
        </div>
      </div>

      {/* the headline invariant */}
      <div
        className={`mt-6 flex items-center gap-4 rounded-2xl border p-5 ${
          balanced ? "border-emerald-500/40 bg-emerald-500/10" : "border-red-500/40 bg-red-500/10"
        }`}
      >
        <CheckCircle2 className={`h-10 w-10 ${balanced ? "text-emerald-400" : "text-red-400"}`} />
        <div>
          <div className="text-sm uppercase tracking-wider text-muted-foreground">Ledger integrity</div>
          <div className={`text-2xl font-bold ${balanced ? "text-emerald-400" : "text-red-400"}`}>
            {balanced ? "BALANCED — zero drift" : `DRIFT DETECTED: ${drift}¢`}
          </div>
          <div className="text-sm text-muted-foreground">
            Every entry across the whole ledger sums to exactly {drift}¢. Double-entry guarantees money is never created or destroyed.
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={TrendingUp} label="Total volume settled" value={dollars(m.ledger_volume_cents_total ?? 0)} hint="lifetime credited" />
        <Tile icon={Send} label="Transactions" value={(m.ledger_transactions_total ?? 0).toLocaleString()} hint="posted, immutable" />
        <Tile icon={Landmark} label="Accounts" value={(m.ledger_accounts_total ?? 0).toLocaleString()} hint="incl. system accounts" />
        <Tile icon={Layers} label="Outbox pending" value={(m.ledger_outbox_pending ?? 0).toLocaleString()} hint="awaiting publish" />
      </div>

      <h2 className="mt-8 mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Event bus <span className="ml-1 text-xs normal-case text-emerald-400">● Redis stream</span>
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Radio} label="Events on stream" value={(stream?.length ?? 0).toLocaleString()} hint={stream?.stream || "ledger.events"} />
        <Tile icon={Activity} label="Delivered to workers" value={(stream?.delivered ?? 0).toLocaleString()} hint={stream?.group || "consumer group"} />
        <Tile icon={Layers} label="Consumer lag" value={(stream?.pending ?? 0).toLocaleString()} hint="read, not yet ACKed" />
        <Tile icon={Send} label="Active consumers" value={(stream?.consumers ?? 0).toLocaleString()} hint="worker processes" />
      </div>

      {recon && (
        <p className="mt-6 text-xs text-muted-foreground">
          Reconciliation job: {recon.balanced ? "✓ balanced" : "drift!"} · {recon.unbalanced_transactions?.length ?? 0} unbalanced transactions ·
          a background reconciler re-proves these invariants independently of the API.
        </p>
      )}
    </div>
  )
}

function Tile({ icon: Icon, label, value, hint }: { icon: any; label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-2xl border bg-card p-4">
      <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
        <Icon className="h-4 w-4 text-indigo-400" /> {label}
      </div>
      <div className="font-mono text-2xl font-bold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
    </div>
  )
}
